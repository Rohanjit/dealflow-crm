"""Just enough iCalendar (RFC 5545) for CalDAV calendars: read meetings, write one invitation."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc


@dataclass
class Event:
    uid: str
    start: dt.datetime
    end: dt.datetime | None
    all_day: bool = False
    summary: str = ""
    location: str = ""
    description: str = ""
    status: str = ""
    organizer: tuple[str, str] | None = None  # (name, email)
    attendees: list[tuple[str, str]] = field(default_factory=list)
    recurrence_id: str = ""


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict, str]:
    # NAME;PARAM=a;PARAM2="b:c":value  (a colon inside quotes isn't the separator)
    in_q, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            cut = i
            break
    if cut < 0:
        return line.upper(), {}, ""
    head, value = line[:cut], line[cut + 1:]
    parts = re.findall(r'(?:[^;"]|"[^"]*")+', head)
    name = parts[0].upper() if parts else ""
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return name, params, value


def unescape(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _when(value: str, params: dict) -> tuple[dt.datetime, bool]:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = dt.datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=UTC), True
    utc = value.endswith("Z")
    t = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return t.replace(tzinfo=UTC), False
    tz = params.get("TZID")
    if tz:
        try:
            return t.replace(tzinfo=ZoneInfo(tz.strip("/"))).astimezone(UTC), False
        except (ZoneInfoNotFoundError, ValueError):
            pass
    from django.utils import timezone

    return timezone.make_aware(t).astimezone(UTC), False  # floating time: the firm's clock


_DUR = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def _duration(v: str) -> dt.timedelta | None:
    m = _DUR.fullmatch(v.strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    td = dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -td if m.group(1) == "-" else td


def _person(value: str, params: dict) -> tuple[str, str]:
    email = re.sub(r"^mailto:", "", value.strip(), flags=re.I).strip().lower()
    return unescape(params.get("CN", "")).strip(), email


def parse(text: str) -> list[Event]:
    events, cur, depth = [], None, 0
    for line in _unfold(text or ""):
        if not line:
            continue
        name, params, value = _split(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and cur is None:
                cur, depth = {"attendees": []}, 0
            elif cur is not None:
                depth += 1  # VALARM inside an event
            continue
        if name == "END":
            if cur is not None and depth:
                depth -= 1
            elif cur is not None and value.upper() == "VEVENT":
                if cur.get("uid") and cur.get("start"):
                    start, all_day = cur["start"]
                    end = cur.get("end")
                    if end is None and cur.get("duration") is not None:
                        end = start + cur["duration"]
                    if end is None and all_day:
                        end = start + dt.timedelta(days=1)
                    events.append(Event(
                        uid=cur["uid"], start=start, end=end, all_day=all_day, summary=cur.get("summary", ""),
                        location=cur.get("location", ""), description=cur.get("description", ""),
                        status=cur.get("status", ""), organizer=cur.get("organizer"), attendees=cur["attendees"],
                        recurrence_id=cur.get("recurrence_id", ""),
                    ))
                cur = None
            continue
        if cur is None or depth:
            continue
        try:
            if name == "UID":
                cur["uid"] = value.strip()
            elif name == "DTSTART":
                cur["start"] = _when(value, params)
            elif name == "DTEND":
                cur["end"] = _when(value, params)[0]
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "SUMMARY":
                cur["summary"] = unescape(value)[:300]
            elif name == "LOCATION":
                cur["location"] = unescape(value)[:300]
            elif name == "DESCRIPTION":
                cur["description"] = unescape(value)[:20000]
            elif name == "STATUS":
                cur["status"] = value.strip().upper()
            elif name == "ORGANIZER":
                cur["organizer"] = _person(value, params)
            elif name == "ATTENDEE":
                cur["attendees"].append(_person(value, params))
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = value.strip()
        except ValueError:
            continue
    return events


def _fmt(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def invitation(*, organizer: tuple[str, str], attendees: list[tuple[str, str]], start: dt.datetime,
               end: dt.datetime, summary: str, location: str = "", description: str = "") -> tuple[str, str]:
    """(uid, calendar text) for a new meeting with invitations."""
    uid = f"{uuid.uuid4()}@dealflow"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dealflow//Meetings//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_fmt(dt.datetime.now(UTC))}", f"DTSTART:{_fmt(start)}",
             f"DTEND:{_fmt(end)}", f"SUMMARY:{escape(summary)}"]
    if location:
        lines.append(f"LOCATION:{escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{escape(description)}")
    name, email = organizer
    lines.append(f'ORGANIZER;CN="{name.replace(chr(34), "")}":mailto:{email}')
    for n, e in attendees:
        cn = f'CN="{n.replace(chr(34), "")}";' if n else ""
        lines.append(f"ATTENDEE;{cn}ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{e}")
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    out = []
    for line in lines:  # fold at 75 octets
        b = line.encode()
        while len(b) > 75:
            cut = 75
            while cut and (b[cut] & 0xC0) == 0x80:
                cut -= 1
            out.append(b[:cut].decode())
            b = b" " + b[cut:]
        out.append(b.decode())
    return uid, "\r\n".join(out)
