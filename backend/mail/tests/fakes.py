"""Stand-ins for Google and Microsoft, so the tests run without real mailboxes."""

import base64
import email
import json
import urllib.parse
from email import policy


def _id_token(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class FakeMail:
    """Routes calls by address. `valid` is the set of access tokens accepted right now."""

    def __init__(self):
        self.calls = []
        self.valid = set()
        self.n = 0
        self.gmail_sent = []  # parsed MIME messages
        self.graph = {}  # id -> message dict
        self.uploads = {}  # upload url -> bytearray
        self.fail_next = []  # (status, body, headers) answers for the next send calls
        self.outlook_instant = True  # Exchange files sends at once, else only on deliver()
        self.refresh_ok = True
        self.gmail_scope = "openid email https://www.googleapis.com/auth/gmail.send"

    def _token(self, prefix):
        self.n += 1
        t = f"{prefix}-access-{self.n}"
        self.valid.add(t)
        return t

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        u = urllib.parse.urlparse(url)
        auth = headers.get("Authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else ""

        def answer(status, data=None, h=None):
            return status, {k.lower(): v for k, v in (h or {}).items()}, json.dumps(data or {}).encode()

        # ---- Google
        if u.netloc == "oauth2.googleapis.com" and u.path == "/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("grant_type") == "authorization_code":
                if form.get("code") != "good-code" or not form.get("code_verifier"):
                    return answer(400, {"error": "invalid_grant"})
                return answer(200, {"access_token": self._token("g"), "refresh_token": "g-refresh",
                                    "expires_in": 3599, "scope": self.gmail_scope,
                                    "id_token": _id_token({"email": "Asha@Gmail.com", "email_verified": True})})
            if not self.refresh_ok or form.get("refresh_token") != "g-refresh":
                return answer(400, {"error": "invalid_grant"})
            return answer(200, {"access_token": self._token("g"), "expires_in": 3599})
        if u.netloc == "oauth2.googleapis.com" and u.path == "/revoke":
            return answer(200)
        if u.netloc == "gmail.googleapis.com":
            if bearer not in self.valid:
                return answer(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
            if self.fail_next:
                return self.fail_next.pop(0)
            msg = email.message_from_bytes(body, policy=policy.default)
            self.gmail_sent.append(msg)
            n = len(self.gmail_sent)
            return answer(200, {"id": f"18f{n:05d}", "threadId": f"t{n}", "labelIds": ["SENT"]})

        # ---- Microsoft
        if u.netloc == "login.microsoftonline.com" and u.path.endswith("/token"):
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("grant_type") == "authorization_code":
                if form.get("code") != "good-code":
                    return answer(400, {"error": "invalid_grant"})
                return answer(200, {"access_token": self._token("o"), "refresh_token": "o-refresh-1",
                                    "expires_in": 3599, "scope": "openid email User.Read Mail.ReadWrite Mail.Send"})
            if not self.refresh_ok:
                return answer(400, {"error": "invalid_grant"})
            return answer(200, {"access_token": self._token("o"), "refresh_token": "o-refresh-2", "expires_in": 3599})
        if u.netloc == "upload.fake":
            buf = self.uploads.setdefault(url, bytearray())
            buf.extend(body)
            return answer(200)
        if u.netloc == "graph.microsoft.com":
            if bearer not in self.valid:
                return answer(401, {"error": {"code": "InvalidAuthenticationToken"}})
            path = u.path.removeprefix("/v1.0")
            if path == "/me":
                return answer(200, {"mail": "Asha@Firm.com", "userPrincipalName": "asha@firm.onmicrosoft.com"})
            if path == "/me/mailFolders/sentitems":
                return answer(200, {"id": "SENT-FOLDER"})
            if path == "/me/messages" and method == "POST":
                if self.fail_next:
                    return self.fail_next.pop(0)
                draft = json.loads(body)
                mid = f"AAMk{len(self.graph) + 1}"
                draft.update(id=mid, isDraft=True, parentFolderId="DRAFTS", webLink=f"https://outlook.office.com/?ItemID={mid}",
                             conversationId=f"conv{mid}", uploaded=[])
                self.graph[mid] = draft
                return answer(201, {k: v for k, v in draft.items() if k != "attachments"})
            parts = path.split("/")
            if len(parts) >= 4 and parts[2] == "messages":
                mid = urllib.parse.unquote(parts[3])
                m = self.graph.get(mid)
                if m is None:
                    return answer(404, {"error": {"code": "ErrorItemNotFound"}})
                if len(parts) == 4 and method == "GET":
                    return answer(200, {k: m[k] for k in ("isDraft", "parentFolderId", "webLink", "conversationId")})
                if len(parts) == 4 and method == "DELETE":
                    del self.graph[mid]
                    return answer(204)
                if parts[4:] == ["send"]:
                    m["sent"] = True
                    if self.outlook_instant:
                        self.deliver(mid)
                    return answer(202)
                if parts[4:] == ["attachments", "createUploadSession"]:
                    item = json.loads(body)["AttachmentItem"]
                    up = f"https://upload.fake/{mid}/{len(m['uploaded'])}"
                    m["uploaded"].append((item["name"], up))
                    return answer(201, {"uploadUrl": up})
        return answer(404, {"error": f"unexpected {method} {url}"})

    def deliver(self, mid):
        self.graph[mid].update(isDraft=False, parentFolderId="SENT-FOLDER")
