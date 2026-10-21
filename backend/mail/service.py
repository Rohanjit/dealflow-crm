"""Sending through a person's own mailbox, confirming it in their Sent folder, and recording it
in Dealflow: a log entry on the record it was about and on each contact it went to, and Last
contacted moved to that day once the send is confirmed (decision 62)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.models import Note
from core.records import apply_values, clean_values, visible
from core.registry import enabled_map, get_module

from . import crypto
from .models import MailAccount, MailSettings, SentEmail
from .providers import PROVIDERS, AppConfig, Mailbox, MailError, Outgoing, Sent

EMAIL_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
MAX_RECIPIENTS = 100  # one-to-one emails; campaigns send one email per person
MAX_ATTACH_BYTES = 25 * 1024 * 1024  # what both Gmail and most Outlook mailboxes accept
UPLOAD_DAYS = 7

ENV = {
    "gmail": ("DEALFLOW_GOOGLE_CLIENT_ID", "DEALFLOW_GOOGLE_CLIENT_SECRET", None),
    "outlook": ("DEALFLOW_MICROSOFT_CLIENT_ID", "DEALFLOW_MICROSOFT_CLIENT_SECRET", "DEALFLOW_MICROSOFT_TENANT"),
}


# ---------------------------------------------------------------- set-up


def app_config(provider: str) -> AppConfig | None:
    """The firm's app registration with Google or Microsoft: environment variables first, then
    what an admin saved in Settings > Email."""
    id_env, secret_env, tenant_env = ENV[provider]
    if os.environ.get(id_env) and os.environ.get(secret_env):
        return AppConfig(os.environ[id_env], os.environ[secret_env],
                         (os.environ.get(tenant_env) if tenant_env else None) or "common")
    ms = MailSettings.load()
    if provider == "gmail":
        cid, secret, tenant = ms.google_client_id, crypto.unseal(ms.google_client_secret), "common"
    else:
        cid, secret, tenant = ms.microsoft_client_id, crypto.unseal(ms.microsoft_client_secret), ms.microsoft_tenant
    if not cid or not secret:
        return None
    return AppConfig(cid, secret, tenant or "common")


def from_env(provider: str) -> bool:
    id_env, secret_env, _ = ENV[provider]
    return bool(os.environ.get(id_env) and os.environ.get(secret_env))


def redirect_uri(request, provider: str) -> str:
    base = (MailSettings.load().public_url or "").rstrip("/")
    path = f"/api/mail/callback/{provider}/"
    return base + path if base else request.build_absolute_uri(path)


def daily_limit(account: MailAccount) -> int:
    ms = MailSettings.load()
    return ms.gmail_daily_limit if account.provider == "gmail" else ms.outlook_daily_limit


def sent_today(account: MailAccount) -> int:
    return account.sent_today if account.sent_day == timezone.localdate() else 0


def remaining_today(account: MailAccount) -> int:
    return max(0, daily_limit(account) - sent_today(account))


def _count(account: MailAccount, n: int = 1):
    today = timezone.localdate()
    if account.sent_day != today:
        account.sent_day, account.sent_today = today, 0
    account.sent_today += n
    MailAccount.objects.filter(pk=account.pk).update(sent_day=account.sent_day, sent_today=account.sent_today)


def mailbox_for(user) -> Mailbox:
    account = MailAccount.objects.filter(user=user).first()
    if not account:
        raise MailError("Connect your Gmail or Outlook first, in Settings > My preferences.", "setup")
    if account.needs_reconnect:
        raise MailError(f"Your {PROVIDERS[account.provider].label} needs to be connected again, in "
                        "Settings > My preferences.", "auth")
    cfg = app_config(account.provider)
    if not cfg:
        raise MailError(f"{PROVIDERS[account.provider].label} isn't set up for this install. Ask an admin to "
                        "fill in Settings > Email.", "setup")

    def save(tokens):
        account.tokens = crypto.seal(tokens)
        MailAccount.objects.filter(pk=account.pk).update(tokens=account.tokens)

    try:
        return Mailbox(account, cfg, save)
    except MailError as e:
        note_problem(account, e)
        raise


def note_problem(account: MailAccount, e: MailError):
    """An auth problem means the person has to connect again; say so everywhere."""
    if e.kind == "auth":
        account.needs_reconnect = True
    account.last_error = str(e)[:300]
    MailAccount.objects.filter(pk=account.pk).update(needs_reconnect=account.needs_reconnect,
                                                     last_error=account.last_error)


def connect(user, provider: str, tokens: dict, email: str) -> MailAccount:
    account, _ = MailAccount.objects.update_or_create(
        user=user,
        defaults={"provider": provider, "email": email[:255], "tokens": crypto.seal(tokens),
                  "needs_reconnect": False, "last_error": "", "connected_at": timezone.now()},
    )
    # Campaigns stopped for a reconnect carry on now.
    from .models import Campaign

    Campaign.objects.filter(owner=user, status="paused", paused_reason__startswith="Reconnect").update(
        resume_at=timezone.now())
    return account


def account_payload(user) -> dict | None:
    a = MailAccount.objects.filter(user=user).first()
    if not a:
        return None
    return {"provider": a.provider, "provider_label": PROVIDERS[a.provider].label, "email": a.email,
            "connected_at": a.connected_at.isoformat(), "needs_reconnect": a.needs_reconnect,
            "last_error": a.last_error, "sent_today": sent_today(a), "daily_limit": daily_limit(a)}


# ---------------------------------------------------------------- addresses


def clean_addresses(raw, label: str) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]", raw)
    if not isinstance(raw, list):
        raise ValueError(f"{label}: give a list of email addresses.")
    out = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{label}: give a list of email addresses.")
        e = item.strip().strip("<>").strip()
        if not e:
            continue
        if len(e) > 254 or not EMAIL_RE.match(e):
            raise ValueError(f"{label}: “{e[:80]}” isn't an email address.")
        if e.lower() not in {x.lower() for x in out}:
            out.append(e)
    return out


def contacts_by_email(emails: list[str]):
    from contacts.models import Contact

    low = [e.lower() for e in emails]
    if not low:
        return []
    q = Q()
    for e in low:
        q |= Q(work_email__iexact=e) | Q(personal_email__iexact=e)
    return list(Contact.objects.filter(q, archived_at__isnull=True).select_related("investor", "company"))


def contact_email(c) -> str:
    for e in (c.work_email, c.personal_email):
        if e and EMAIL_RE.match(e.strip()):
            return e.strip()
    return ""


def suggestions(mod, obj, user) -> list[dict]:
    """People this record is about, for the To box: the contact itself, or the contacts of the
    investor or company (primary contacts first)."""
    from contacts.models import Contact

    if not user.can("contacts", "view") and mod.key != "contacts":
        return []
    if mod.key == "contacts":
        qs = Contact.objects.filter(pk=obj.pk)
    elif mod.key == "investors":
        qs = Contact.objects.filter(investor=obj)
    elif mod.key == "companies":
        qs = Contact.objects.filter(company=obj)
    elif mod.key in ("outreach", "deals"):
        qs = Contact.objects.filter(company_id=obj.company_id)
    elif mod.key == "deal_investors":
        qs = Contact.objects.filter(investor_id=obj.investor_id)
    else:
        return []
    qs = qs.filter(archived_at__isnull=True).select_related("investor", "company", "decision_level")
    qs = qs.order_by("-primary_contact", "decision_level__order", "full_name")[:50]
    out = []
    for c in qs:
        firm = c.investor.firm_name if c.investor_id else (c.company.name if c.company_id else "")
        for kind, e in (("Work", c.work_email), ("Personal", c.personal_email)):
            e = (e or "").strip()
            if e and EMAIL_RE.match(e):
                out.append({"name": c.full_name, "email": e, "contact_id": c.pk, "kind": kind, "firm": firm,
                            "designation": c.designation, "primary": c.primary_contact,
                            "opted_out": c.email_opt_out})
    return out


# ---------------------------------------------------------------- attachments


def _upload_dir(user) -> Path:
    p = Path(settings.DATA_DIR) / "mail_uploads" / str(user.pk)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_upload(user, f) -> dict:
    """A file from the person's computer, kept for a week so a campaign can send it later."""
    from vault.files import check, clean_name

    ext, kind, digest = check(f)  # same allowlist and checks as the Vault
    token = secrets.token_urlsafe(18)
    d = _upload_dir(user)
    with open(d / f"{token}.bin", "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)
    meta = {"name": clean_name(f.name), "size": f.size, "content_type": kind.content_type, "type": kind.label}
    (d / f"{token}.json").write_text(json.dumps(meta))
    return {"kind": "upload", "token": token, **meta}


def _valid_token(token) -> bool:
    return isinstance(token, str) and bool(re.fullmatch(r"[A-Za-z0-9_\-]{10,60}", token))


def upload_info(user, token) -> dict | None:
    if not _valid_token(token):
        return None
    p = _upload_dir(user) / f"{token}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def clean_attachments(raw, user, request=None, for_campaign=False) -> list[dict]:
    """[{kind: "vault", id} | {kind: "upload", token}] → checked list with names and sizes.
    Vault documents need Vault view access and an open Vault (its PIN) right now."""
    from vault.models import Document

    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError("Attach at most 20 files.")
    out, total = [], 0
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Attachments are listed wrongly.")
        if item.get("kind") == "vault":
            from core.records import to_id

            if not user.can("vault", "view"):
                raise ValueError("Your role can't see the Vault.")
            if request is not None:
                get_module("vault").gate(request)
            doc = Document.objects.filter(pk=to_id(item.get("id")), archived_at__isnull=True).first()
            if not doc:
                raise ValueError("A Vault document you picked has been deleted.")
            entry = {"kind": "vault", "id": doc.pk, "name": doc.file_name, "size": doc.file_size,
                     "content_type": doc.content_type, "type": doc.file_type}
        elif item.get("kind") == "upload":
            info = upload_info(user, item.get("token"))
            if not info:
                raise ValueError("A file you attached has expired. Attach it again.")
            entry = {"kind": "upload", "token": item["token"], **info}
        else:
            raise ValueError("Attachments come from the Vault or your computer.")
        if any(e.get("kind") == entry["kind"] and (e.get("id"), e.get("token")) == (entry.get("id"), entry.get("token"))
               for e in out):
            continue
        total += entry["size"]
        out.append(entry)
    if total > MAX_ATTACH_BYTES:
        raise ValueError(f"Attachments come to {total / 1048576:.1f} MB; emails can carry up to "
                         f"{MAX_ATTACH_BYTES // 1048576} MB.")
    return out


def read_attachments(items: list[dict], user) -> list[tuple[str, str, bytes]]:
    from vault.models import Document

    files = []
    for a in items:
        if a["kind"] == "vault":
            doc = Document.objects.filter(pk=a["id"], archived_at__isnull=True).first()
            if not doc:
                raise MailError(f"{a['name']} has been deleted from the Vault.", "rejected")
            with doc.file.open("rb") as fh:
                files.append((doc.file_name, doc.content_type, fh.read()))
        else:
            if not _valid_token(a.get("token")):
                raise MailError(f"{a.get('name', 'An attachment')} has expired.", "rejected")
            p = _upload_dir(user) / f"{a['token']}.bin"
            try:
                files.append((a["name"], a["content_type"], p.read_bytes()))
            except OSError:
                raise MailError(f"{a['name']} has expired. Attach it again.", "rejected")
    return files


def remove_old_uploads(now=None):
    """Housekeeping: files from computers are kept a week (longer while a campaign still needs them)."""
    from .models import Campaign

    keep = set()
    for c in Campaign.objects.filter(status__in=["draft", "scheduled", "sending", "paused"]).only("attachments"):
        keep.update(a.get("token") for a in c.attachments or [] if a.get("kind") == "upload")
    cut = (now or timezone.now()).timestamp() - UPLOAD_DAYS * 86400
    root = Path(settings.DATA_DIR) / "mail_uploads"
    for p in root.glob("*/*"):
        if p.stem not in keep and p.stat().st_mtime < cut:
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- recording


def _firm_record(contact):
    """The investor (or company, if its module keeps a log) a contact belongs to."""
    if contact.investor_id:
        return get_module("investors"), contact.investor
    if contact.company_id and get_module("companies").timeline:
        return get_module("companies"), contact.company
    return None


def record_sent(user, out: Outgoing, sent: Sent, provider: str, records: list, contacts: list,
                attachments: list[dict], campaign=None) -> SentEmail:
    """Log it on each record (the one it was about, the contacts it went to, their firms) and,
    once confirmed, move Last contacted."""
    se = SentEmail.objects.create(
        user=user, provider=provider, from_email=out.from_email, to=[e for _, e in out.to],
        cc=[e for _, e in out.cc], subject=out.subject[:300], message_id=sent.message_id[:300],
        thread_id=sent.thread_id[:300], web_link=sent.web_link[:1000],
        status="confirmed" if sent.confirmed else "pending",
        confirmed_at=timezone.now() if sent.confirmed else None,
        contact_ids=[c.pk for c in contacts], campaign=campaign,
    )
    targets, seen = [], set()
    for mod, obj in [*records, *[(get_module("contacts"), c) for c in contacts],
                     *[f for c in contacts if (f := _firm_record(c))]]:
        if mod.timeline and (mod.key, obj.pk) not in seen:
            seen.add((mod.key, obj.pk))
            targets.append((mod, obj))
    meta = {"email": se.pk, "provider": provider, "from": out.from_email, "to": se.to, "cc": se.cc,
            "bcc": len(out.bcc), "subject": out.subject[:300], "web_link": se.web_link, "status": se.status,
            "attachments": [a["name"] for a in attachments],
            **({"campaign": campaign.pk, "campaign_name": campaign.name} if campaign else {})}
    today = timezone.localdate()
    notes = Note.objects.bulk_create([
        Note(module=mod.key, record_id=obj.pk, kind="email", date=today, text=out.text[:20000], meta=meta,
             created_by=user)
        for mod, obj in targets
    ])
    se.note_ids = [n.pk for n in notes if n.pk]
    if not se.note_ids:  # SQLite before Django 5 doesn't return ids from bulk_create
        se.note_ids = list(Note.objects.filter(kind="email", meta__email=se.pk).values_list("pk", flat=True))
    se.save(update_fields=["note_ids"])
    if sent.confirmed:
        touch_contacted(se, user)
    return se


def confirm(se: SentEmail, sent: Sent | None, give_up=False):
    """A later look at the Sent folder: found it, or stopped looking."""
    se.checks += 1
    se.checked_at = timezone.now()
    if sent:
        se.status, se.confirmed_at = "confirmed", timezone.now()
        se.web_link = (sent.web_link or se.web_link)[:1000]
    elif give_up:
        se.status = "unconfirmed"
    se.save()
    if se.status != "pending":
        for n in Note.objects.filter(pk__in=se.note_ids):
            n.meta = {**n.meta, "status": se.status, "web_link": se.web_link}
            n.save(update_fields=["meta", "updated_at"])
    if se.status == "confirmed":
        touch_contacted(se, se.user)


def touch_contacted(se: SentEmail, user):
    """Last contacted = the day it was sent, on each contact it went to and on their investor,
    through the normal save (stamps, audit), never moving it backwards."""
    from contacts.models import Contact

    day = timezone.localtime(se.sent_at).date()
    for c in Contact.objects.filter(pk__in=se.contact_ids, archived_at__isnull=True).select_related("investor"):
        for mod, obj in [(get_module("contacts"), c), *([(get_module("investors"), c.investor)]
                                                       if c.investor_id and c.investor.archived_at is None else [])]:
            if (obj.last_contacted and obj.last_contacted >= day) or not enabled_map(mod.key).get("last_contacted", True):
                continue
            values, errors = clean_values(mod, {"last_contacted": day.isoformat()}, enabled=enabled_map(mod.key),
                                          instance=obj, partial=True)
            if errors:
                continue
            obj.stale_snoozed_until = None
            apply_values(obj, mod, values, user)


def recheck_pending(now=None, batch=50):
    """Scheduler: look again in Outlook's Sent Items for sends not yet confirmed. Up to 12 looks
    over about an hour, then it's marked "Not found in Sent"."""
    now = now or timezone.now()
    due = SentEmail.objects.filter(status="pending").filter(
        Q(checked_at__isnull=True) | Q(checked_at__lte=now - dt.timedelta(minutes=4)))[:batch]
    boxes = {}
    for se in due:
        give_up = se.checks >= 11 or se.sent_at < now - dt.timedelta(hours=2)
        if se.provider != "outlook" or not se.user_id or not se.message_id:
            confirm(se, None, give_up=True)
            continue
        try:
            if se.user_id not in boxes:
                boxes[se.user_id] = mailbox_for(se.user)
            box = boxes[se.user_id]
            if box.email.lower() != se.from_email.lower():
                confirm(se, None, give_up=True)
                continue
            found = box.provider.check(box, se.message_id)
        except MailError as e:
            if e.kind == "auth" and (acct := MailAccount.objects.filter(user_id=se.user_id).first()):
                note_problem(acct, e)
            confirm(se, None, give_up=give_up)
            continue
        confirm(se, found, give_up=give_up)


# ---------------------------------------------------------------- one-to-one


def send_email(user, *, to, cc=(), bcc=(), subject, text, attachments=(), records=(), campaign=None,
               unsubscribe=False, box: Mailbox | None = None, contacts=None, files=None) -> SentEmail:
    """Send now from the person's own mailbox and record it. Raises MailError."""
    box = box or mailbox_for(user)
    account = box.account
    if remaining_today(account) <= 0:
        raise MailError(f"You've sent {daily_limit(account)} emails today, this install's daily limit for one "
                        "mailbox. It resets at midnight.", "limit")
    if files is None:
        files = read_attachments(list(attachments), user)
    named = {e.lower(): c.full_name for c in (contacts if contacts is not None else contacts_by_email([*to, *cc, *bcc]))
             for e in (c.work_email, c.personal_email) if e}
    out = Outgoing(
        from_name=user.full_name, from_email=box.email,
        to=[(named.get(e.lower(), ""), e) for e in to], cc=[(named.get(e.lower(), ""), e) for e in cc],
        bcc=[(named.get(e.lower(), ""), e) for e in bcc],
        subject=subject, text=text, attachments=files, unsubscribe=unsubscribe,
    )
    try:
        sent = box.provider.send(box, out)
    except MailError as e:
        note_problem(account, e)
        raise
    _count(account)
    if account.last_error:
        MailAccount.objects.filter(pk=account.pk).update(last_error="")
    if contacts is None:
        contacts = contacts_by_email([*to, *cc, *bcc])
    return record_sent(user, out, sent, account.provider, list(records), contacts, list(attachments), campaign)


def sent_payload(se: SentEmail) -> dict:
    return {"id": se.pk, "status": se.status, "status_label": se.get_status_display(), "web_link": se.web_link,
            "provider": se.provider, "from": se.from_email, "to": se.to, "subject": se.subject,
            "logged_on": len(se.note_ids)}


def about_record(raw: str, user, action="edit"):
    """"deals:5" → (module, record) the person may log on, or ValueError."""
    from core.records import to_id

    if not isinstance(raw, str) or ":" not in raw:
        raise ValueError("Say which record this email is about.")
    key, _, rid = raw.partition(":")
    from core.modules import BUILT_MODULES

    if key not in BUILT_MODULES or key in ("tasks", "vault"):
        raise ValueError("Emails can be sent from investors, contacts, companies, outreach, deals and trackers.")
    mod = get_module(key)
    if not user.can(key, "view"):
        raise PermissionError(f"Your role can't see {mod.label}.")
    if action == "edit" and not user.can(key, "edit"):
        raise PermissionError(f"Your role can't edit {mod.label}, so it can't log emails there.")
    obj = visible(mod.model.objects.filter(pk=to_id(rid), archived_at__isnull=True), mod, user).first()
    if not obj:
        raise LookupError(f"This {mod.singular.lower()} doesn't exist or has been deleted.")
    return mod, obj
