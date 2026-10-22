"""What gets logged, and where (decision 64).

- Only emails and meetings with someone already in Dealflow (a contact's work or personal email).
- Mail only among colleagues (people with a Dealflow account, or the firm's own email domain) is skipped.
- Nothing from before the person connected.
- Logged on each matched contact and their firm; also on their open deal (or outreach, or the
  investor's entry on a deal's tracker) when there's exactly one. With several, the owner is asked
  to pick (`Item.choices`).
- Emails are logged straight away; meetings once they've ended. Last contacted moves on.
- An email sent from Dealflow's own composer is already logged: its copy is remembered, not repeated.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import Note
from core.records import visible
from core.registry import get_module
from mail.providers import MailError

from .models import Connection, Copy, Item
from .providers import RemoteEvent, RemoteMessage, source

log = logging.getLogger("dealflow.sync")

EVERY = dt.timedelta(minutes=5)      # how often each connection is read
AHEAD = dt.timedelta(days=60)        # meetings looked at ahead of today
BEHIND = dt.timedelta(days=2)        # and behind (changes to meetings that just happened)
PER_TICK = 10                        # connections read per scheduler minute
DEAL_KEYS = ("deals", "outreach", "deal_investors")

PUBLIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "yahoo.co.in",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "zoho.com", "zohomail.in",
    "gmx.com", "gmx.net", "rediffmail.com", "yandex.com", "mail.com", "fastmail.com", "hey.com",
}


# ---------------------------------------------------------------- who's who


def colleagues() -> tuple[set[str], set[str]]:
    """(addresses, domains) of the firm: everyone with a Dealflow account and their mailboxes, and their
    email domains unless those are public ones like gmail.com."""
    from accounts.models import User

    addrs = {e.lower() for e in User.objects.filter(is_active=True).values_list("email", flat=True) if e}
    addrs |= {e.lower() for e in Connection.objects.values_list("email", flat=True) if e}
    from mail.models import MailAccount

    addrs |= {e.lower() for e in MailAccount.objects.values_list("email", flat=True) if e}
    domains = {a.split("@", 1)[1] for a in addrs if "@" in a} - PUBLIC_DOMAINS
    return addrs, domains


def is_colleague(addr: str, firm: tuple[set[str], set[str]]) -> bool:
    addr = addr.lower()
    return addr in firm[0] or (addr.split("@", 1)[-1] in firm[1])


def contacts_for(addrs: list[str]) -> dict[str, object]:
    from mail.service import contacts_by_email

    out = {}
    for c in contacts_by_email(addrs):
        for e in (c.work_email, c.personal_email):
            if e and e.strip().lower() in addrs and e.strip().lower() not in out:
                out[e.strip().lower()] = c
    return out


def firm_of(contact) -> str | None:
    if contact.investor_id and contact.investor and contact.investor.archived_at is None:
        return f"investors:{contact.investor_id}"
    if contact.company_id and contact.company and contact.company.archived_at is None:
        return f"companies:{contact.company_id}"
    return None


def open_targets(contact, user) -> list[str]:
    """The open deals, outreach attempts and tracker entries a contact belongs to, that `user` can see."""
    from deals.models import Deal, DealInvestor
    from outreach.models import Outreach

    out = []
    if contact.company_id:
        for key, qs in (
            ("outreach", Outreach.objects.filter(company_id=contact.company_id, archived_at__isnull=True,
                                                 status="open").exclude(stage="won")),
            ("deals", Deal.objects.filter(company_id=contact.company_id, archived_at__isnull=True, status="open")
             .exclude(stage="closed")),
        ):
            mod = get_module(key)
            if user is not None and not user.can(key, "view"):
                continue
            out += [f"{key}:{pk}" for pk in visible(qs, mod, user).values_list("pk", flat=True)]
    if contact.investor_id:
        mod = get_module("deal_investors")
        if user is None or user.can("deals", "view"):
            qs = DealInvestor.objects.filter(investor_id=contact.investor_id, archived_at__isnull=True, status="open",
                                             deal__archived_at__isnull=True, deal__status="open").exclude(
                deal__stage="closed")
            out += [f"deal_investors:{pk}" for pk in visible(qs, mod, user).values_list("pk", flat=True)]
    return out


def place(contacts: list, user) -> tuple[list[str], list[str]]:
    """(records to log on, choices to ask about)."""
    records, targets = [], []
    for c in contacts:
        for r in (f"contacts:{c.pk}", firm_of(c)):
            if r and r not in records:
                records.append(r)
        for t in open_targets(c, user):
            if t not in targets:
                targets.append(t)
    if len(targets) == 1:
        return records + targets, []
    return records, targets


def _participants(pairs: list[tuple[str, str, str]], matched: dict, firm) -> list[dict]:
    from accounts.models import User

    users = {u.email.lower(): u for u in User.objects.filter(email__in=[e for _, e, _ in pairs])}
    out, seen = [], set()
    for name, addr, role in pairs:
        if not addr or addr in seen:
            continue
        seen.add(addr)
        c, u = matched.get(addr), users.get(addr)
        out.append({"name": (c.full_name if c else (u.full_name if u else name)) or addr, "email": addr,
                    "contact": c.pk if c else None, "user": u.pk if u else None, "role": role,
                    "colleague": is_colleague(addr, firm)})
    return out


# ---------------------------------------------------------------- logging


def log_item(item: Item, records: list[str]):
    """Write the log entries (one per record that keeps a log) and move Last contacted."""
    from mail.service import mark_contacted

    meta = {"synced": item.pk, "subject": item.subject, "end": item.end.isoformat() if item.end else None,
            "all_day": item.all_day, "attachments": [a.get("name") for a in item.attachments],
            "participants": [{k: p.get(k) for k in ("name", "email", "contact", "role", "colleague")}
                             for p in item.participants]}
    day = timezone.localtime(item.at).date()
    notes = []
    for r in records:
        mkey, _, rid = r.partition(":")
        try:
            mod = get_module(mkey)
        except KeyError:
            continue
        if not mod.timeline or Note.objects.filter(module=mkey, record_id=int(rid), meta__synced=item.pk).exists():
            continue
        notes.append(Note(module=mkey, record_id=int(rid), kind=item.kind, date=day, text="", meta=meta,
                          created_by=item.owner))
    Note.objects.bulk_create(notes)
    contacts = [p["contact"] for p in item.participants if p.get("contact")]
    if contacts and item.at <= timezone.now():
        mark_contacted(contacts, day, item.owner)


def _remember(conn: Connection, item: Item, remote_id: str, extra: dict):
    Copy.objects.update_or_create(connection=conn, remote_id=remote_id[:1000], defaults={"item": item, "extra": extra})


def ingest_message(conn: Connection, m: RemoteMessage, firm=None) -> Item | None:
    if m.at < conn.connected_at - dt.timedelta(minutes=5):
        return None
    firm = firm or colleagues()
    pairs = [(m.sender[0], m.sender[1], "from")] + [(n, e, "to") for n, e in m.to] + [(n, e, "cc") for n, e in m.cc]
    outside = [e for _, e, _ in pairs if e and not is_colleague(e, firm)]
    if not outside:
        return None  # colleagues only
    matched = contacts_for(outside)
    if not matched:
        return None  # nobody in Dealflow
    key = ("e:" + m.message_id.strip("<> ").lower()) if m.message_id else f"e:{conn.pk}:{m.remote_id}"
    item = Item.objects.filter(key=key[:500]).first()
    if item:
        _remember(conn, item, m.remote_id, m.extra)
        return item
    from mail.models import SentEmail

    from_dealflow = SentEmail.objects.filter(user=conn.user, message_id=m.remote_id).exists()
    contacts = list(dict.fromkeys(matched.values()))
    records, choices = ([], []) if from_dealflow else place(contacts, conn.user)
    try:
        with transaction.atomic():
            item = Item.objects.create(
                kind="email", key=key[:500], subject=m.subject[:300], at=m.at,
                participants=_participants(pairs, matched, firm), attachments=m.attachments[:50],
                records=records, choices=choices, owner=conn.user, logged=True,
            )
            _remember(conn, item, m.remote_id, m.extra)
            if not from_dealflow:
                log_item(item, records)
    except IntegrityError:  # a colleague's mailbox logged it a moment ago
        item = Item.objects.filter(key=key[:500]).first()
        if item:
            _remember(conn, item, m.remote_id, m.extra)
    return item


def ingest_event(conn: Connection, ev: RemoteEvent, firm=None) -> Item | None:
    firm = firm or colleagues()
    pairs = ([(ev.organizer[0], ev.organizer[1], "organizer")] if ev.organizer else []) + \
        [(n, e, "attendee") for n, e in ev.attendees]
    outside = [e for _, e, _ in pairs if e and not is_colleague(e, firm)]
    matched = contacts_for(outside) if outside else {}
    key = f"m:{ev.uid}:{ev.start.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"[:500]
    item = Item.objects.filter(key=key).first()
    if not matched:
        if item and not item.logged and item.copies.filter(connection=conn).exists():
            item.cancelled = True  # the contact was taken off the invitation
            item.save(update_fields=["cancelled"])
        return None
    end = ev.end or (ev.start + dt.timedelta(hours=1))
    if item:
        _remember(conn, item, ev.remote_id, ev.extra)
        if not item.logged:
            contacts = list(dict.fromkeys(matched.values()))
            records, choices = place(contacts, item.owner or conn.user)
            item.subject, item.at, item.end, item.all_day = ev.summary[:300], ev.start, end, ev.all_day
            item.participants, item.cancelled = _participants(pairs, matched, firm), ev.cancelled
            filed = [r for r in item.records if item.filed_by and r.split(":")[0] in DEAL_KEYS]
            item.records = list(dict.fromkeys(records + filed))
            if not item.filed_by:
                item.choices = choices
            item.save()
        return item
    if ev.cancelled:
        return None
    if end < conn.connected_at:
        return None  # before connecting
    contacts = list(dict.fromkeys(matched.values()))
    records, choices = place(contacts, conn.user)
    try:
        with transaction.atomic():
            item = Item.objects.create(
                kind="meeting", key=key, subject=ev.summary[:300], at=ev.start, end=end, all_day=ev.all_day,
                participants=_participants(pairs, matched, firm), records=records, choices=choices,
                owner=conn.user, logged=False,
            )
            _remember(conn, item, ev.remote_id, ev.extra)
    except IntegrityError:
        item = Item.objects.filter(key=key).first()
        if item:
            _remember(conn, item, ev.remote_id, ev.extra)
    return item


def finish_meetings(now=None) -> int:
    """Meetings that have ended are logged (which also starts the follow-up nudge)."""
    now = now or timezone.now()
    n = 0
    for item in Item.objects.filter(kind="meeting", logged=False, cancelled=False, end__lte=now)[:500]:
        with transaction.atomic():
            item.logged = True
            item.save(update_fields=["logged"])
            log_item(item, item.records)
        n += 1
    return n


# ---------------------------------------------------------------- reading a connection


def problem(conn: Connection, e: Exception):
    conn.last_error = str(e)[:300]
    if isinstance(e, MailError) and e.kind in ("auth", "setup"):
        conn.needs_reconnect = e.kind == "auth"
    conn.save(update_fields=["last_error", "needs_reconnect", "mail_cursor", "calendar_cursor", "synced_at"])


def sync_connection(conn: Connection, now=None) -> dict:
    """Read new mail and the calendar window once. Returns counts."""
    now = now or timezone.now()
    counts = {"emails": 0, "meetings": 0}
    conn.synced_at = now
    try:
        src = source(conn)
        firm = colleagues()
        if conn.mail:
            for m in src.new_messages():
                if ingest_message(conn, m, firm):
                    counts["emails"] += 1
        if conn.calendar:
            start, end = max(conn.connected_at, now - BEHIND), now + AHEAD
            seen = set()
            for ev in src.events(start, end):
                seen.add(ev.remote_id)
                if ingest_event(conn, ev, firm):
                    counts["meetings"] += 1
            # Meetings of this calendar in the window that are gone now were cancelled or moved.
            gone = Item.objects.filter(kind="meeting", logged=False, cancelled=False, at__gte=start, at__lte=end,
                                       copies__connection=conn).exclude(copies__remote_id__in=seen)
            for item in gone:
                if not item.copies.exclude(connection=conn).exists():
                    item.cancelled = True
                    item.save(update_fields=["cancelled"])
        conn.last_error, conn.needs_reconnect = "", False
        conn.save(update_fields=["last_error", "needs_reconnect", "mail_cursor", "calendar_cursor", "synced_at"])
    except MailError as e:
        problem(conn, e)
        counts["error"] = str(e)
    except Exception as e:  # never let one mailbox stop the rest
        log.exception("Reading %s failed", conn)
        problem(conn, e)
        counts["error"] = "Something went wrong reading this mailbox. It will be tried again."
    return counts


def tick(now=None) -> int:
    """Scheduler, every minute: read the connections that are due, then log meetings that ended."""
    now = now or timezone.now()
    due = Connection.objects.filter(needs_reconnect=False, user__is_active=True).exclude(synced_at__gt=now - EVERY)
    n = 0
    for conn in due.order_by(F("synced_at").asc(nulls_first=True))[:PER_TICK]:
        sync_connection(conn, now)
        n += 1
    finish_meetings(now)
    return n
