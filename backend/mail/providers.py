"""Talking to Gmail and Outlook (Microsoft Graph). Standard library only.

Every HTTP call goes through `TRANSPORT`, which tests replace with fakes of Google and Microsoft.

Fewest permissions (Shobhit, 21 Oct 2026):
- Gmail asks only to send (`gmail.send`). Gmail answers a send with the message as filed, and its
  "SENT" label is the confirmation that it is in the Sent folder.
- Outlook has no send-only way to learn what happened to a message, so it also asks to read and
  write mail (`Mail.ReadWrite`): Dealflow writes a draft, sends it, then reads it back and
  confirms it once it has left Drafts for Sent Items. Exchange sends in the background, so that
  can take a few seconds; the scheduler checks again for anything still waiting.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid


class MailError(Exception):
    """kind: "auth" (reconnect the mailbox), "limit" (the provider's sending limit: wait),
    "rejected" (this message can't be sent, e.g. a bad address), "temporary" (try again later),
    "setup" (the app registration is missing or wrong)."""

    def __init__(self, message: str, kind: str = "temporary", retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


# ---------------------------------------------------------------- HTTP


def _urllib(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise MailError(f"Couldn't reach the mail service ({getattr(e, 'reason', e)}). Try again shortly.")


TRANSPORT = _urllib
CONFIRM_WAITS = (0.5, 1.5, 3)  # seconds before each look in Outlook's Sent Items (tests set zeros)


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None):
    return TRANSPORT(method, url, headers or {}, body)


def _json(raw: bytes) -> dict:
    try:
        v = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _form(data: dict) -> bytes:
    return urllib.parse.urlencode(data).encode()


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _retry_after(headers: dict) -> int | None:
    try:
        return max(1, int(headers.get("retry-after", "")))
    except ValueError:
        return None


# ---------------------------------------------------------------- messages


@dataclass
class Outgoing:
    from_name: str
    from_email: str
    to: list[tuple[str, str]]  # (name, email)
    subject: str
    text: str
    cc: list[tuple[str, str]] = field(default_factory=list)
    bcc: list[tuple[str, str]] = field(default_factory=list)
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)  # (name, content type, data)
    unsubscribe: bool = False  # campaigns: List-Unsubscribe header where the provider allows it

    @property
    def html(self) -> str:
        return text_to_html(self.text)


@dataclass
class Sent:
    message_id: str
    thread_id: str = ""
    web_link: str = ""
    confirmed: bool = False


_URL = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)])")


def text_to_html(text: str) -> str:
    """Plain text as a simple HTML email: paragraphs, line breaks, links."""
    paras = []
    for block in re.split(r"\n\s*\n", text.strip()):
        safe = html.escape(block)
        safe = _URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', safe)
        paras.append("<p style=\"margin:0 0 12px\">" + safe.replace("\n", "<br>") + "</p>")
    return ('<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;'
            'line-height:1.5;color:#1f1f1f">' + "".join(paras) + "</div>")


def _one_line(s: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", s or "").strip()


def _addr(pairs) -> str:
    return ", ".join(formataddr((_one_line(n), e)) for n, e in pairs)


def build_mime(out: Outgoing) -> bytes:
    msg = EmailMessage(policy=SMTP)
    msg["From"] = formataddr((_one_line(out.from_name), out.from_email))
    msg["To"] = _addr(out.to)
    if out.cc:
        msg["Cc"] = _addr(out.cc)
    if out.bcc:
        msg["Bcc"] = _addr(out.bcc)  # Gmail removes it from what recipients see
    msg["Subject"] = _one_line(out.subject)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=out.from_email.rsplit("@", 1)[-1] or None)
    if out.unsubscribe:
        msg["List-Unsubscribe"] = f"<mailto:{out.from_email}?subject=unsubscribe>"
    msg.set_content(out.text)
    msg.add_alternative(out.html, subtype="html")
    for name, ctype, data in out.attachments:
        main, _, sub = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=_one_line(name))
    return msg.as_bytes()


# ---------------------------------------------------------------- providers


@dataclass
class AppConfig:
    client_id: str
    client_secret: str
    tenant: str = "common"


class Provider:
    key = ""
    label = ""
    scopes: list[str] = []

    def authorize_url(self, cfg: AppConfig, redirect_uri: str, state: str, challenge: str) -> str:
        raise NotImplementedError

    def token_url(self, cfg: AppConfig) -> str:
        raise NotImplementedError

    def _token(self, cfg: AppConfig, data: dict) -> dict:
        status, headers, raw = http("POST", self.token_url(cfg), {"Content-Type": "application/x-www-form-urlencoded",
                                                                   "Accept": "application/json"}, _form(data))
        body = _json(raw)
        if status >= 400 or "access_token" not in body:
            err = body.get("error") or f"HTTP {status}"
            if err in ("invalid_grant", "interaction_required", "consent_required"):
                raise MailError(f"{self.label} needs to be connected again.", "auth")
            if err in ("invalid_client", "unauthorized_client", "invalid_request"):
                desc = body.get("error_description") or ""
                raise MailError(f"{self.label} refused Dealflow's app details ({err}). Ask an admin to check "
                                f"Settings > Email. {desc[:200]}".strip(), "setup")
            if status >= 500:
                raise MailError(f"{self.label} is having trouble ({err}). Try again shortly.")
            raise MailError(f"{self.label} refused the sign-in ({err}).", "auth")
        return body

    def _tokens_from(self, body: dict, old: dict | None = None) -> dict:
        return {
            "access": body["access_token"],
            "refresh": body.get("refresh_token") or (old or {}).get("refresh", ""),
            "expires": time.time() + int(body.get("expires_in") or 3600) - 60,
            "scope": body.get("scope") or (old or {}).get("scope", ""),
            **{k: v for k, v in (old or {}).items() if k not in ("access", "refresh", "expires", "scope")},
        }

    def exchange(self, cfg: AppConfig, code: str, redirect_uri: str, verifier: str) -> tuple[dict, str]:
        """Finish connecting: (tokens, mailbox address)."""
        raise NotImplementedError

    def refresh(self, cfg: AppConfig, tokens: dict) -> dict:
        if not tokens.get("refresh"):
            raise MailError(f"{self.label} needs to be connected again.", "auth")
        body = self._token(cfg, {"client_id": cfg.client_id, "client_secret": cfg.client_secret,
                                 "grant_type": "refresh_token", "refresh_token": tokens["refresh"]})
        return self._tokens_from(body, tokens)

    def send(self, box: "Mailbox", out: Outgoing) -> Sent:
        raise NotImplementedError

    def check(self, box: "Mailbox", message_id: str) -> Sent | None:
        """Look again for a message sent earlier: Sent once it's confirmed, None while still waiting."""
        return None


class Gmail(Provider):
    key = "gmail"
    label = "Gmail"
    scopes = ["openid", "email", "https://www.googleapis.com/auth/gmail.send"]
    SEND_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=media"

    def authorize_url(self, cfg, redirect_uri, state, challenge):
        q = {"client_id": cfg.client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": " ".join(self.scopes), "access_type": "offline", "prompt": "consent select_account",
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)

    def token_url(self, cfg):
        return "https://oauth2.googleapis.com/token"

    def exchange(self, cfg, code, redirect_uri, verifier):
        body = self._token(cfg, {"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code,
                                 "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                                 "code_verifier": verifier})
        if "gmail.send" not in (body.get("scope") or ""):
            raise MailError("Google didn't give Dealflow permission to send email. Connect again and leave "
                            "“Send email on your behalf” ticked.", "auth")
        # The id token comes straight from Google over TLS, so its claims can be read without
        # checking the signature (Google's own guidance for this case).
        claims = {}
        try:
            part = (body.get("id_token") or "").split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (IndexError, ValueError):
            pass
        email = (claims.get("email") or "").lower()
        if not email or claims.get("email_verified") is False:
            raise MailError("Google didn't say which mailbox this is. Try connecting again.", "auth")
        return self._tokens_from(body), email

    def send(self, box, out):
        status, headers, raw = box.call("POST", self.SEND_URL, {"Content-Type": "message/rfc822"}, build_mime(out))
        body = _json(raw)
        if status >= 400:
            raise self._error(status, headers, body)
        mid = body.get("id") or ""
        return Sent(
            message_id=mid,
            thread_id=body.get("threadId") or "",
            web_link=f"https://mail.google.com/mail/?authuser={urllib.parse.quote(box.email)}#all/{mid}" if mid else "",
            confirmed="SENT" in (body.get("labelIds") or []),
        )

    def _error(self, status, headers, body) -> MailError:
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        message = (err.get("message") or "")[:200]
        reasons = {e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)}
        if status == 401 or "insufficientPermissions" in reasons or "authError" in reasons:
            return MailError("Gmail needs to be connected again.", "auth")
        if status == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded",
                                       "quotaExceeded"}:
            return MailError("Gmail's sending limit was reached.", "limit", _retry_after(headers))
        if status == 403 and "limit" in message.lower():
            return MailError("Gmail's sending limit was reached.", "limit", _retry_after(headers))
        if status in (400, 403, 413):
            return MailError(f"Gmail refused this email: {message or status}", "rejected")
        return MailError(f"Gmail is having trouble ({status}). Try again shortly.")


class Outlook(Provider):
    key = "outlook"
    label = "Outlook"
    scopes = ["offline_access", "openid", "email", "User.Read", "Mail.ReadWrite", "Mail.Send"]
    GRAPH = "https://graph.microsoft.com/v1.0"
    IMMUTABLE = {"Prefer": 'IdType="ImmutableId"'}
    INLINE_LIMIT = 3 * 1024 * 1024  # bigger attachments go up in pieces (Graph's 4 MB request limit)
    CHUNK = 320 * 1024 * 12

    def _base(self, cfg):
        tenant = re.sub(r"[^A-Za-z0-9.\-]", "", cfg.tenant or "common") or "common"
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"

    def authorize_url(self, cfg, redirect_uri, state, challenge):
        q = {"client_id": cfg.client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "response_mode": "query", "scope": " ".join(self.scopes), "state": state, "prompt": "select_account",
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{self._base(cfg)}/authorize?" + urllib.parse.urlencode(q)

    def token_url(self, cfg):
        return f"{self._base(cfg)}/token"

    def exchange(self, cfg, code, redirect_uri, verifier):
        body = self._token(cfg, {"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code,
                                 "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                                 "code_verifier": verifier, "scope": " ".join(self.scopes)})
        granted = (body.get("scope") or "").lower()
        if "mail.send" not in granted or "mail.readwrite" not in granted:
            raise MailError("Microsoft didn't give Dealflow permission to send and confirm email. Connect again "
                            "and accept the permissions asked for.", "auth")
        tokens = self._tokens_from(body)
        status, _, raw = http("GET", f"{self.GRAPH}/me?$select=mail,userPrincipalName",
                              {"Authorization": f"Bearer {tokens['access']}", "Accept": "application/json"})
        me = _json(raw)
        email = (me.get("mail") or me.get("userPrincipalName") or "").lower()
        if status >= 400 or "@" not in email:
            raise MailError("Microsoft didn't say which mailbox this is. Try connecting again.", "auth")
        return tokens, email

    def _error(self, status, headers, body, doing="send this email") -> MailError:
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = err.get("code") or ""
        message = (err.get("message") or "")[:200]
        if status == 401 or code in ("ErrorAccessDenied", "InvalidAuthenticationToken", "AuthenticationError"):
            return MailError("Outlook needs to be connected again.", "auth")
        if status == 429 or code in ("ErrorExceededMessageLimit", "ErrorSubmissionQuotaExceeded",
                                     "ApplicationThrottled", "MailboxConcurrency"):
            return MailError("Outlook's sending limit was reached.", "limit", _retry_after(headers))
        if status in (400, 403, 413) or code in ("ErrorInvalidRecipients", "ErrorMessageSizeExceeded",
                                                  "ErrorQuotaExceeded"):
            return MailError(f"Outlook refused to {doing}: {message or code or status}", "rejected")
        return MailError(f"Outlook is having trouble ({status}). Try again shortly.", "temporary",
                         _retry_after(headers))

    def _recipients(self, pairs):
        return [{"emailAddress": {"address": e, "name": _one_line(n) or e}} for n, e in pairs]

    def send(self, box, out):
        small, large, total = [], [], 0
        for name, ctype, data in out.attachments:
            if total + len(data) <= self.INLINE_LIMIT:
                total += len(data)
                small.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": _one_line(name),
                              "contentType": ctype, "contentBytes": base64.b64encode(data).decode()})
            else:
                large.append((name, ctype, data))
        draft = {"subject": _one_line(out.subject), "body": {"contentType": "HTML", "content": out.html},
                 "toRecipients": self._recipients(out.to), "ccRecipients": self._recipients(out.cc),
                 "bccRecipients": self._recipients(out.bcc), "attachments": small}
        status, headers, raw = box.call("POST", f"{self.GRAPH}/me/messages",
                                        {"Content-Type": "application/json", **self.IMMUTABLE},
                                        json.dumps(draft).encode())
        body = _json(raw)
        if status >= 400 or not body.get("id"):
            raise self._error(status, headers, body)
        mid = body["id"]
        try:
            for name, ctype, data in large:
                self._upload(box, mid, name, ctype, data)
            status, headers, raw = box.call("POST", f"{self.GRAPH}/me/messages/{urllib.parse.quote(mid)}/send",
                                            {"Content-Length": "0", **self.IMMUTABLE}, b"")
            if status >= 400:
                raise self._error(status, headers, _json(raw))
        except MailError:
            try:  # don't leave a half-made draft behind
                box.call("DELETE", f"{self.GRAPH}/me/messages/{urllib.parse.quote(mid)}", dict(self.IMMUTABLE))
            except MailError:
                pass
            raise
        result = Sent(message_id=mid, thread_id=body.get("conversationId") or "", web_link=body.get("webLink") or "")
        for wait in CONFIRM_WAITS:  # Exchange usually files it within a few seconds
            time.sleep(wait)
            found = self.check(box, mid)
            if found:
                return found
        return result

    def _upload(self, box, mid, name, ctype, data):
        status, headers, raw = box.call(
            "POST", f"{self.GRAPH}/me/messages/{urllib.parse.quote(mid)}/attachments/createUploadSession",
            {"Content-Type": "application/json", **self.IMMUTABLE},
            json.dumps({"AttachmentItem": {"attachmentType": "file", "name": _one_line(name), "size": len(data),
                                           "contentType": ctype}}).encode())
        body = _json(raw)
        url = body.get("uploadUrl")
        if status >= 400 or not url:
            raise self._error(status, headers, body, f"attach {name}")
        for start in range(0, len(data), self.CHUNK):
            piece = data[start:start + self.CHUNK]
            # The upload address carries its own authorisation; no bearer token goes to it.
            status, headers, raw = http("PUT", url, {
                "Content-Length": str(len(piece)), "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{start + len(piece) - 1}/{len(data)}"}, piece)
            if status >= 400:
                raise self._error(status, headers, _json(raw), f"attach {name}")

    def _sent_folder(self, box) -> str:
        if not box.tokens.get("sent_folder"):
            status, headers, raw = box.call("GET", f"{self.GRAPH}/me/mailFolders/sentitems?$select=id",
                                            {"Accept": "application/json"})
            body = _json(raw)
            if status >= 400 or not body.get("id"):
                raise self._error(status, headers, body, "find the Sent Items folder")
            box.remember("sent_folder", body["id"])
        return box.tokens["sent_folder"]

    def check(self, box, message_id):
        status, headers, raw = box.call(
            "GET", f"{self.GRAPH}/me/messages/{urllib.parse.quote(message_id)}"
                   "?$select=isDraft,parentFolderId,webLink,conversationId",
            {"Accept": "application/json", **self.IMMUTABLE})
        body = _json(raw)
        if status == 404:
            return None
        if status >= 400:
            raise self._error(status, headers, body, "check the Sent Items folder")
        if body.get("isDraft") is False and body.get("parentFolderId") == self._sent_folder(box):
            return Sent(message_id=message_id, thread_id=body.get("conversationId") or "",
                        web_link=body.get("webLink") or "", confirmed=True)
        return None


PROVIDERS: dict[str, Provider] = {"gmail": Gmail(), "outlook": Outlook()}


class Mailbox:
    """A connected mailbox ready to call: adds the access token, refreshes it when it runs out
    (or is refused once), and saves new tokens encrypted on the account."""

    def __init__(self, account, cfg: AppConfig, save):
        self.account = account
        self.provider = PROVIDERS[account.provider]
        self.cfg = cfg
        self.email = account.email
        self._save = save
        from .crypto import unseal

        self.tokens = unseal(account.tokens) or {}
        if not self.tokens.get("access"):
            raise MailError(f"{self.provider.label} needs to be connected again.", "auth")

    def _refresh(self):
        self.tokens = self.provider.refresh(self.cfg, self.tokens)
        self._save(self.tokens)

    def remember(self, key, value):
        self.tokens[key] = value
        self._save(self.tokens)

    def call(self, method, url, headers=None, body=None):
        if self.tokens.get("expires", 0) < time.time():
            self._refresh()
        for attempt in (1, 2):
            h = {"Authorization": f"Bearer {self.tokens['access']}", **(headers or {})}
            status, rh, raw = http(method, url, h, body)
            if status == 401 and attempt == 1:
                self._refresh()
                continue
            return status, rh, raw
        return status, rh, raw
