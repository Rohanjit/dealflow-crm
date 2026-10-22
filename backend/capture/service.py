"""Contact capture (decision 64): people you've emailed who aren't in Dealflow, reading a signature,
and checking a captured person against Dealflow before saving (same person? which firm?)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from core.registry import get_module

from . import signature
from .models import Stranger

AUTOMATED = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|mailer-daemon|postmaster|bounces?|"
    r"newsletters?|news|marketing|updates?|info|support|help|billing|invoices?|accounts?|team|hello|admin|"
    r"calendar(-notification)?|reminders?|digest|feedback|careers|jobs|hr|sales|service|webmaster|system)"
    r"([+._-].*)?$", re.I)


def automated(addr: str) -> bool:
    local = addr.split("@", 1)[0]
    return bool(AUTOMATED.match(local)) or "bounce" in local or local.startswith("reply+")


def note_strangers(conn, msg, addrs: list[str]):
    """After a message is read: remember the people in it who aren't contacts or colleagues."""
    if msg.bulk or not addrs:
        return
    names = {e: n for n, e in [msg.sender, *msg.to, *msg.cc] if e}
    mine = msg.sender[1] == conn.email.lower() or msg.sender[1] == (conn.user.email or "").lower()
    for addr in addrs:
        if automated(addr) or len(addr) > 255:
            continue
        inbound = msg.sender[1] == addr
        s, created = Stranger.objects.get_or_create(
            user=conn.user, email=addr,
            defaults={"name": (names.get(addr) or "")[:200], "count": 1, "first_seen": msg.at, "last_seen": msg.at,
                      "last_subject": msg.subject[:300], "wrote_to_them": mine,
                      "connection": conn if inbound else None, "remote_id": msg.remote_id if inbound else ""})
        if created:
            continue
        s.count = F("count") + 1
        if msg.at >= s.last_seen:
            s.last_seen, s.last_subject = msg.at, msg.subject[:300]
        if names.get(addr) and not s.name:
            s.name = names[addr][:200]
        s.wrote_to_them = s.wrote_to_them or mine
        if inbound:
            s.connection, s.remote_id = conn, msg.remote_id[:1000]
        s.save()


def _contacts_emails(addrs) -> set[str]:
    from mail.service import contacts_by_email

    found = set()
    for c in contacts_by_email(list(addrs)):
        found |= {e.strip().lower() for e in (c.work_email, c.personal_email) if e}
    return found


def suggestions(user) -> list[dict]:
    rows = list(Stranger.objects.filter(user=user, dismissed=False)[:300])
    known = _contacts_emails([r.email for r in rows])
    if known:  # added since: drop them
        Stranger.objects.filter(user=user, email__in=known).delete()
    out = []
    for r in rows:
        if r.email in known:
            continue
        out.append({"id": r.pk, "email": r.email, "name": r.name, "count": r.count, "last_seen": r.last_seen.isoformat(),
                    "last_subject": r.last_subject, "wrote_to_them": r.wrote_to_them,
                    "firm": firm_matches(user, "", r.email)[:1]})
        if len(out) >= 100:
            break
    return out


def count(user) -> int:
    return Stranger.objects.filter(user=user, dismissed=False).count()


def dismiss(user, pk):
    n = Stranger.objects.filter(user=user, pk=pk).update(dismissed=True)
    if not n:
        raise NotFound("This suggestion doesn't exist.")


def prefill(user, pk) -> dict:
    """Read their latest email live (owner only) and suggest details from the signature."""
    s = Stranger.objects.filter(user=user, pk=pk).select_related("connection").first()
    if s is None:
        raise NotFound("This suggestion doesn't exist.")
    values = {"full_name": s.name, "work_email": s.email}
    found = {}
    if s.connection_id and s.remote_id and s.connection.user_id == user.pk:
        from mail.providers import MailError
        from sync.providers import source

        class _Copy:
            remote_id = s.remote_id
            extra: dict = {}

        try:
            found = signature.parse(source(s.connection).open_message(_Copy()).get("text", ""), s.name, s.email)
        except MailError:
            found = {}
    values.update({k: v for k, v in found.items() if v})
    return values


# ---------------------------------------------------------------- checking against Dealflow


def _domain(url_or_email: str) -> str:
    v = (url_or_email or "").strip().lower()
    if "@" in v and "/" not in v:
        return v.split("@", 1)[1]
    if "://" not in v:
        v = "https://" + v
    host = (urlparse(v).hostname or "")
    return host[4:] if host.startswith("www.") else host


def firm_matches(user, name: str, email_or_site: str = "", website: str = "") -> list[dict]:
    """Investors and companies with the same website domain or (normalised) name."""
    from core.importer import _existing_keys
    from sync.engine import PUBLIC_DOMAINS

    out, seen = [], set()
    domains = {d for d in (_domain(email_or_site), _domain(website)) if d and d not in PUBLIC_DOMAINS}
    for mkey, name_key in (("investors", "firm_name"), ("companies", "name")):
        if not user.can(mkey, "view"):
            continue
        mod = get_module(mkey)
        index = _existing_keys(mod, user)
        keys = [f"web:{d}" for d in domains]
        if name:
            keys += [k for k in mod.duplicate_keys({name_key: name}) if k.startswith("name:")]
        for k in keys:
            for pk, title in index.get(k, []):
                if (mkey, pk) not in seen:
                    seen.add((mkey, pk))
                    out.append({"module": mkey, "id": pk, "title": title,
                                "why": "Same website" if k.startswith("web:") else "Same name"})
    return out


CAPTURE_FIELDS = ("full_name", "designation", "work_email", "personal_email", "mobile", "office_phone", "linkedin",
                  "city")


def check(user, data: dict) -> dict:
    """Is this person already in Dealflow, what would change, and which firm do they belong to?"""
    if not user.can("contacts", "view"):
        raise PermissionDenied("Your role can't see contacts.")
    from core.importer import _existing_keys
    from core.records import serialize, visible

    values = {k: str(data.get(k) or "").strip()[:255] for k in CAPTURE_FIELDS if isinstance(data.get(k), (str, type(None)))}
    company = str(data.get("company") or "").strip()[:200]
    website = str(data.get("website") or "").strip()[:300]
    firms = firm_matches(user, company, values.get("work_email", ""), website)
    mod = get_module("contacts")
    index = _existing_keys(mod, user)
    probe = {**values, "firm": f"{firms[0]['module']}:{firms[0]['id']}" if firms else ""}
    matches = []
    for k in mod.duplicate_keys(probe):
        for pk, title in index.get(k, []):
            if pk not in [m["id"] for m in matches]:
                matches.append({"id": pk, "title": title, "why": k.split(":", 1)[0]})
    existing = None
    if matches:
        obj = visible(mod.model.objects.filter(pk=matches[0]["id"]), mod, user).first()
        if obj is not None:
            rec = serialize(obj, mod)
            changes = []
            for k in CAPTURE_FIELDS:
                new = values.get(k) or ""
                old = rec.get(k) or ""
                if new and str(old).strip().lower() != new.lower() and k in rec:
                    changes.append({"field": k, "label": mod.field_map[k].label, "from": old, "to": new})
            existing = {"id": obj.pk, "title": mod.title_of(obj), "firm": rec.get("firm"), "changes": changes,
                        "why": matches[0]["why"]}
    can_new_firm = {"investors": user.can("investors", "create"),
                    "companies": user.can("outreach", "create") or user.can("deals", "create")}
    return {"existing": existing, "others": matches[1:5], "firms": firms, "company": company, "website": website,
            "can": {"create": user.can("contacts", "create"), "edit": user.can("contacts", "edit"), "new_firm": can_new_firm}}


@transaction.atomic
def add_firm(user, kind: str, name: str, website: str = "") -> dict:
    """Add the firm in the same step (decision 64), through the normal create with its checks and stamps."""
    from core.records import apply_values, clean_values
    from core.registry import enabled_map

    if kind not in ("investors", "companies"):
        raise ValidationError({"kind": "Pick investor or company."})
    mkey = kind
    allowed = user.can("investors", "create") if kind == "investors" else (user.can("outreach", "create")
                                                                           or user.can("deals", "create"))
    if not allowed:
        raise PermissionDenied("Your role can't add firms of this kind.")
    mod = get_module(mkey)
    data = {("firm_name" if kind == "investors" else "name"): name}
    if website:
        data["website"] = website
    values, errors = clean_values(mod, data, enabled=enabled_map(mkey))
    if errors and "website" in errors:
        data.pop("website")
        values, errors = clean_values(mod, data, enabled=enabled_map(mkey))
    if errors:
        raise ValidationError(errors)
    obj = apply_values(mod.model(), mod, values, user)
    return {"module": mkey, "id": obj.pk, "title": mod.title_of(obj)}
