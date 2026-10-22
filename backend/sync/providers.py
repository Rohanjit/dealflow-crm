"""Reading (and, for calendars, writing) each kind of connected mailbox and calendar.

Every source gives the same things: new messages since last time (headers and attachment names
only), meetings in a time window, and, for the owner only, an email's text, a meeting's notes or
one attachment fetched live. Nothing here decides what gets logged; that's `sync.engine`.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import email
import email.policy
import html
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from django.utils import timezone

from mail import crypto
from mail.providers import PROVIDERS as MAIL_PROVIDERS
from mail.providers import Mailbox, MailError, _json

from . import ics, net

UTC = dt.timezone.utc
MAX_TEXT = 200_000
BATCH = 200  # messages read per mailbox folder per round


# ---------------------------------------------------------------- shapes


@dataclass
class RemoteMessage:
    remote_id: str
    message_id: str
    subject: str
    at: dt.datetime
    sender: tuple[str, str]
    to: list[tuple[str, str]] = field(default_factory=list)
    cc: list[tuple[str, str]] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)  # {"name", "size", "type"}
    extra: dict = field(default_factory=dict)


@dataclass
class RemoteEvent:
    remote_id: str
    uid: str
    start: dt.datetime
    end: dt.datetime | None
    all_day: bool = False
    summary: str = ""
    organizer: tuple[str, str] | None = None
    attendees: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def header_text(v) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(str(v)))).strip()
    except (ValueError, LookupError, UnicodeDecodeError):
        return str(v).strip()


def addresses(*values) -> list[tuple[str, str]]:
    out = []
    for name, addr in getaddresses([header_text(v) for v in values if v]):
        addr = (addr or "").strip().lower()
        if "@" in addr:
            out.append((name.strip(), addr))
    return out


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    try:
        p.feed(s or "")
        p.close()
    except Exception:  # broken markup: fall back to stripping tags
        return re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    text = "".join(p.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def message_text(msg) -> str:
    """The readable text of a parsed email (plain text preferred, HTML turned into text)."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except (KeyError, AttributeError):
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError, AttributeError):
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return (html_to_text(content) if part.get_content_type() == "text/html" else content)[:MAX_TEXT]


def message_attachments(msg) -> list:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename():
            out.append(part)
    return out


def _dt(v: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def secret(conn) -> dict:
    return crypto.unseal(conn.secret) or {}


# ---------------------------------------------------------------- OAuth sources (Google, Microsoft)


class _Account:
    """What mail.providers.Mailbox needs: provider key, address and sealed tokens."""

    def __init__(self, conn, provider):
        self.provider = provider
        self.email = conn.email
        self.tokens = conn.secret


def _mailbox(conn) -> Mailbox:
    from mail.service import app_config

    provider = "gmail" if conn.kind == "google" else "outlook"
    cfg = app_config(provider)
    if not cfg:
        raise MailError(f"{conn.get_kind_display()} isn't set up for this install. An admin can do it in "
                        "Settings > Email.", "setup")

    def save(tokens):
        conn.secret = crypto.seal(tokens)
        conn.save(update_fields=["secret"])

    return Mailbox(_Account(conn, provider), cfg, save)


class Source:
    label = ""

    def __init__(self, conn):
        self.conn = conn

    # to fill in
    def start(self):  # cursors at the moment of connecting
        pass

    def new_messages(self) -> list[RemoteMessage]:
        return []

    def events(self, start: dt.datetime, end: dt.datetime) -> list[RemoteEvent]:
        return []

    def open_message(self, copy) -> dict:
        raise MailError("This email can't be opened.")

    def attachment(self, copy, index: int) -> tuple[str, str, bytes]:
        raise MailError("This attachment can't be fetched.")

    def open_event(self, copy) -> dict:
        return {"description": "", "location": ""}

    def create_event(self, *, summary, start, end, attendees, location, description) -> RemoteEvent:
        raise MailError("This calendar can't add meetings.")


class GoogleSource(Source):
    label = "Google"
    GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
    CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    SKIP_LABELS = {"DRAFT", "SPAM", "TRASH", "CHAT"}

    def __init__(self, conn):
        super().__init__(conn)
        self.box = _mailbox(conn)

    def _get(self, url):
        status, headers, raw = self.box.call("GET", url, {"Accept": "application/json"})
        body = _json(raw)
        if status == 401 or status == 403 and "insufficient" in raw.decode("utf-8", "replace").lower():
            raise MailError("Google needs to be connected again.", "auth")
        return status, body

    def start(self):
        if self.conn.mail:
            status, body = self._get(f"{self.GMAIL}/profile")
            if status >= 400 or not body.get("historyId"):
                raise MailError(f"Gmail didn't answer ({status}). Try again shortly.")
            self.conn.mail_cursor = {"history": str(body["historyId"])}

    def new_messages(self):
        cur = self.conn.mail_cursor.get("history")
        if not cur:
            self.start()
            return []
        ids, page, latest = [], None, cur
        for _ in range(20):
            q = {"startHistoryId": cur, "historyTypes": "messageAdded", "maxResults": 500}
            if page:
                q["pageToken"] = page
            status, body = self._get(f"{self.GMAIL}/history?" + urllib.parse.urlencode(q))
            if status == 404:  # too old to continue from: carry on from now
                self.start()
                return []
            if status >= 400:
                raise MailError(f"Gmail didn't answer ({status}). Try again shortly.")
            for h in body.get("history") or []:
                for added in h.get("messagesAdded") or []:
                    m = added.get("message") or {}
                    if m.get("id") and not (set(m.get("labelIds") or []) & self.SKIP_LABELS):
                        ids.append(m["id"])
            latest = str(body.get("historyId") or latest)
            page = body.get("nextPageToken")
            if not page:
                break
        out = []
        fields = "id,threadId,labelIds,internalDate,payload(headers,partId,filename,mimeType,body/size," \
                 "parts(partId,filename,mimeType,body/size,parts(partId,filename,mimeType,body/size)))"
        for mid in list(dict.fromkeys(ids))[:BATCH]:
            status, m = self._get(f"{self.GMAIL}/messages/{urllib.parse.quote(mid)}?format=full&fields="
                                  + urllib.parse.quote(fields))
            if status == 404:
                continue
            if status >= 400:
                raise MailError(f"Gmail didn't answer ({status}). Try again shortly.")
            if set(m.get("labelIds") or []) & self.SKIP_LABELS:
                continue
            payload = m.get("payload") or {}
            h = {x.get("name", "").lower(): x.get("value", "") for x in payload.get("headers") or []}
            atts = []

            def walk(p):
                if p.get("filename"):
                    atts.append({"name": p["filename"][:255], "size": (p.get("body") or {}).get("size") or 0,
                                 "type": p.get("mimeType") or "", "part": p.get("partId") or ""})
                for c in p.get("parts") or []:
                    walk(c)

            walk(payload)
            try:
                at = dt.datetime.fromtimestamp(int(m.get("internalDate") or 0) / 1000, UTC)
            except (TypeError, ValueError):
                at = timezone.now()
            sender = addresses(h.get("from"))
            out.append(RemoteMessage(
                remote_id=m["id"], message_id=h.get("message-id", "").strip(), subject=header_text(h.get("subject"))[:300],
                at=at, sender=sender[0] if sender else ("", ""), to=addresses(h.get("to")), cc=addresses(h.get("cc")),
                attachments=[{k: a[k] for k in ("name", "size", "type")} for a in atts],
                extra={"parts": [a["part"] for a in atts]},
            ))
        self.conn.mail_cursor = {"history": latest}
        return out

    def _raw(self, copy):
        status, m = self._get(f"{self.GMAIL}/messages/{urllib.parse.quote(copy.remote_id)}?format=raw")
        if status == 404:
            raise MailError("This email is no longer in the mailbox.", "rejected")
        if status >= 400 or not m.get("raw"):
            raise MailError(f"Gmail didn't answer ({status}). Try again shortly.")
        data = base64.urlsafe_b64decode(m["raw"] + "=" * (-len(m["raw"]) % 4))
        return email.message_from_bytes(data, policy=email.policy.default)

    def open_message(self, copy):
        msg = self._raw(copy)
        return {"text": message_text(msg)}

    def attachment(self, copy, index):
        parts = message_attachments(self._raw(copy))
        if not 0 <= index < len(parts):
            raise MailError("That attachment isn't in the email any more.", "rejected")
        p = parts[index]
        return p.get_filename() or "attachment", p.get_content_type(), p.get_payload(decode=True) or b""

    def events(self, start, end):
        out, page = [], None
        for _ in range(10):
            q = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                 "showDeleted": "true", "maxResults": 250, "orderBy": "startTime",
                 "fields": "nextPageToken,items(id,iCalUID,status,summary,start,end,attendees(email,displayName,"
                           "self,responseStatus),organizer(email,displayName,self),htmlLink)"}
            if page:
                q["pageToken"] = page
            status, body = self._get(f"{self.CAL}?" + urllib.parse.urlencode(q))
            if status >= 400:
                raise MailError(f"Google Calendar didn't answer ({status}). Try again shortly.",
                                "auth" if status in (401, 403) else "temporary")
            for e in body.get("items") or []:
                ev = self._event(e)
                if ev:
                    out.append(ev)
            page = body.get("nextPageToken")
            if not page:
                break
        return out

    def _event(self, e):
        s, en = e.get("start") or {}, e.get("end") or {}
        all_day = "date" in s and "dateTime" not in s
        start = _dt(s.get("dateTime") or (s.get("date", "") + "T00:00:00+00:00"))
        end = _dt(en.get("dateTime") or (en.get("date", "") + "T00:00:00+00:00")) if en else None
        if not start or not e.get("id"):
            return None
        org = e.get("organizer") or {}
        return RemoteEvent(
            remote_id=e["id"], uid=e.get("iCalUID") or e["id"], start=start, end=end, all_day=all_day,
            summary=(e.get("summary") or "")[:300],
            organizer=((org.get("displayName") or "", (org.get("email") or "").lower()) if org.get("email") else None),
            attendees=[(a.get("displayName") or "", (a.get("email") or "").lower()) for a in e.get("attendees") or []
                       if a.get("email") and (a.get("responseStatus") != "declined" or a.get("self"))],
            cancelled=e.get("status") == "cancelled", extra={"link": e.get("htmlLink") or ""},
        )

    def open_event(self, copy):
        status, e = self._get(f"{self.CAL}/{urllib.parse.quote(copy.remote_id)}?fields=description,location")
        if status >= 400:
            raise MailError("This meeting couldn't be read from the calendar.")
        return {"description": html_to_text(e.get("description") or "")[:MAX_TEXT], "location": e.get("location") or ""}

    def create_event(self, *, summary, start, end, attendees, location, description):
        body = {"summary": summary, "location": location, "description": description,
                "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                "attendees": [{"email": e, "displayName": n} for n, e in attendees]}
        status, headers, raw = self.box.call("POST", f"{self.CAL}?sendUpdates=all",
                                             {"Content-Type": "application/json", "Accept": "application/json"},
                                             json.dumps(body).encode())
        if status >= 400:
            raise MailError(f"Google Calendar didn't add the meeting ({status}).",
                            "auth" if status in (401, 403) else "rejected")
        ev = self._event(_json(raw))
        if not ev:
            raise MailError("Google Calendar didn't say what it added.")
        return ev


class MicrosoftSource(Source):
    label = "Microsoft 365"
    GRAPH = "https://graph.microsoft.com/v1.0"
    H = {"Accept": "application/json", "Prefer": 'IdType="ImmutableId", outlook.timezone="UTC"'}

    def __init__(self, conn):
        super().__init__(conn)
        self.box = _mailbox(conn)

    def _get(self, url, extra=None):
        status, headers, raw = self.box.call("GET", url, {**self.H, **(extra or {})})
        if status in (401, 403):
            raise MailError("Microsoft needs to be connected again.", "auth")
        return status, _json(raw)

    def start(self):
        if self.conn.mail:
            skip = []
            for folder in ("junkemail", "deleteditems", "drafts"):
                status, body = self._get(f"{self.GRAPH}/me/mailFolders/{folder}?$select=id")
                if status < 400 and body.get("id"):
                    skip.append(body["id"])
            self.conn.mail_cursor = {"since": timezone.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                     "skip": skip}

    def new_messages(self):
        cur = self.conn.mail_cursor
        if not cur.get("since"):
            self.start()
            return []
        since, skip = cur["since"], set(cur.get("skip") or [])
        q = {"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime asc", "$top": "50",
             "$select": "id,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                        "hasAttachments,isDraft,parentFolderId"}
        url, out, latest = f"{self.GRAPH}/me/messages?" + urllib.parse.urlencode(q), [], since
        while url and len(out) < BATCH:
            status, body = self._get(url)
            if status >= 400:
                raise MailError(f"Outlook didn't answer ({status}). Try again shortly.")
            for m in body.get("value") or []:
                got = _dt(m.get("receivedDateTime") or "")
                if got and got > (_dt(latest) or got):
                    latest = got.strftime("%Y-%m-%dT%H:%M:%SZ")
                if m.get("isDraft") or m.get("parentFolderId") in skip:
                    continue

                def who(x):
                    a = (x or {}).get("emailAddress") or {}
                    return ((a.get("name") or "").strip(), (a.get("address") or "").strip().lower())

                atts = []
                if m.get("hasAttachments"):
                    s2, ab = self._get(f"{self.GRAPH}/me/messages/{urllib.parse.quote(m['id'])}/attachments"
                                       "?$select=id,name,size,contentType,isInline")
                    atts = [{"name": (a.get("name") or "attachment")[:255], "size": a.get("size") or 0,
                             "type": a.get("contentType") or "", "id": a.get("id")}
                            for a in ab.get("value") or [] if not a.get("isInline")] if s2 < 400 else []
                out.append(RemoteMessage(
                    remote_id=m["id"], message_id=(m.get("internetMessageId") or "").strip(),
                    subject=(m.get("subject") or "")[:300], at=_dt(m.get("receivedDateTime") or "") or timezone.now(),
                    sender=who(m.get("from")), to=[who(x) for x in m.get("toRecipients") or []],
                    cc=[who(x) for x in m.get("ccRecipients") or []],
                    attachments=[{k: a[k] for k in ("name", "size", "type")} for a in atts],
                    extra={"att_ids": [a["id"] for a in atts]},
                ))
            url = body.get("@odata.nextLink")
        self.conn.mail_cursor = {**cur, "since": latest}
        return out

    def open_message(self, copy):
        status, m = self._get(f"{self.GRAPH}/me/messages/{urllib.parse.quote(copy.remote_id)}?$select=body",
                              {"Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'})
        if status == 404:
            raise MailError("This email is no longer in the mailbox.", "rejected")
        if status >= 400:
            raise MailError(f"Outlook didn't answer ({status}). Try again shortly.")
        body = m.get("body") or {}
        text = body.get("content") or ""
        return {"text": (html_to_text(text) if body.get("contentType") == "html" else text)[:MAX_TEXT]}

    def attachment(self, copy, index):
        ids = copy.extra.get("att_ids") or []
        if not 0 <= index < len(ids):
            raise MailError("That attachment isn't in the email any more.", "rejected")
        status, a = self._get(f"{self.GRAPH}/me/messages/{urllib.parse.quote(copy.remote_id)}/attachments/"
                              f"{urllib.parse.quote(ids[index])}")
        if status >= 400 or "contentBytes" not in a:
            raise MailError("That attachment couldn't be fetched.", "rejected")
        return a.get("name") or "attachment", a.get("contentType") or "", base64.b64decode(a["contentBytes"])

    def events(self, start, end):
        q = {"startDateTime": start.astimezone(UTC).isoformat(), "endDateTime": end.astimezone(UTC).isoformat(),
             "$top": "100", "$select": "id,iCalUId,subject,start,end,isAllDay,attendees,organizer,isCancelled,webLink"}
        url, out = f"{self.GRAPH}/me/calendarView?" + urllib.parse.urlencode(q), []
        for _ in range(20):
            if not url:
                break
            status, body = self._get(url)
            if status >= 400:
                raise MailError(f"Outlook Calendar didn't answer ({status}). Try again shortly.")
            for e in body.get("value") or []:
                ev = self._event(e)
                if ev:
                    out.append(ev)
            url = body.get("@odata.nextLink")
        return out

    def _event(self, e):
        def when(x):  # times come in UTC (the Prefer header asks for it)
            v = (x or {}).get("dateTime") or ""
            return _dt(v[:19] + "+00:00") if v else None

        def who(x):
            a = (x or {}).get("emailAddress") or {}
            return ((a.get("name") or "").strip(), (a.get("address") or "").strip().lower())

        start = when(e.get("start"))
        if not start or not e.get("id"):
            return None
        org = who(e.get("organizer"))
        return RemoteEvent(
            remote_id=e["id"], uid=e.get("iCalUId") or e["id"], start=start, end=when(e.get("end")),
            all_day=bool(e.get("isAllDay")), summary=(e.get("subject") or "")[:300],
            organizer=org if org[1] else None,
            attendees=[who(a) for a in e.get("attendees") or []
                       if ((a.get("status") or {}).get("response") != "declined") and who(a)[1]],
            cancelled=bool(e.get("isCancelled")), extra={"link": e.get("webLink") or ""},
        )

    def open_event(self, copy):
        status, e = self._get(f"{self.GRAPH}/me/events/{urllib.parse.quote(copy.remote_id)}?$select=body,location",
                              {"Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'})
        if status >= 400:
            raise MailError("This meeting couldn't be read from the calendar.")
        return {"description": ((e.get("body") or {}).get("content") or "")[:MAX_TEXT],
                "location": (e.get("location") or {}).get("displayName") or ""}

    def create_event(self, *, summary, start, end, attendees, location, description):
        body = {"subject": summary, "body": {"contentType": "text", "content": description},
                "start": {"dateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                "end": {"dateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                "location": {"displayName": location},
                "attendees": [{"emailAddress": {"address": e, "name": n}, "type": "required"} for n, e in attendees]}
        status, headers, raw = self.box.call("POST", f"{self.GRAPH}/me/events",
                                             {**self.H, "Content-Type": "application/json"}, json.dumps(body).encode())
        if status >= 400:
            raise MailError(f"Outlook Calendar didn't add the meeting ({status}).",
                            "auth" if status in (401, 403) else "rejected")
        ev = self._event(_json(raw))
        if not ev:
            raise MailError("Outlook Calendar didn't say what it added.")
        return ev


# ---------------------------------------------------------------- IMAP + CalDAV (iCloud and others)

ICLOUD = {"imap_host": "imap.mail.me.com", "imap_port": 993, "caldav_url": "https://caldav.icloud.com/"}
_UID = re.compile(rb"UID (\d+)")
_NAME = re.compile(rb'"(?:FILE)?NAME\*?" "([^"]{1,255})"', re.I)
_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>"(?:[^"\\]|\\.)*"|\S+)')


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class PasswordSource(Source):
    """iCloud (fixed servers) or any IMAP mailbox plus CalDAV calendar, with an app password."""

    label = "Mailbox"

    def __init__(self, conn):
        super().__init__(conn)
        s = secret(conn)
        if not s.get("password"):
            raise MailError("This connection needs its password again.", "auth")
        self.user = s.get("username") or conn.email
        self.password = s["password"]
        self.imap_host = s.get("imap_host") or ""
        self.imap_port = int(s.get("imap_port") or 993)
        self.caldav_url = s.get("caldav_url") or ""

    # ---- IMAP

    def _imap(self):
        if not self.imap_host:
            raise MailError("No mail server is set for this connection.", "setup")
        M = net.IMAP(self.imap_host, self.imap_port)
        try:
            M.login(self.user, self.password)
        except Exception:
            with contextlib.suppress(Exception):
                M.logout()
            raise MailError("The mail server refused the email address or app password.", "auth")
        return M

    def _folders(self, M) -> list[str]:
        typ, data = M.list()
        inbox, sent = "INBOX", None
        names = []
        for line in data or []:
            if not isinstance(line, bytes):
                continue
            m = _LIST.match(line)
            if not m:
                continue
            raw = m.group("name").decode("utf-8", "replace")
            name = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\") if raw.startswith('"') else raw
            names.append(name)
            if b"\\Sent" in m.group("flags"):
                sent = name
        if not sent:
            sent = next((n for n in names if n.lower() in ("sent", "sent messages", "sent items", "sent mail")), None)
        return [inbox] + ([sent] if sent else [])

    def _status(self, M, folder) -> tuple[int, int]:
        typ, data = M.status(_quote(folder), "(UIDNEXT UIDVALIDITY)")
        text = (data[0] or b"") if data else b""
        nxt = re.search(rb"UIDNEXT (\d+)", text)
        val = re.search(rb"UIDVALIDITY (\d+)", text)
        return (int(val.group(1)) if val else 0), (int(nxt.group(1)) if nxt else 1)

    def verify(self):
        if self.conn.mail:
            M = self._imap()
            M.logout()
        if self.conn.calendar:
            self._calendars()

    def start(self):
        if self.conn.mail:
            M = self._imap()
            try:
                cur = {}
                for f in self._folders(M):
                    v, nxt = self._status(M, f)
                    cur[f] = {"v": v, "last": nxt - 1}
                self.conn.mail_cursor = {"folders": cur}
            finally:
                M.logout()
        if self.conn.calendar:
            self.conn.calendar_cursor = {"calendars": self._calendars()}

    def new_messages(self):
        if not self.conn.mail_cursor.get("folders"):
            self.start()
            return []
        M = self._imap()
        out, cur = [], dict(self.conn.mail_cursor["folders"])
        try:
            for folder in self._folders(M):
                v, nxt = self._status(M, folder)
                c = cur.get(folder)
                if not c or c.get("v") != v:  # new folder or renumbered: carry on from now
                    cur[folder] = {"v": v, "last": nxt - 1}
                    continue
                if nxt - 1 <= c["last"]:
                    continue
                typ, _ = M.select(_quote(folder), readonly=True)
                if typ != "OK":
                    continue
                typ, data = M.uid("SEARCH", None, f"UID {c['last'] + 1}:*")
                uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > c["last"])[:BATCH]
                if not uids:
                    continue
                typ, data = M.uid("FETCH", ",".join(map(str, uids)),
                                  "(UID BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)])")
                for part in data or []:
                    if not isinstance(part, tuple):
                        continue
                    meta, headers = part[0], part[1]
                    um = _UID.search(meta)
                    if not um:
                        continue
                    uid = int(um.group(1))
                    # Encoded words stay ASCII; servers that send raw UTF-8 (RFC 6532) are read as UTF-8.
                    h = email.message_from_string((headers or b"").decode("utf-8", "replace"),
                                                  policy=email.policy.compat32)
                    try:
                        at = parsedate_to_datetime(h.get("Date")) if h.get("Date") else timezone.now()
                        at = at if at.tzinfo else at.replace(tzinfo=UTC)
                    except (TypeError, ValueError):
                        at = timezone.now()
                    names = list(dict.fromkeys(header_text(n.decode("utf-8", "replace")) for n in _NAME.findall(meta)))
                    sender = addresses(h.get("From"))
                    out.append(RemoteMessage(
                        remote_id=f"{folder}\t{v}\t{uid}", message_id=(h.get("Message-ID") or "").strip(),
                        subject=header_text(h.get("Subject"))[:300], at=at,
                        sender=sender[0] if sender else ("", ""), to=addresses(h.get("To")), cc=addresses(h.get("Cc")),
                        attachments=[{"name": n[:255], "size": 0, "type": ""} for n in names],
                    ))
                cur[folder] = {"v": v, "last": max(uids)}
        finally:
            with contextlib.suppress(Exception):
                M.logout()
        self.conn.mail_cursor = {"folders": cur}
        return out

    def _fetch(self, copy):
        try:
            folder, v, uid = copy.remote_id.split("\t")
        except ValueError:
            raise MailError("This email can't be found in the mailbox.", "rejected")
        M = self._imap()
        try:
            M.select(_quote(folder), readonly=True)
            if self._status(M, folder)[0] != int(v):
                raise MailError("This email is no longer in the mailbox.", "rejected")
            typ, data = M.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in data or [] if isinstance(p, tuple)), None)
        finally:
            M.logout()
        if not raw:
            raise MailError("This email is no longer in the mailbox.", "rejected")
        return email.message_from_bytes(raw, policy=email.policy.default)

    def open_message(self, copy):
        return {"text": message_text(self._fetch(copy))}

    def attachment(self, copy, index):
        parts = message_attachments(self._fetch(copy))
        if not 0 <= index < len(parts):
            raise MailError("That attachment isn't in the email any more.", "rejected")
        p = parts[index]
        return p.get_filename() or "attachment", p.get_content_type(), p.get_payload(decode=True) or b""

    # ---- CalDAV

    def _dav(self, method, url, body=None, depth="0", extra=None):
        h = {"Authorization": net.basic_auth(self.user, self.password), "Depth": depth,
             "Content-Type": "application/xml; charset=utf-8", **(extra or {})}
        status, headers, data, final = net.http(method, url, h, body.encode() if isinstance(body, str) else body)
        if status == 401:
            raise MailError("The calendar refused the email address or app password.", "auth")
        return status, data, final

    @staticmethod
    def _xml(data: bytes):
        if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data.upper():
            raise MailError("The calendar server answered with something Dealflow won't read.")
        try:
            return ET.fromstring(data)
        except (ET.ParseError, DefusedXmlException):
            raise MailError("The calendar server's answer couldn't be read.")

    D, C = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"

    def _href(self, root, tag):
        el = root.find(f".//{tag}/{self.D}href")
        return el.text.strip() if el is not None and el.text else None

    def _calendars(self) -> list[dict]:
        if not self.caldav_url:
            raise MailError("No calendar address is set for this connection.", "setup")
        prop = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
        status, data, base = self._dav("PROPFIND", self.caldav_url, prop)
        if status >= 400:
            raise MailError(f"The calendar server didn't answer ({status}). Check the address.", "setup")
        principal = self._href(self._xml(data), f"{self.D}current-user-principal")
        if not principal:
            raise MailError("The calendar server didn't say where your calendars are.", "setup")
        principal = urllib.parse.urljoin(base, principal)
        prop = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')
        status, data, base = self._dav("PROPFIND", principal, prop)
        home = self._href(self._xml(data), f"{self.C}calendar-home-set") if status < 400 else None
        if not home:
            raise MailError("The calendar server didn't say where your calendars are.", "setup")
        home = urllib.parse.urljoin(base, home)
        prop = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>')
        status, data, base = self._dav("PROPFIND", home, prop, depth="1")
        if status >= 400:
            raise MailError("The calendar server didn't list your calendars.", "setup")
        out = []
        for resp in self._xml(data).findall(f"{self.D}response"):
            href = resp.findtext(f"{self.D}href") or ""
            rt = resp.find(f".//{self.D}resourcetype")
            if rt is None or rt.find(f"{self.C}calendar") is None:
                continue
            comps = [c.get("name") for c in resp.findall(f".//{self.C}comp")]
            if comps and "VEVENT" not in comps:
                continue
            out.append({"href": urllib.parse.urljoin(base, href.strip()),
                        "name": (resp.findtext(f".//{self.D}displayname") or "")[:100]})
        if not out:
            raise MailError("No calendars were found for this account.", "setup")
        return out

    def _cals(self) -> list[dict]:
        cals = self.conn.calendar_cursor.get("calendars")
        if not cals:
            cals = self._calendars()
            self.conn.calendar_cursor = {**self.conn.calendar_cursor, "calendars": cals}
        return cals

    def events(self, start, end):
        s, e = start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"), end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        q = ('<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
             f'<d:prop><d:getetag/><c:calendar-data><c:expand start="{s}" end="{e}"/></c:calendar-data></d:prop>'
             '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
             f'<c:time-range start="{s}" end="{e}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
        out = []
        for cal in self._cals():
            status, data, base = self._dav("REPORT", cal["href"], q, depth="1")
            if status >= 400:
                raise MailError(f"The calendar server didn't answer ({status}).")
            for resp in self._xml(data).findall(f"{self.D}response"):
                href = urllib.parse.urljoin(base, (resp.findtext(f"{self.D}href") or "").strip())
                text = resp.findtext(f".//{self.C}calendar-data") or ""
                for ev in ics.parse(text):
                    out.append(RemoteEvent(
                        remote_id=f"{href}#{ev.recurrence_id or ev.start.strftime('%Y%m%dT%H%M%SZ')}", uid=ev.uid,
                        start=ev.start, end=ev.end, all_day=ev.all_day, summary=ev.summary, organizer=ev.organizer,
                        attendees=ev.attendees, cancelled=ev.status == "CANCELLED", extra={"href": href},
                    ))
        return out

    def open_event(self, copy):
        href = copy.extra.get("href") or copy.remote_id.split("#")[0]
        status, data, _ = self._dav("GET", href, extra={"Content-Type": "text/calendar"})
        if status >= 400:
            raise MailError("This meeting couldn't be read from the calendar.")
        evs = ics.parse(data.decode("utf-8", "replace"))
        ev = evs[0] if evs else None
        return {"description": ev.description if ev else "", "location": ev.location if ev else ""}

    def create_event(self, *, summary, start, end, attendees, location, description):
        cal = self._cals()[0]
        uid, text = ics.invitation(organizer=(self.conn.user.full_name or "", self.conn.email), attendees=attendees,
                                   start=start, end=end, summary=summary, location=location, description=description)
        href = cal["href"].rstrip("/") + "/" + urllib.parse.quote(uid.split("@")[0]) + ".ics"
        status, data, _ = self._dav("PUT", href, text, extra={"Content-Type": "text/calendar; charset=utf-8",
                                                              "If-None-Match": "*"})
        if status >= 400:
            raise MailError(f"The calendar didn't add the meeting ({status}).", "rejected")
        return RemoteEvent(remote_id=f"{href}#{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}", uid=uid,
                           start=start, end=end, summary=summary, organizer=(self.conn.user.full_name or "", self.conn.email),
                           attendees=attendees, extra={"href": href})


def source(conn) -> Source:
    if conn.kind == "google":
        return GoogleSource(conn)
    if conn.kind == "microsoft":
        return MicrosoftSource(conn)
    return PasswordSource(conn)


# Scopes asked for when logging is connected (decision 64): reading mail, reading and adding events.
GOOGLE_SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly",
                 "https://www.googleapis.com/auth/calendar.events"]
MICROSOFT_SCOPES = ["offline_access", "openid", "email", "User.Read", "Mail.Read", "Calendars.ReadWrite"]


def authorize_url(kind: str, cfg, redirect_uri: str, state: str, challenge: str, mail: bool, calendar: bool) -> str:
    if kind == "google":
        scopes = ["openid", "email"] + (["https://www.googleapis.com/auth/gmail.readonly"] if mail else []) \
            + (["https://www.googleapis.com/auth/calendar.events"] if calendar else [])
        q = {"client_id": cfg.client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent select_account",
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
             "include_granted_scopes": "false"}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)
    scopes = ["offline_access", "openid", "email", "User.Read"] + (["Mail.Read"] if mail else []) \
        + (["Calendars.ReadWrite"] if calendar else [])
    outlook = MAIL_PROVIDERS["outlook"]
    q = {"client_id": cfg.client_id, "redirect_uri": redirect_uri, "response_type": "code", "response_mode": "query",
         "scope": " ".join(scopes), "state": state, "prompt": "select_account", "code_challenge": challenge,
         "code_challenge_method": "S256"}
    return f"{outlook._base(cfg)}/authorize?" + urllib.parse.urlencode(q)


def exchange(kind: str, cfg, code: str, redirect_uri: str, verifier: str, mail: bool, calendar: bool):
    """Finish connecting: (tokens, address). Refuses if the permissions asked for weren't all given."""
    if kind == "google":
        p = MAIL_PROVIDERS["gmail"]
        body = p._token(cfg, {"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code,
                              "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": verifier})
        granted = body.get("scope") or ""
        if (mail and "gmail.readonly" not in granted) or (calendar and "calendar.events" not in granted):
            raise MailError("Google didn't give every permission Dealflow asked for. Connect again and leave all "
                            "the boxes ticked.", "auth")
        claims = {}
        try:
            part = (body.get("id_token") or "").split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (IndexError, ValueError):
            pass
        address = (claims.get("email") or "").lower()
        if not address or claims.get("email_verified") is False:
            raise MailError("Google didn't say which account this is. Try connecting again.", "auth")
        return p._tokens_from(body), address
    p = MAIL_PROVIDERS["outlook"]
    scopes = ["offline_access", "openid", "email", "User.Read"] + (["Mail.Read"] if mail else []) \
        + (["Calendars.ReadWrite"] if calendar else [])
    body = p._token(cfg, {"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code,
                          "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": verifier,
                          "scope": " ".join(scopes)})
    granted = (body.get("scope") or "").lower()
    if (mail and "mail.read" not in granted) or (calendar and "calendars.readwrite" not in granted):
        raise MailError("Microsoft didn't give every permission Dealflow asked for. Connect again and accept them.",
                        "auth")
    tokens = p._tokens_from(body)
    from mail.providers import http

    status, _, raw = http("GET", f"{p.GRAPH}/me?$select=mail,userPrincipalName",
                          {"Authorization": f"Bearer {tokens['access']}", "Accept": "application/json"})
    me = _json(raw)
    address = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    if status >= 400 or "@" not in address:
        raise MailError("Microsoft didn't say which account this is. Try connecting again.", "auth")
    return tokens, address


def revoke(conn):
    """Best effort: tell Google to forget the permission when someone disconnects."""
    if conn.kind != "google":
        return
    tokens = secret(conn)
    if tokens.get("refresh"):
        try:
            from mail.providers import http

            http("POST", "https://oauth2.googleapis.com/revoke", {"Content-Type": "application/x-www-form-urlencoded"},
                 urllib.parse.urlencode({"token": tokens["refresh"]}).encode())
        except MailError:
            pass

