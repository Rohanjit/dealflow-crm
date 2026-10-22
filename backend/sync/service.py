"""Email and calendar logging for the API: connecting, what each person may see of a logged item,
opening an email live, saving an attachment to the Vault, picking the deal, agendas and scheduling."""

from __future__ import annotations

import datetime as dt
import re

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from core import access, current
from core.records import visible
from core.registry import get_module
from mail import crypto
from mail.providers import MailError

from . import engine, providers
from .models import Connection, Copy, Item, Prefs

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REMINDER_CHOICES = (None, 5, 10, 15, 30, 60)


# ---------------------------------------------------------------- connections


def connection_payload(c: Connection) -> dict:
    s = providers.secret(c) if c.kind == "other" else {}
    return {
        "id": c.pk, "kind": c.kind, "label": c.get_kind_display(), "email": c.email, "mail": c.mail,
        "calendar": c.calendar, "connected_at": c.connected_at.isoformat(),
        "synced_at": c.synced_at.isoformat() if c.synced_at else None,
        "needs_reconnect": c.needs_reconnect, "problem": c.last_error,
        **({"imap_host": s.get("imap_host", ""), "imap_port": s.get("imap_port", 993),
            "caldav_url": s.get("caldav_url", "")} if c.kind == "other" else {}),
    }


def start(conn: Connection) -> Connection:
    """Set the reading points at "now" (nothing older is ever logged) and save."""
    src = providers.source(conn)
    if isinstance(src, providers.PasswordSource):
        src.verify()
    src.start()
    conn.synced_at = None
    conn.needs_reconnect, conn.last_error = False, ""
    conn.save()
    return conn


@transaction.atomic
def connect_oauth(user, kind: str, tokens: dict, email: str, mail: bool, calendar: bool) -> Connection:
    conn = Connection.objects.select_for_update().filter(user=user, kind=kind).first()
    if conn and conn.email == email and conn.mail == mail and conn.calendar == calendar and \
            (conn.mail_cursor or conn.calendar_cursor or not mail):
        # Reconnecting the same account: carry on from where reading stopped.
        conn.secret, conn.needs_reconnect, conn.last_error, conn.synced_at = crypto.seal(tokens), False, "", None
        conn.save()
        return conn
    conn = conn or Connection(user=user, kind=kind)
    conn.email, conn.secret, conn.mail, conn.calendar = email, crypto.seal(tokens), mail, calendar
    conn.mail_cursor, conn.calendar_cursor = {}, {}
    conn.connected_at = timezone.now()
    conn.save()
    return start(conn)


def _text(data, key, limit=300) -> str:
    v = data.get(key)
    return v.strip()[:limit] if isinstance(v, str) else ""


def connect_password(user, kind: str, data: dict) -> Connection:
    mail, calendar = data.get("mail") is not False, data.get("calendar") is not False
    if not (mail or calendar):
        raise ValidationError({"detail": "Pick email, calendar or both."})
    email = _text(data, "email", 255).lower()
    if not EMAIL_RE.match(email):
        raise ValidationError({"email": "Enter the email address of the account."})
    password = data.get("password")
    if not isinstance(password, str) or not password.strip() or len(password) > 200:
        raise ValidationError({"password": "Enter the app password."})
    if kind == "icloud":
        cfg = dict(providers.ICLOUD)
    else:
        cfg = {"imap_host": _text(data, "imap_host", 253), "caldav_url": _text(data, "caldav_url", 500)}
        try:
            cfg["imap_port"] = int(data.get("imap_port") or 993)
        except (TypeError, ValueError):
            raise ValidationError({"imap_port": "Enter the port number (usually 993)."})
        if not 1 <= cfg["imap_port"] <= 65535:
            raise ValidationError({"imap_port": "Enter the port number (usually 993)."})
        if mail and not cfg["imap_host"]:
            raise ValidationError({"imap_host": "Enter the mail (IMAP) server, for example imap.yourfirm.com."})
        if calendar and not cfg["caldav_url"].startswith("https://"):
            raise ValidationError({"caldav_url": "Enter the calendar (CalDAV) address, starting with https://."})
    secret = {"username": _text(data, "username", 255) or email, "password": password.strip(), **cfg}
    with transaction.atomic():
        conn, _ = Connection.objects.select_for_update().get_or_create(
            user=user, kind=kind, defaults={"email": email, "secret": crypto.seal(secret)})
        conn.email, conn.secret, conn.mail, conn.calendar = email, crypto.seal(secret), mail, calendar
        conn.mail_cursor, conn.calendar_cursor = {}, {}
        conn.connected_at = timezone.now()
        try:
            start(conn)
        except MailError as e:
            transaction.set_rollback(True)
            raise ValidationError({"detail": str(e), "code": "sync_failed"})
    return conn


def disconnect(conn: Connection):
    providers.revoke(conn)
    current.note(f"Disconnected {conn.get_kind_display()} logging for {conn.email}")
    conn.delete()  # logged items stay (they're the firm's records); the email text can't be opened any more


def mine(user) -> dict:
    p = Prefs.for_user(user)
    from mail.service import app_config

    return {
        "connections": [connection_payload(c) for c in Connection.objects.filter(user=user)],
        "reminder_minutes": p.reminder_minutes,
        "ready": {"google": bool(app_config("gmail")), "microsoft": bool(app_config("outlook")),
                  "icloud": True, "other": True},
    }


def overview() -> list[dict]:
    """Settings > Email (admins): who has connected logging."""
    from accounts.models import User

    conns = {}
    for c in Connection.objects.select_related("user"):
        conns.setdefault(c.user_id, []).append(c)
    out = []
    for u in User.objects.filter(is_active=True).order_by("full_name", "email"):
        cs = conns.get(u.pk, [])
        out.append({"id": u.pk, "name": u.full_name or u.email, "email": u.email,
                    "connections": [{"label": c.get_kind_display(), "email": c.email, "mail": c.mail,
                                     "calendar": c.calendar, "ok": not c.needs_reconnect and not c.last_error,
                                     "synced_at": c.synced_at.isoformat() if c.synced_at else None} for c in cs]})
    return out


# ---------------------------------------------------------------- what each person sees


def _record_visible(ref: str, user) -> bool:
    mkey, _, rid = ref.partition(":")
    try:
        mod = get_module(mkey)
    except KeyError:
        return False
    if not user.can(mkey, "view"):
        return False
    return visible(mod.model.objects.filter(pk=int(rid), archived_at__isnull=True), mod, user).exists()


def restricted_for(item: Item, user) -> bool:
    """The item is about a deal this person can't see (a restricted deal): its subject is withheld."""
    return any(not _record_visible(r, user) for r in item.records if r.split(":")[0] in engine.DEAL_KEYS)


def can_open(item: Item, user) -> bool:
    return Copy.objects.filter(item=item, connection__user=user).exists()


def participants_for(parts: list[dict], user) -> list[dict]:
    hide_email = bool({"work_email", "personal_email"} & access.hidden(get_module("contacts"), user))
    out = []
    for p in parts:
        q = {k: p.get(k) for k in ("name", "email", "contact", "role", "colleague")}
        if hide_email and p.get("contact"):
            q["email"] = ""
        out.append(q)
    return out


def note_meta(meta: dict, user) -> tuple[dict, dict]:
    """(meta to send, extras) for a synced log entry."""
    item = Item.objects.filter(pk=meta.get("synced")).first()
    out = {**meta, "participants": participants_for(meta.get("participants") or [], user)}
    extra = {"can_open": False, "choices": []}
    if item is None:
        return out, extra
    if restricted_for(item, user):
        out["subject"] = "About a restricted deal"
        out["attachments"] = []
        out["restricted"] = True
        return out, extra
    extra["can_open"] = can_open(item, user)
    if extra["can_open"] and item.choices:
        extra["choices"] = choices_payload(item, user)
    return out, extra


def choices_payload(item: Item, user) -> list[dict]:
    out = []
    for ref in item.choices:
        mkey, _, rid = ref.partition(":")
        mod = get_module(mkey)
        obj = visible(mod.model.objects.filter(pk=int(rid)), mod, user).first()
        if obj is not None:
            out.append({"ref": ref, "module": mkey, "id": obj.pk, "title": mod.title_of(obj),
                        "kind": {"deals": "Deal", "outreach": "Outreach", "deal_investors": "Tracker"}[mkey]})
    return out


def links_for(item: Item, user, limit=3) -> list[dict]:
    """The records it's logged on that this person can open (deals first), for links in lists."""
    order = {"deals": 0, "outreach": 1, "deal_investors": 2, "contacts": 3, "investors": 4, "companies": 5}
    out = []
    for ref in sorted(item.records, key=lambda r: order.get(r.split(":")[0], 9)):
        mkey, _, rid = ref.partition(":")
        if not user.can(mkey, "view"):
            continue
        mod = get_module(mkey)
        obj = visible(mod.model.objects.filter(pk=int(rid), archived_at__isnull=True), mod, user).first()
        if obj is None:
            continue
        if out and out[0]["module"] in engine.DEAL_KEYS and mkey not in engine.DEAL_KEYS:
            break  # the deal says it all; its company and people are on its page
        title = mod.title_of(obj)
        if all(o["title"] != title for o in out):
            out.append({"module": mkey, "id": obj.pk, "title": title})
        if len(out) >= limit:
            break
    return out


def item_payload(item: Item, user) -> dict:
    restricted = restricted_for(item, user)
    return {
        "links": [] if restricted else links_for(item, user),
        "id": item.pk, "kind": item.kind, "subject": "About a restricted deal" if restricted else item.subject,
        "at": item.at.isoformat(), "end": item.end.isoformat() if item.end else None, "all_day": item.all_day,
        "participants": participants_for(item.participants, user), "cancelled": item.cancelled,
        "attachments": [] if restricted else [a.get("name") for a in item.attachments],
        "can_open": not restricted and can_open(item, user), "restricted": restricted,
        "choices": choices_payload(item, user) if item.choices and can_open(item, user) else [],
    }


def _item_for(user, pk) -> tuple[Item, Copy]:
    item = Item.objects.filter(pk=pk).first()
    if item is None:
        raise NotFound("This email or meeting doesn't exist.")
    copy = Copy.objects.filter(item=item, connection__user=user).select_related("connection").first()
    if copy is None:
        raise PermissionDenied("Only the person whose mailbox or calendar it's in can open this.")
    return item, copy


def open_item(user, pk) -> dict:
    item, copy = _item_for(user, pk)
    try:
        src = providers.source(copy.connection)
        detail = src.open_message(copy) if item.kind == "email" else src.open_event(copy)
    except MailError as e:
        raise ValidationError({"detail": str(e), "code": "sync_failed"})
    current.note(f"Opened {'email' if item.kind == 'email' else 'meeting'} “{item.subject[:100]}”")
    return {**item_payload(item, user), **detail}


# ---------------------------------------------------------------- attachments to the Vault


def _vault_tags(item: Item) -> list[str]:
    from deals.models import DealInvestor

    tags = []
    for ref in item.records:
        mkey, _, rid = ref.partition(":")
        if mkey in ("companies", "outreach", "deals", "investors"):
            tags.append(ref)
        elif mkey == "deal_investors":
            e = DealInvestor.objects.filter(pk=int(rid)).first()
            if e:
                tags += [f"deals:{e.deal_id}", f"investors:{e.investor_id}"]
    return list(dict.fromkeys(tags))


def save_attachment(request, pk, index: int, title: str = "") -> dict:
    from django.core.files.base import ContentFile
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core import antivirus
    from core.records import apply_values, clean_values, serialize
    from core.registry import enabled_map
    from vault import crypto as vcrypto
    from vault import keyring
    from vault import pin as vp
    from vault.files import FileRejected, check, clean_name
    from vault.models import Document

    user = request.user
    if not user.can("vault", "create"):
        raise PermissionDenied("Your role can't add to the Vault.")
    vp.require_open(request)
    item, copy = _item_for(user, pk)
    if item.kind != "email" or not 0 <= index < len(item.attachments):
        raise ValidationError({"detail": "That attachment isn't on this email."})
    try:
        name, ctype, data = providers.source(copy.connection).attachment(copy, index)
    except MailError as e:
        raise ValidationError({"detail": str(e), "code": "sync_failed"})
    f = SimpleUploadedFile(clean_name(name), data, content_type=ctype or "application/octet-stream")
    try:
        ext, kind, sha = check(f)
    except FileRejected as e:
        raise ValidationError({"detail": str(e)})
    antivirus.check_upload(f, name=clean_name(name), place="email", user=user)
    mod = get_module("vault")
    enabled = enabled_map("vault")
    base = title.strip() if title and title.strip() else (name.rpartition(".")[0] or name)
    values, errors = clean_values(mod, {"title": base[:255], "tagged": _vault_tags(item),
                                        "description": f"Attachment from the email “{item.subject[:200]}”"},
                                  enabled=enabled)
    if errors:
        values, errors = clean_values(mod, {"title": base[:255]}, enabled=enabled)
        if errors:
            raise ValidationError(errors)
    key_id, key = keyring.require_key(request)
    with transaction.atomic():
        f.seek(0)
        blob = vcrypto.encrypt_file(f.read(), key, vcrypto.file_aad(sha, key_id))
        doc = Document(file=ContentFile(blob, name=f"upload.{ext}"), file_name=clean_name(name), file_type=kind.label,
                       file_size=f.size, content_type=kind.content_type, sha256=sha, encrypted_with_id=key_id)
        doc._ext = ext
        doc = apply_values(doc, mod, values, user)
    current.note(f"Saved attachment “{clean_name(name)}” to the Vault")
    return serialize(doc, mod)


# ---------------------------------------------------------------- picking the deal


def file_item(user, pk, ref: str | None) -> dict:
    item, _ = _item_for(user, pk)
    if not item.choices:
        raise ValidationError({"detail": "This is already filed."})
    if ref is not None and ref not in item.choices:
        raise ValidationError({"detail": "Pick one of the deals offered."})
    if ref is not None and not _record_visible(ref, user):
        raise PermissionDenied("You can't see that deal.")
    with transaction.atomic():
        item.choices, item.filed_by = [], user
        if ref:
            item.records = list(dict.fromkeys(item.records + [ref]))
        item.save(update_fields=["choices", "filed_by", "records"])
        if ref and item.logged:
            engine.log_item(item, [ref])
    return item_payload(item, user)


# ---------------------------------------------------------------- meetings: agenda, record pages


def _meetings(qs, user) -> list[dict]:
    out = []
    for item in qs:
        if not any(_record_visible(r, user) for r in item.records):
            continue
        out.append(item_payload(item, user))
    return out


def agenda(user, now=None) -> dict:
    """My upcoming meetings (7 days), which ones to remind about now, and items waiting for a deal."""
    now = now or timezone.now()
    p = Prefs.for_user(user)
    mine_q = Item.objects.filter(copies__connection__user=user).distinct()
    upcoming = list(mine_q.filter(kind="meeting", cancelled=False, end__gt=now,
                                  at__lte=now + dt.timedelta(days=7)).order_by("at")[:50])
    meetings = _meetings(upcoming, user)
    soon = []
    if p.reminder_minutes:
        lead = dt.timedelta(minutes=p.reminder_minutes)
        soon = [m["id"] for m in meetings if not m.get("all_day")
                and now >= dt.datetime.fromisoformat(m["at"]) - lead and now < dt.datetime.fromisoformat(m["at"])]
    to_file = [item_payload(i, user) for i in mine_q.exclude(choices=[]).order_by("-at")[:20]]
    return {"now": now.isoformat(), "meetings": meetings, "soon": soon, "reminder_minutes": p.reminder_minutes,
            "to_file": [x for x in to_file if x["choices"]]}


def meetings_about(user, mkey: str, pk: int, now=None) -> list[dict]:
    now = now or timezone.now()
    mod = get_module(mkey)
    if not user.can(mkey, "view") or not visible(mod.model.objects.filter(pk=pk), mod, user).exists():
        raise NotFound
    ref = f"{mkey}:{pk}"
    refs = [ref]
    if mkey == "deals":  # its tracker entries and the company's people count too
        from deals.models import DealInvestor

        refs += [f"deal_investors:{i}" for i in DealInvestor.objects.filter(deal_id=pk).values_list("pk", flat=True)]
    qs = Item.objects.filter(kind="meeting", cancelled=False, end__gt=now).order_by("at")
    # Meetings waiting for the owner to pick between deals show on each deal they could be about.
    items = [i for i in qs[:500] if set(refs) & (set(i.records) | set(i.choices))][:20]
    return [item_payload(i, user) for i in items if not restricted_for(i, user)]


# ---------------------------------------------------------------- scheduling


def _about(raw):
    from mail.service import about_record

    try:
        return about_record(raw, current.user(), "edit")
    except PermissionError as e:
        raise PermissionDenied(str(e))
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise ValidationError({"about": str(e)})


def schedule(user, data: dict) -> dict:
    conns = [c for c in Connection.objects.filter(user=user, calendar=True, needs_reconnect=False)]
    if not conns:
        raise ValidationError({"detail": "Connect your calendar first, in Settings > My preferences.",
                               "code": "no_calendar"})
    conn = next((c for c in conns if str(c.pk) == str(data.get("connection"))), conns[0])
    mod, obj = _about(data.get("about"))
    title = _text(data, "title", 300)
    if not title:
        raise ValidationError({"title": "Give the meeting a title."})
    try:
        start = dt.datetime.fromisoformat(str(data.get("start") or ""))
    except ValueError:
        raise ValidationError({"start": "Pick a date and time."})
    if start.tzinfo is None:
        start = timezone.make_aware(start)
    try:
        minutes = int(data.get("minutes") or 30)
    except (TypeError, ValueError):
        minutes = 0
    if not 5 <= minutes <= 12 * 60:
        raise ValidationError({"minutes": "Pick a length between 5 minutes and 12 hours."})
    if start < timezone.now() - dt.timedelta(minutes=5):
        raise ValidationError({"start": "Pick a time that hasn't passed."})
    raw = data.get("attendees")
    if not isinstance(raw, list) or not raw or len(raw) > 50:
        raise ValidationError({"attendees": "Add who to invite."})
    attendees = []
    for a in raw:
        e = (a.get("email") if isinstance(a, dict) else a) or ""
        n = (a.get("name") if isinstance(a, dict) else "") or ""
        e = str(e).strip().lower()
        if not EMAIL_RE.match(e):
            raise ValidationError({"attendees": f"“{e[:100]}” isn't an email address."})
        attendees.append((str(n)[:120], e))
    try:
        ev = providers.source(conn).create_event(
            summary=title, start=start, end=start + dt.timedelta(minutes=minutes), attendees=attendees,
            location=_text(data, "location", 300), description=_text(data, "notes", 5000))
    except MailError as e:
        raise ValidationError({"detail": str(e), "code": "sync_failed"})
    item = engine.ingest_event(conn, ev)
    if item is not None and f"{mod.key}:{obj.pk}" not in item.records and mod.key in engine.DEAL_KEYS:
        item.records = item.records + [f"{mod.key}:{obj.pk}"]
        item.choices, item.filed_by = [], user
        item.save(update_fields=["records", "choices", "filed_by"])
    current.note(f"Scheduled “{title[:100]}” with {', '.join(e for _, e in attendees)[:200]}")
    return item_payload(item, user) if item else {"subject": title, "at": start.isoformat(), "logged": False}


def set_reminder(user, minutes) -> dict:
    if minutes not in REMINDER_CHOICES:
        raise ValidationError({"reminder_minutes": "Pick off, 5, 10, 15, 30 or 60 minutes."})
    p = Prefs.for_user(user)
    p.reminder_minutes = minutes
    p.save(update_fields=["reminder_minutes"])
    return {"reminder_minutes": minutes}

