"""Stand-ins for Google (Gmail, Calendar), Microsoft Graph, an IMAP server and a CalDAV server, so the
logging tests run without real accounts. The real IMAP/CalDAV code is also checked against a real
CalDAV server in test_password_sources (when Radicale is installed)."""

from __future__ import annotations

import base64
import datetime as dt
import email
import email.policy
import json
import re
import urllib.parse
from email.message import EmailMessage

UTC = dt.timezone.utc


def _id_token(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def mime(sender, to, subject, text, cc=(), attachments=(), message_id=None, date=None) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = ", ".join(to)
    if cc:
        m["Cc"] = ", ".join(cc)
    m["Subject"] = subject
    m["Message-ID"] = message_id or f"<{abs(hash((sender, subject, text)))}@test>"
    m["Date"] = email.utils.format_datetime(date or dt.datetime.now(UTC))
    m.set_content(text)
    for name, ctype, data in attachments:
        maintype, subtype = ctype.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return bytes(m)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FakeCloud:
    """Google and Microsoft through mail.providers.TRANSPORT."""

    GOOGLE_SCOPE = ("openid email https://www.googleapis.com/auth/gmail.readonly "
                    "https://www.googleapis.com/auth/calendar.events")
    MS_SCOPE = "offline_access openid email User.Read Mail.Read Calendars.ReadWrite"

    def __init__(self, address="admin@firm.com"):
        self.address = address
        self.calls = []
        self.valid = set()
        self.n = 0
        self.google_scope = self.GOOGLE_SCOPE
        self.ms_scope = self.MS_SCOPE
        self.history = 100
        self.gmail = {}  # id -> {"hid", "labels", "raw", "at"}
        self.gcal = {}  # id -> event dict (Google shape)
        self.graph_mail = {}  # id -> {"m": message dict, "raw": bytes, "atts": [...]}
        self.graph_cal = {}  # id -> event dict (Graph shape)
        self.created = []  # events added through the API
        self.fail = set()  # "gmail", "gcal", "graph" answer 401

    def _token(self, p):
        self.n += 1
        t = f"{p}-access-{self.n}"
        self.valid.add(t)
        return t

    # ---- filling the mailboxes

    def add_gmail(self, mid, raw: bytes, labels=("INBOX",), at=None):
        self.history += 1
        self.gmail[mid] = {"hid": self.history, "labels": list(labels), "raw": raw,
                           "at": at or dt.datetime.now(UTC)}

    def add_gcal(self, eid, start, minutes=60, summary="Meeting", attendees=(), status="confirmed", organizer=None,
                 description="", location="", all_day=False):
        e = {"id": eid, "iCalUID": f"{eid}@google.com", "status": status, "summary": summary,
             "attendees": [{"email": a, "displayName": a.split("@")[0].title(), "responseStatus": "accepted"}
                           for a in attendees],
             "organizer": {"email": organizer or self.address, "self": organizer is None},
             "description": description, "location": location, "htmlLink": f"https://calendar.google.com/{eid}"}
        if all_day:
            e["start"], e["end"] = {"date": start.date().isoformat()}, {"date": (start + dt.timedelta(days=1)).date().isoformat()}
        else:
            e["start"] = {"dateTime": _iso(start)}
            e["end"] = {"dateTime": _iso(start + dt.timedelta(minutes=minutes))}
        self.gcal[eid] = e
        return e

    def add_graph(self, mid, sender, to, subject, text="", cc=(), attachments=(), at=None, folder="inbox"):
        at = at or dt.datetime.now(UTC)
        who = lambda a: {"emailAddress": {"address": a, "name": a.split("@")[0].title()}}  # noqa: E731
        self.graph_mail[mid] = {"m": {
            "id": mid, "internetMessageId": f"<{mid}@outlook.test>", "subject": subject, "from": who(sender),
            "toRecipients": [who(a) for a in to], "ccRecipients": [who(a) for a in cc], "receivedDateTime": _iso(at),
            "hasAttachments": bool(attachments), "isDraft": False, "parentFolderId": folder,
        }, "text": text, "atts": [{"id": f"att{i}", "name": n, "contentType": c, "size": len(d), "isInline": False,
                                   "contentBytes": base64.b64encode(d).decode()} for i, (n, c, d) in enumerate(attachments)]}

    def add_graph_event(self, eid, start, minutes=60, subject="Meeting", attendees=(), cancelled=False):
        self.graph_cal[eid] = {
            "id": eid, "iCalUId": f"{eid}-uid", "subject": subject, "isAllDay": False, "isCancelled": cancelled,
            "start": {"dateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
            "end": {"dateTime": (start + dt.timedelta(minutes=minutes)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0000000"),
                    "timeZone": "UTC"},
            "attendees": [{"emailAddress": {"address": a, "name": a.split("@")[0].title()},
                           "status": {"response": "accepted"}} for a in attendees],
            "organizer": {"emailAddress": {"address": self.address, "name": "Me"}}, "webLink": "https://outlook/x",
            "body": {"content": "Agenda: numbers", "contentType": "text"}, "location": {"displayName": "Their office"},
        }

    # ---- answering

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        bearer = (headers.get("Authorization") or "")[7:]

        def answer(status, data=None):
            return status, {}, json.dumps(data if data is not None else {}).encode()

        if u.netloc == "oauth2.googleapis.com" and u.path == "/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("grant_type") == "authorization_code":
                if form.get("code") != "good-code":
                    return answer(400, {"error": "invalid_grant"})
                return answer(200, {"access_token": self._token("g"), "refresh_token": "g-refresh", "expires_in": 3599,
                                    "scope": self.google_scope,
                                    "id_token": _id_token({"email": self.address, "email_verified": True})})
            return answer(200, {"access_token": self._token("g"), "expires_in": 3599})
        if u.netloc == "oauth2.googleapis.com" and u.path == "/revoke":
            return answer(200)
        if u.netloc == "login.microsoftonline.com":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if form.get("grant_type") == "authorization_code" and form.get("code") != "good-code":
                return answer(400, {"error": "invalid_grant"})
            return answer(200, {"access_token": self._token("m"), "refresh_token": "m-refresh", "expires_in": 3599,
                                "scope": self.ms_scope})

        if u.netloc == "gmail.googleapis.com":
            if bearer not in self.valid or "gmail" in self.fail:
                return answer(401, {"error": {"code": 401}})
            path = u.path.removeprefix("/gmail/v1/users/me")
            if path == "/profile":
                return answer(200, {"emailAddress": self.address, "historyId": str(self.history)})
            if path == "/history":
                start = int(q["startHistoryId"])
                hist = [{"id": str(m["hid"]), "messagesAdded": [{"message": {"id": mid, "labelIds": m["labels"]}}]}
                        for mid, m in sorted(self.gmail.items(), key=lambda x: x[1]["hid"]) if m["hid"] > start]
                return answer(200, {"history": hist, "historyId": str(self.history)})
            m = re.fullmatch(r"/messages/([^/]+)", path)
            if m:
                msg = self.gmail.get(urllib.parse.unquote(m.group(1)))
                if not msg:
                    return answer(404)
                if q.get("format") == "raw":
                    return answer(200, {"raw": base64.urlsafe_b64encode(msg["raw"]).decode()})
                parsed = email.message_from_bytes(msg["raw"], policy=email.policy.default)
                parts, i = [], 0
                for p in parsed.walk():
                    if p.get_filename():
                        i += 1
                        parts.append({"partId": str(i), "filename": p.get_filename(), "mimeType": p.get_content_type(),
                                      "body": {"size": len(p.get_payload(decode=True) or b"")}})
                return answer(200, {"id": m.group(1), "labelIds": msg["labels"],
                                    "internalDate": str(int(msg["at"].timestamp() * 1000)),
                                    "payload": {"headers": [{"name": k, "value": str(v)} for k, v in parsed.items()],
                                                "parts": parts}})
            return answer(404)

        if u.netloc == "www.googleapis.com" and u.path.startswith("/calendar/v3/calendars/primary/events"):
            if bearer not in self.valid or "gcal" in self.fail:
                return answer(401)
            rest = u.path.removeprefix("/calendar/v3/calendars/primary/events").strip("/")
            if method == "POST":
                data = json.loads(body)
                eid = f"new{len(self.created) + 1}"
                e = {"id": eid, "iCalUID": f"{eid}@google.com", "status": "confirmed", "summary": data["summary"],
                     "start": data["start"], "end": data["end"],
                     "attendees": [{**a, "responseStatus": "needsAction"} for a in data["attendees"]],
                     "organizer": {"email": self.address, "self": True}, "description": data.get("description", ""),
                     "location": data.get("location", ""), "sendUpdates": q.get("sendUpdates")}
                self.gcal[eid] = e
                self.created.append(e)
                return answer(200, e)
            if rest:
                e = self.gcal.get(urllib.parse.unquote(rest))
                return answer(200, e) if e else answer(404)
            t0, t1 = dt.datetime.fromisoformat(q["timeMin"]), dt.datetime.fromisoformat(q["timeMax"])
            items = []
            for e in self.gcal.values():
                s = e["start"].get("dateTime") or e["start"]["date"] + "T00:00:00+00:00"
                st = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                if t0 <= st <= t1:
                    items.append(e)
            return answer(200, {"items": items})

        if u.netloc == "graph.microsoft.com":
            if bearer not in self.valid or "graph" in self.fail:
                return answer(401)
            path = u.path.removeprefix("/v1.0")
            if path == "/me":
                return answer(200, {"mail": self.address})
            m = re.fullmatch(r"/me/mailFolders/(\w+)", path)
            if m:
                return answer(200, {"id": m.group(1)})
            if path == "/me/messages":
                since = re.search(r"ge (\S+)", q.get("$filter", "")).group(1)
                t = lambda v: dt.datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
                vals = [x["m"] for x in self.graph_mail.values() if t(x["m"]["receivedDateTime"]) >= t(since)]
                return answer(200, {"value": sorted(vals, key=lambda v: v["receivedDateTime"])})
            m = re.fullmatch(r"/me/messages/([^/]+)/attachments(?:/([^/]+))?", path)
            if m:
                x = self.graph_mail.get(urllib.parse.unquote(m.group(1)))
                if not x:
                    return answer(404)
                if m.group(2):
                    a = next((a for a in x["atts"] if a["id"] == urllib.parse.unquote(m.group(2))), None)
                    return answer(200, a) if a else answer(404)
                return answer(200, {"value": [{k: v for k, v in a.items() if k != "contentBytes"} for a in x["atts"]]})
            m = re.fullmatch(r"/me/messages/([^/]+)", path)
            if m:
                x = self.graph_mail.get(urllib.parse.unquote(m.group(1)))
                return answer(200, {"body": {"contentType": "text", "content": x["text"]}}) if x else answer(404)
            if path == "/me/calendarView":
                t0 = dt.datetime.fromisoformat(q["startDateTime"])
                t1 = dt.datetime.fromisoformat(q["endDateTime"])
                vals = [e for e in self.graph_cal.values()
                        if t0 <= dt.datetime.fromisoformat(e["start"]["dateTime"][:19] + "+00:00") <= t1]
                return answer(200, {"value": vals})
            m = re.fullmatch(r"/me/events(?:/([^/]+))?", path)
            if m and method == "POST":
                data = json.loads(body)
                eid = f"ev{len(self.created) + 1}"
                e = {"id": eid, "iCalUId": f"{eid}-uid", "subject": data["subject"], "isAllDay": False,
                     "isCancelled": False, "start": data["start"], "end": data["end"], "attendees": data["attendees"],
                     "organizer": {"emailAddress": {"address": self.address}}}
                self.graph_cal[eid] = e
                self.created.append(e)
                return answer(201, e)
            if m and m.group(1):
                e = self.graph_cal.get(urllib.parse.unquote(m.group(1)))
                return answer(200, e) if e else answer(404)
            return answer(404)
        return answer(404, {"error": f"unexpected {method} {url}"})


class FakeIMAP:
    """Enough of imaplib.IMAP4 for PasswordSource: folders, UIDs, headers, whole messages."""

    def __init__(self, server, host, port):
        self.server = server
        self.selected = None

    def login(self, user, password):
        if (user, password) != (self.server.user, self.server.password):
            import imaplib

            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"done"]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent Messages"',
                      b'(\\HasNoChildren \\Junk) "/" "Junk"']

    def _name(self, quoted):
        return quoted.strip('"')

    def status(self, folder, what):
        f = self.server.folders.get(self._name(folder), [])
        nxt = (max(u for u, _ in f) + 1) if f else 1
        return "OK", [f'"{self._name(folder)}" (UIDNEXT {nxt} UIDVALIDITY {self.server.validity})'.encode()]

    def select(self, folder, readonly=False):
        self.selected = self._name(folder)
        return "OK", [str(len(self.server.folders.get(self.selected, []))).encode()]

    def uid(self, cmd, *args):
        msgs = dict(self.server.folders.get(self.selected, []))
        if cmd == "SEARCH":
            lo = int(re.search(r"UID (\d+):", args[1]).group(1))
            found = [u for u in msgs if u >= lo] or ([max(msgs)] if msgs else [])
            return "OK", [" ".join(map(str, found)).encode()]
        if cmd == "FETCH":
            uids = [int(x) for x in args[0].split(",")]
            out = []
            for u in uids:
                raw = msgs.get(u)
                if raw is None:
                    continue
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                if "BODY.PEEK[]" in args[1] and "HEADER" not in args[1]:
                    out += [(f"{u} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
                    continue
                names = " ".join(f'("attachment" ("filename" "{p.get_filename()}"))' for p in parsed.walk()
                                 if p.get_filename())
                rawh = email.message_from_bytes(raw, policy=email.policy.compat32)
                head = "".join(f"{k}: {rawh[k]}\r\n" for k in ("From", "To", "Cc", "Subject", "Date", "Message-ID")
                               if rawh[k]).encode() + b"\r\n"
                out += [(f'{u} (UID {u} BODYSTRUCTURE (("text" "plain") {names}) '
                         f'BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)] {{{len(head)}}}'.encode(), head), b")"]
            return "OK", out
        return "NO", [b""]


class FakeIMAPServer:
    def __init__(self, user="banker@icloud.com", password="abcd-efgh-ijkl-mnop"):
        self.user, self.password, self.validity = user, password, 7
        self.folders = {"INBOX": [], "Sent Messages": []}

    def add(self, folder, raw):
        f = self.folders.setdefault(folder, [])
        f.append(((max(u for u, _ in f) + 1) if f else 1, raw))

    def __call__(self, host, port):
        return FakeIMAP(self, host, port)


class FakeDAV:
    """A CalDAV server with one calendar, through sync.net.HTTP."""

    BASE = "https://caldav.example.com"

    def __init__(self, user="banker@icloud.com", password="abcd-efgh-ijkl-mnop"):
        self.user, self.password = user, password
        self.events = {}  # href -> ics text
        self.puts = []

    def __call__(self, method, url, headers, body):
        import base64 as b64

        auth = headers.get("Authorization", "")
        if auth != "Basic " + b64.b64encode(f"{self.user}:{self.password}".encode()).decode():
            return 401, {}, b""
        u = urllib.parse.urlparse(url)
        ms = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{}</d:multistatus>'
        if method == "PROPFIND" and u.path == "/":
            return 207, {}, ms.format('<d:response><d:href>/</d:href><d:propstat><d:prop><d:current-user-principal>'
                                      '<d:href>/p/</d:href></d:current-user-principal></d:prop></d:propstat></d:response>').encode()
        if method == "PROPFIND" and u.path == "/p/":
            return 207, {}, ms.format('<d:response><d:href>/p/</d:href><d:propstat><d:prop><c:calendar-home-set>'
                                      '<d:href>/home/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response>').encode()
        if method == "PROPFIND" and u.path == "/home/":
            return 207, {}, ms.format(
                '<d:response><d:href>/home/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>'
                '</d:prop></d:propstat></d:response>'
                '<d:response><d:href>/home/work/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>'
                '<c:calendar/></d:resourcetype><d:displayname>Work</d:displayname><c:supported-calendar-component-set>'
                '<c:comp name="VEVENT"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response>'
                '<d:response><d:href>/home/tasks/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>'
                '<c:calendar/></d:resourcetype><c:supported-calendar-component-set><c:comp name="VTODO"/>'
                '</c:supported-calendar-component-set></d:prop></d:propstat></d:response>').encode()
        if method == "REPORT" and u.path == "/home/work/":
            parts = "".join(f"<d:response><d:href>{h}</d:href><d:propstat><d:prop><c:calendar-data>"
                            f"{t.replace('&', '&amp;').replace('<', '&lt;')}</c:calendar-data></d:prop></d:propstat></d:response>"
                            for h, t in self.events.items())
            return 207, {}, ms.format(parts).encode()
        if method == "GET" and u.path in self.events:
            return 200, {}, self.events[u.path].encode()
        if method == "PUT" and u.path.startswith("/home/work/"):
            self.events[u.path] = body.decode()
            self.puts.append((u.path, body.decode()))
            return 201, {}, b""
        return 404, {}, b""
