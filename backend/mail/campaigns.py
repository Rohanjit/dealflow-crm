"""Mail-merge campaigns: one email per person, from the sender's own mailbox, sent in the
background by the scheduler within the mailbox's daily and per-minute limits."""

from __future__ import annotations

import datetime as dt
import re

from django.db import transaction
from django.utils import timezone

from core.records import filter_queryset, visible
from core.registry import enabled_map, get_module

from . import service
from .models import Campaign, CampaignRecipient, MailSettings
from .providers import PROVIDERS, MailError

MAX_RECIPIENTS = 5000

MERGE_FIELDS = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("full_name", "Full name"),
    ("designation", "Designation"),
    ("firm", "Firm / company"),
    ("city", "City"),
    ("my_name", "Your name"),
    ("my_first_name", "Your first name"),
    ("my_email", "Your email"),
]
KNOWN = {k for k, _ in MERGE_FIELDS}
_FIELD = re.compile(r"\{\s*([A-Za-z_]+)\s*(?:\|([^{}\n]{0,80}))?\}")
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "shri", "smt", "sri", "ca", "cs", "adv"}


def _split(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    while len(parts) > 1 and parts[0].lower().rstrip(".") in _HONORIFICS:
        parts = parts[1:]
    if not parts:
        return "", ""
    return parts[0], (parts[-1] if len(parts) > 1 else "")


def merge_values(contact, sender) -> dict:
    first, last = _split(contact.full_name) if contact else ("", "")
    firm = ""
    if contact and contact.investor_id:
        firm = contact.investor.firm_name
    elif contact and contact.company_id:
        firm = contact.company.name
    return {
        "first_name": first, "last_name": last, "full_name": (contact.full_name if contact else ""),
        "designation": (contact.designation if contact else ""), "firm": firm,
        "city": (contact.city if contact else ""),
        "my_name": sender.full_name, "my_first_name": _split(sender.full_name)[0],
        "my_email": (service.account_payload(sender) or {}).get("email") or sender.email,
    }


def unknown_fields(*texts: str) -> list[str]:
    found = []
    for t in texts:
        for m in _FIELD.finditer(t or ""):
            if m.group(1).lower() not in KNOWN and m.group(1) not in found:
                found.append(m.group(1))
    return found


def used_fields(*texts: str) -> set[str]:
    return {m.group(1).lower() for t in texts for m in _FIELD.finditer(t or "") if m.group(2) is None}


def render(text: str, values: dict) -> str:
    """{first_name} → the value; {first_name|there} → "there" when it's empty."""

    def sub(m):
        key = m.group(1).lower()
        if key not in KNOWN:
            return m.group(0)
        v = (values.get(key) or "").strip()
        return v or (m.group(2) or "").strip()

    return _FIELD.sub(sub, text or "")


def opt_out_line() -> str:
    return MailSettings.load().opt_out_text.strip()


def compose(campaign: Campaign, values: dict) -> tuple[str, str]:
    subject = re.sub(r"\s+", " ", render(campaign.subject, values)).strip()
    body = render(campaign.body, values).rstrip()
    if line := opt_out_line():
        body += "\n\n—\n" + line
    return subject, body


# ---------------------------------------------------------------- who it goes to


def resolve_contacts(user, *, contact_ids=None, investor_ids=None, query=None, module="contacts", who="primary"):
    """Pick people: ticked contacts, ticked investors (their primary contacts, or all of them),
    or everything a list currently shows (`query` = its filters)."""
    from contacts.models import Contact
    from core.records import to_id

    if not user.can("contacts", "view"):
        raise PermissionError("Your role can't see Contacts, so it can't pick people for a campaign.")
    base = Contact.objects.filter(archived_at__isnull=True).select_related("investor", "company")
    if query is not None:
        if module not in ("contacts", "investors"):
            raise ValueError("Campaigns pick people from Contacts or Investors.")
        if not user.can(module, "view"):
            raise PermissionError(f"Your role can't see {get_module(module).label}.")
        mod = get_module(module)
        params = {k: v for k, v in query.items() if isinstance(k, str) and isinstance(v, str)}
        params.pop("page", None)
        params.pop("page_size", None)
        qs = filter_queryset(visible(mod.model.objects.filter(archived_at__isnull=True), mod, user), mod, params,
                             user, enabled_map(module))
        ids = list(qs.values_list("pk", flat=True)[:MAX_RECIPIENTS + 1])
        if module == "contacts":
            contact_ids = ids
        else:
            investor_ids = ids
    out, seen = [], set()
    if contact_ids:
        ids = [i for i in (to_id(x) for x in contact_ids) if i is not None]
        by_id = {c.pk: c for c in base.filter(pk__in=ids)}
        for i in ids:
            if i in by_id and i not in seen:
                seen.add(i)
                out.append(by_id[i])
    if investor_ids:
        if not user.can("investors", "view"):
            raise PermissionError("Your role can't see Investors.")
        ids = [i for i in (to_id(x) for x in investor_ids) if i is not None]
        people = list(base.filter(investor_id__in=ids).order_by("-primary_contact", "full_name"))
        for inv in ids:
            mine = [c for c in people if c.investor_id == inv]
            if who == "primary" and any(c.primary_contact for c in mine):
                mine = [c for c in mine if c.primary_contact]
            for c in mine:
                if c.pk not in seen:
                    seen.add(c.pk)
                    out.append(c)
    return out


def skip_reason(contact, email: str, taken: set[str]) -> str:
    if not email:
        return "No email address"
    if contact.email_opt_out:
        return "Marked “No campaign emails”"
    if email.lower() in taken:
        return "Same email as another person on this campaign"
    return ""


@transaction.atomic
def add_recipients(campaign: Campaign, contacts: list) -> dict:
    existing = {r.contact_id for r in campaign.recipients.all()}
    taken = {e.lower() for e in campaign.recipients.exclude(status="skipped").values_list("email", flat=True)}
    count = campaign.recipients.count()
    rows, added, skipped = [], 0, 0
    for c in contacts:
        if c.pk in existing:
            continue
        if count + len(rows) >= MAX_RECIPIENTS:
            raise ValueError(f"A campaign can go to at most {MAX_RECIPIENTS:,} people.")
        email = service.contact_email(c)
        reason = skip_reason(c, email, taken)
        if not reason:
            taken.add(email.lower())
        rows.append(CampaignRecipient(campaign=campaign, contact=c, email=email or "", name=c.full_name,
                                      status="skipped" if reason else "queued", reason=reason))
        existing.add(c.pk)
        skipped += bool(reason)
        added += not reason
    CampaignRecipient.objects.bulk_create(rows)
    return {"added": added, "skipped": skipped}


def counts(campaign: Campaign) -> dict:
    out = {"queued": 0, "sending": 0, "sent": 0, "failed": 0, "skipped": 0}
    from django.db.models import Count

    for row in campaign.recipients.values("status").annotate(n=Count("pk")):
        out[row["status"]] = row["n"]
    out["total"] = sum(out.values())
    return out


def problems(campaign: Campaign) -> list[str]:
    """What stops it being started."""
    out = []
    if not campaign.subject.strip():
        out.append("Write a subject.")
    if not campaign.body.strip():
        out.append("Write the email.")
    if bad := unknown_fields(campaign.subject, campaign.body):
        out.append("Unknown merge fields: " + ", ".join("{" + b + "}" for b in bad) + ".")
    if not campaign.recipients.filter(status="queued").exists():
        out.append("Add at least one person with an email address.")
    return out


# ---------------------------------------------------------------- sending


def _pause(c: Campaign, reason: str, resume_at=None):
    c.status, c.paused_reason, c.resume_at = "paused", reason[:300], resume_at
    c.save(update_fields=["status", "paused_reason", "resume_at"])


def _tomorrow():
    t = timezone.localtime() + dt.timedelta(days=1)
    return t.replace(hour=0, minute=5, second=0, microsecond=0)


def tick(now=None) -> int:
    """Scheduler, every minute: wake scheduled and resting campaigns, then send the next few
    emails of each. Returns how many went out."""
    now = now or timezone.now()
    Campaign.objects.filter(status="scheduled", send_at__lte=now).update(status="sending")
    Campaign.objects.filter(status="paused", resume_at__lte=now).update(status="sending", paused_reason="",
                                                                         resume_at=None)
    # A send cut off half-way (the scheduler stopped): we can't know whether it left.
    CampaignRecipient.objects.filter(status="sending", sent_at__lt=now - dt.timedelta(minutes=10)).update(
        status="failed", reason="Interrupted while sending; check your Sent folder before sending again")
    budget: dict[int, int] = {}
    sent = 0
    for c in Campaign.objects.filter(status="sending").select_related("owner").order_by("started_at", "pk"):
        sent += run(c, now, budget)
    return sent


def run(c: Campaign, now, budget: dict) -> int:
    owner = c.owner
    if not owner.is_active or not owner.has_flag("send_campaigns"):
        _pause(c, "The sender can no longer send campaigns. Someone with “Send campaigns” has to take it over.")
        return 0
    try:
        box = service.mailbox_for(owner)
    except MailError as e:
        if e.kind == "auth":
            _pause(c, "Reconnect your mailbox in Settings > My preferences to carry on.")
        else:
            _pause(c, str(e))
        return 0
    try:
        files = service.read_attachments(c.attachments or [], owner)
    except MailError as e:
        _pause(c, f"{e} Remove it from the campaign to carry on.")
        return 0
    per_minute = max(1, MailSettings.load().per_minute)
    left_today = service.remaining_today(box.account)
    room = min(per_minute - budget.get(owner.pk, 0), left_today)
    queued = list(c.recipients.filter(status="queued").select_related("contact__investor", "contact__company")[:max(room, 0)])
    if not queued and not c.recipients.filter(status__in=["queued", "sending"]).exists():
        c.status, c.finished_at = "done", now
        c.save(update_fields=["status", "finished_at"])
        return 0
    if left_today <= 0:
        _pause(c, f"Today's limit of {service.daily_limit(box.account)} emails from this mailbox is reached. "
                  "Carries on after midnight.", _tomorrow())
        return 0
    n = 0
    for r in queued:
        contact = r.contact
        if contact is None or contact.archived_at is not None:
            r.status, r.reason = "skipped", "Contact deleted"
            r.save(update_fields=["status", "reason"])
            continue
        if contact.email_opt_out:
            r.status, r.reason = "skipped", "Marked “No campaign emails”"
            r.save(update_fields=["status", "reason"])
            continue
        values = merge_values(contact, owner)
        subject, body = compose(c, values)
        r.status, r.sent_at, r.merge = "sending", timezone.now(), values
        r.save(update_fields=["status", "sent_at", "merge"])
        try:
            se = service.send_email(owner, to=[r.email], subject=subject, text=body, attachments=c.attachments or [],
                                    files=files, campaign=c, unsubscribe=True, box=box, contacts=[contact])
        except MailError as e:
            if e.kind == "rejected":
                r.status, r.reason = "failed", str(e)[:300]
                r.save(update_fields=["status", "reason"])
                continue
            r.status, r.sent_at = "queued", None
            r.save(update_fields=["status", "sent_at"])
            label = PROVIDERS[box.account.provider].label
            if e.kind == "auth":
                _pause(c, "Reconnect your mailbox in Settings > My preferences to carry on.")
            elif e.kind == "limit":
                wait = e.retry_after or 15 * 60
                at = now + dt.timedelta(seconds=max(wait, 60))
                _pause(c, f"{label}'s sending limit was reached. Carries on at "
                          f"{timezone.localtime(at):%H:%M}.", at)
            # A temporary problem: try again next minute.
            break
        r.status, r.sent_email, r.reason = "sent", se, ""
        r.save(update_fields=["status", "sent_email", "reason"])
        n += 1
        budget[owner.pk] = budget.get(owner.pk, 0) + 1
    if not c.recipients.filter(status__in=["queued", "sending"]).exists():
        c.refresh_from_db(fields=["status"])
        if c.status == "sending":
            c.status, c.finished_at = "done", timezone.now()
            c.save(update_fields=["status", "finished_at"])
    return n
