"""Investor data rooms (decision 64): what bankers can do, and what happens when investors use them.

Who can do what
- Anyone who can see the deal sees its data room panel (files, who it's shared with, activity).
- Sharing, adding or removing files, answering questions and changing the room need the role flag
  "Share with investors", edit on Active Deals, and being on the deal team (admins always can).
- The first share on a deal needs the deal's approver (its MD; with none, the lead banker; with
  neither, an admin), through the same requests as restricted deals. Shares made before that wait
  and their invitations go out on approval.

Investors
- Each link belongs to one investor on the deal's tracker and one email address. Every opening needs
  a 6-digit code emailed to that address (10 minutes, 5 tries; 5 codes an hour; 10 wrong codes in an
  hour lock the link for an hour). Then the NDA (none, agree on screen, or a signed NDA the banker
  marks as received), then the files.
- Links stop when the banker stops them, when they run out (30 days by default), when the room is
  closed, when the tracker entry or the deal is deleted, and when the deal is withdrawn or dropped.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from core.models import Note
from deals import access
from deals.models import TRACKER_STAGES, AccessRequest, Deal, DealInvestor

from . import crypto, marks
from .models import Code, Question, Room, RoomFile, Share, Visit, Visitor

STAGE_ORDER = [k for k, _ in TRACKER_STAGES]
STAGE_LABELS = dict(TRACKER_STAGES)
CODE_MINUTES = 10
CODE_TRIES = 5
CODES_PER_HOUR = 5
WRONG_PER_HOUR = 10
IDLE_MINUTES = 30
MAX_HOURS = 12
DAYS = (7, 14, 30, 60, 90)


class RoomError(Exception):
    pass


# ---------------------------------------------------------------- permissions


def can_view(user, deal: Deal) -> bool:
    return user.can("deals", "view") and access.can_see(user, deal)


def can_share(user, deal: Deal) -> bool:
    return (can_view(user, deal) and user.has_flag("share_with_investors") and user.can("deals", "edit")
            and (user.is_admin or user.pk in access.team_ids(deal)))


def require_share(user, deal: Deal):
    if not can_share(user, deal):
        if not user.has_flag("share_with_investors"):
            raise PermissionDenied("Your role can't share with investors. An admin can turn on "
                                   "“Share with investors” for it in Settings > Roles.")
        raise PermissionDenied("Only people on the deal team can share this deal with investors.")


def live_deal(user, pk) -> Deal:
    from core.errors import Restricted

    deal = Deal.objects.filter(pk=pk, archived_at__isnull=True).select_related("company", "lead_banker").first()
    if deal is None or not user.can("deals", "view"):
        raise NotFound("This deal doesn't exist or has been deleted.")
    if not access.can_see(user, deal):
        raise Restricted("This deal is restricted.", access.placeholder(deal, user))
    return deal


# ---------------------------------------------------------------- links and email


def base_url(request=None) -> str:
    if getattr(settings, "ROOMS_DOMAIN", ""):
        return f"https://{settings.ROOMS_DOMAIN}"
    from mail.models import MailSettings

    public = (MailSettings.load().public_url or "").rstrip("/")
    if public:
        return public
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return ""


def link_for(share: Share, request=None) -> str:
    base = base_url(request) if (request is not None or getattr(settings, "ROOMS_DOMAIN", "")) else ""
    base = base or share.room.link_base or base_url(None)
    return f"{base}/r/{crypto.open_token(share.token_sealed, str(share.pk))}/"


def _smtp_send(to: str, subject: str, text: str, reply_to: str = ""):
    """DEALFLOW_SMTP_URL=smtps://user:pass@host:465 or smtp://user:pass@host:587 (STARTTLS required)."""
    url = urlparse(settings.ROOMS_SMTP_URL)
    msg = EmailMessage()
    msg["From"] = settings.ROOMS_SMTP_FROM or unquote(url.username or "")
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    port = url.port or (465 if url.scheme == "smtps" else 587)
    try:
        if url.scheme == "smtps":
            server = smtplib.SMTP_SSL(url.hostname, port, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(url.hostname, port, timeout=20)
            server.starttls(context=ctx)
        with server:
            if url.username:
                server.login(unquote(url.username), unquote(url.password or ""))
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise RoomError("The code couldn't be emailed just now. Try again in a minute.") from e


def _sender(share: Share):
    """The banker whose mailbox sends: whoever shared it, else the deal's lead banker."""
    deal = share.room.deal
    for u in (share.created_by, deal.lead_banker):
        if u is not None and u.is_active:
            return u
    return None


def send_quietly(share: Share, subject: str, text: str):
    """Codes and "you have an answer" notes: through the firm's SMTP server if one is set, else from the
    banker's own mailbox. Not logged on the record (a code has no place in a log)."""
    if getattr(settings, "ROOMS_SMTP_URL", ""):
        banker = _sender(share)
        _smtp_send(share.email, subject, text, reply_to=banker.email if banker else "")
        return
    from mail.providers import MailError, Outgoing
    from mail.service import _count, mailbox_for, note_problem

    banker = _sender(share)
    if banker is None:
        raise RoomError("The code couldn't be emailed. Ask the banker who sent you this link.")
    try:
        box = mailbox_for(banker)
        box.provider.send(box, Outgoing(from_name=banker.full_name, from_email=box.email, to=[(share.name, share.email)],
                                        subject=subject, text=text))
        _count(box.account)
    except MailError as e:
        if getattr(e, "kind", "") == "auth":
            from mail.models import MailAccount

            if acct := MailAccount.objects.filter(user=banker).first():
                note_problem(acct, e)
        raise RoomError("The code couldn't be emailed. Ask the banker who sent you this link.") from e


def can_send_codes(share_or_user) -> bool:
    if getattr(settings, "ROOMS_SMTP_URL", ""):
        return True
    from mail.models import MailAccount

    user = share_or_user
    return MailAccount.objects.filter(user=user, needs_reconnect=False).exists()


def _first_name(name: str) -> str:
    return (name or "").split(" ")[0] or "there"


def invite(share: Share, user, request=None):
    """Email the link from the banker's own mailbox, logged on the tracker entry like any email."""
    from core.registry import get_module
    from mail.providers import MailError
    from mail.service import send_email

    room = share.room
    expires = timezone.localtime(share.expires_at).strftime("%d %b %Y")
    text = (f"Hi {_first_name(share.name)},\n\n{user.full_name} has shared the {room.name} data room with you.\n\n"
            f"Open it here: {link_for(share, request)}\n\n"
            "Each time you open it, a one-time code is emailed to this address. Please don't forward this link: "
            f"it only works with codes sent to you. It works until {expires}.\n\n{user.full_name}")
    try:
        send_email(user, to=[share.email], subject=f"{room.name}: your data room link", text=text,
                   records=[(get_module("deal_investors"), share.entry)])
    except MailError as e:
        Share.objects.filter(pk=share.pk).update(invite_error=str(e)[:300])
        share.invite_error = str(e)[:300]
        return False
    now = timezone.now()
    Share.objects.filter(pk=share.pk).update(invited_at=now, invite_error="")
    share.invited_at, share.invite_error = now, ""
    return True


# ---------------------------------------------------------------- the room


def get_room(deal: Deal) -> Room | None:
    return Room.objects.filter(deal=deal).first()


def ensure_room(deal: Deal, user) -> Room:
    room = get_room(deal)
    if room is None:
        room = Room.objects.create(deal=deal, name=f"{deal.company.name} data room"[:200], created_by=user)
    return room


def _remember_base(room: Room, request):
    base = base_url(request)[:300] if request is not None else ""
    if base and room.link_base != base:
        room.link_base = base
        room.save(update_fields=["link_base"])


def _log(deal: Deal, user, text: str, module="deals", record_id=None, meta=None):
    from core import current

    current.note(text)
    Note.objects.create(module=module, record_id=record_id or deal.pk, kind="note", date=timezone.localdate(),
                        text=text, meta=meta or {}, created_by=user)


def update_room(deal: Deal, user, data: dict) -> Room:
    require_share(user, deal)
    room = ensure_room(deal, user)
    changes = []
    if "name" in data:
        name = " ".join(str(data["name"] or "").split())[:200]
        if not name:
            raise ValidationError({"name": "Give the room a name investors will see."})
        room.name = name
    if "welcome" in data:
        room.welcome = str(data["welcome"] or "").replace("\x00", "")[:5000]
    if "nda_mode" in data:
        if data["nda_mode"] not in dict(Room.NDA_MODES):
            raise ValidationError({"nda_mode": "Pick no NDA, agree on screen or signed NDA."})
        if data["nda_mode"] != room.nda_mode:
            changes.append(f"NDA: {dict(Room.NDA_MODES)[data['nda_mode']]}")
        room.nda_mode = data["nda_mode"]
    if "nda_text" in data:
        text = str(data["nda_text"] or "").replace("\x00", "").strip()[:20000]
        if room.nda_mode != "none" and not text:
            raise ValidationError({"nda_text": "Write the NDA investors agree to."})
        room.nda_text = text
    if "is_open" in data:
        if not isinstance(data["is_open"], bool):
            raise ValidationError({"is_open": "Choose open or closed."})
        if data["is_open"] != room.is_open:
            changes.append("Opened the data room again" if data["is_open"] else "Closed the data room (every link stopped)")
        room.is_open = data["is_open"]
    room.save()
    for c in changes:
        _log(deal, user, f"Data room: {c}.")
    return room


# ---------------------------------------------------------------- files


def add_file(request, deal: Deal, document_id, mode: str, title: str = "") -> RoomFile:
    from core.records import visible
    from core.registry import get_module
    from vault import keyring
    from vault.models import Document

    user = request.user
    require_share(user, deal)
    if not user.can("vault", "view"):
        raise PermissionDenied("Your role can't see the Vault.")
    mod = get_module("vault")
    if mod.gate:
        mod.gate(request)
    doc = visible(Document.objects.filter(pk=document_id, archived_at__isnull=True), mod, user).first()
    if doc is None:
        raise ValidationError({"document": "Pick a document from the Vault."})
    room = ensure_room(deal, user)
    if RoomFile.objects.filter(room=room, document=doc).exists():
        raise ValidationError({"document": f"{doc.title} is already in the data room."})
    ext = doc.file_name.rpartition(".")[2].lower() if "." in doc.file_name else ""
    if reason := marks.unsupported_reason(ext):
        raise ValidationError({"document": reason})
    if mode not in ("view", "download"):
        raise ValidationError({"mode": "Pick view only or download."})
    if mode == "view" and ext not in marks.VIEWABLE:
        raise ValidationError({"mode": "Word, Excel and PowerPoint files can only be shared as watermarked "
                                       "downloads (only PDFs and pictures can be shown page by page)."})
    data = keyring.read_document(request, doc)
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise ValidationError({"document": "This file doesn't match what was uploaded. Upload it again."})
    try:
        pages = marks.page_count(data, ext)
        if ext in ("xlsx", "xlsm", "docx", "pptx"):
            marks.check_office(data)
    except marks.MarkError as e:
        raise ValidationError({"document": str(e)}) from e
    rf = RoomFile(room=room, document=doc, title=(" ".join(str(title or "").split()) or doc.title)[:255], mode=mode,
                  position=(RoomFile.objects.filter(room=room).count() + 1), file_name=doc.file_name, ext=ext,
                  file_type=doc.file_type, file_size=doc.file_size, sha256=doc.sha256, pages=pages, added_by=user)
    rf.stored.save("room.dfr", ContentFile(crypto.encrypt_file(data, doc.sha256)), save=False)
    rf.save()
    _log(deal, user, f"Data room: added “{rf.title}” ({dict(RoomFile.MODES)[mode].lower()}).")
    return rf


def read_file(rf: RoomFile) -> bytes:
    with rf.stored.open("rb") as fh:
        return crypto.decrypt_file(fh.read(), rf.sha256)


def change_file(rf: RoomFile, user, data: dict):
    deal = rf.room.deal
    require_share(user, deal)
    if "mode" in data:
        if data["mode"] not in ("view", "download"):
            raise ValidationError({"mode": "Pick view only or download."})
        if data["mode"] == "view" and rf.ext not in marks.VIEWABLE:
            raise ValidationError({"mode": "Only PDFs and pictures can be view only."})
        if data["mode"] != rf.mode:
            _log(deal, user, f"Data room: “{rf.title}” is now {dict(RoomFile.MODES)[data['mode']].lower()}.")
        rf.mode = data["mode"]
    if "title" in data:
        t = " ".join(str(data["title"] or "").split())[:255]
        if not t:
            raise ValidationError({"title": "Give the file a name."})
        rf.title = t
    if "position" in data:
        try:
            rf.position = max(0, min(10_000, int(data["position"])))
        except (TypeError, ValueError):
            raise ValidationError({"position": "A number, please."}) from None
    rf.save()


def remove_file(rf: RoomFile, user):
    deal = rf.room.deal
    require_share(user, deal)
    _log(deal, user, f"Data room: removed “{rf.title}”.")
    rf.delete()


# ---------------------------------------------------------------- shares


def entry_contacts(entry: DealInvestor) -> list[dict]:
    from contacts.models import Contact

    out = []
    for c in Contact.objects.filter(investor=entry.investor, archived_at__isnull=True).order_by("-primary_contact", "full_name"):
        for e in (c.work_email, c.personal_email):
            if e:
                out.append({"id": c.pk, "name": c.full_name, "email": e.lower()})
                break
    return out


def _clean_email(raw) -> str:
    from django.core.exceptions import ValidationError as DjValidationError
    from django.core.validators import validate_email

    e = str(raw or "").strip().lower()
    try:
        validate_email(e)
    except DjValidationError:
        raise ValidationError({"email": "Enter the investor's email address."}) from None
    if len(e) > 255:
        raise ValidationError({"email": "That email address is too long."})
    return e


def approval_state(room: Room) -> dict:
    pending = AccessRequest.objects.filter(deal=room.deal, kind="room", status="pending").select_related("requested_by").first()
    return {
        "approved": room.approved_at is not None,
        "approved_by": room.approved_by.full_name if room.approved_by_id else None,
        "approved_at": room.approved_at.isoformat() if room.approved_at else None,
        "pending": {"id": pending.pk, "requested_by": pending.requested_by.full_name,
                    "at": pending.created_at.isoformat()} if pending else None,
        "approvers": [u.full_name for u in access.approvers(room.deal)],
    }


def _ensure_approval(room: Room, user) -> bool:
    if room.approved_at:
        return True
    r = access.ask_room(room.deal, user)
    room.refresh_from_db()
    return r.status == "approved" and room.approved_at is not None


@transaction.atomic
def create_share(request, deal: Deal, data: dict) -> Share:
    from core.records import to_id

    user = request.user
    require_share(user, deal)
    room = ensure_room(deal, user)
    if not room.is_open:
        raise ValidationError({"detail": "The data room is closed. Open it again first."})
    entry = DealInvestor.objects.filter(pk=to_id(data.get("entry")), deal=deal, archived_at__isnull=True).select_related(
        "investor").first()
    if entry is None:
        raise ValidationError({"entry": "Pick an investor on this deal's tracker."})
    email = _clean_email(data.get("email"))
    from contacts.models import Contact

    contact = None
    if cid := to_id(data.get("contact")):
        contact = Contact.objects.filter(pk=cid, archived_at__isnull=True).first()
    if contact is None:
        contact = Contact.objects.filter(Q(work_email__iexact=email) | Q(personal_email__iexact=email),
                                         archived_at__isnull=True).first()
    name = " ".join(str(data.get("name") or (contact.full_name if contact else "")).split())[:200]
    if not name:
        raise ValidationError({"name": "Enter the investor's name."})
    try:
        days = int(data.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    if days not in DAYS:
        raise ValidationError({"days": "Pick 7, 14, 30, 60 or 90 days."})
    if Share.objects.filter(room=room, email=email, status__in=("waiting", "active"), expires_at__gt=timezone.now()).exists():
        raise ValidationError({"email": f"{email} already has a working link to this room."})
    if not can_send_codes(user):
        raise ValidationError({"detail": "Connect your Gmail or Outlook in Settings > My preferences first: "
                                         "investors' codes and links are sent from your mailbox."})
    token = crypto.new_token()
    share = Share.objects.create(room=room, entry=entry, contact=contact, name=name, email=email,
                                 token_hash=crypto.token_hash(token), token_sealed={}, status="waiting",
                                 expires_at=timezone.now() + dt.timedelta(days=days), created_by=user)
    share.token_sealed = crypto.seal_token(token, str(share.pk))
    share.save(update_fields=["token_sealed"])
    _remember_base(room, request)
    was_approved = room.approved_at is not None
    if was_approved:
        share.status = "active"
        share.save(update_fields=["status"])
        transaction.on_commit(lambda: invite(share, user, request))
    else:
        _ensure_approval(room, user)  # an approver sharing approves it there and then; links go out
        share.refresh_from_db()
    waiting = share.status == "waiting"
    _log(deal, user, f"Data room: shared with {name} ({entry.investor.firm_name})"
                     + (", waiting for approval." if waiting else "."))
    _log(deal, user, f"Data room shared with {name} ({email}), link for {days} days.", module="deal_investors",
         record_id=entry.pk)
    return share


def approved(deal: Deal, approver):
    """Called by deals.access when the first share is approved: the waiting links go out."""
    room = get_room(deal)
    if room is None:
        return
    room.approved_at, room.approved_by = timezone.now(), approver
    room.save(update_fields=["approved_at", "approved_by"])
    waiting = list(Share.objects.filter(room=room, status="waiting").select_related("room__deal", "entry", "created_by"))
    Share.objects.filter(pk__in=[s.pk for s in waiting]).update(status="active")

    def send():
        for s in waiting:
            s.status = "active"
            if s.created_by and s.created_by.is_active:
                invite(s, s.created_by)

    transaction.on_commit(send)


def change_share(request, share: Share, data: dict) -> Share:
    user = request.user
    deal = share.room.deal
    require_share(user, deal)
    action = data.get("action")
    now = timezone.now()
    if action == "stop":
        if share.status == "revoked":
            raise ValidationError({"detail": "This link is already stopped."})
        share.status, share.revoked_at, share.revoked_by = "revoked", now, user
        share.save()
        Visitor.objects.filter(share=share, ended_at__isnull=True).update(ended_at=now)
        _log(deal, user, f"Data room: stopped {share.name}'s link.")
        _log(deal, user, "Data room link stopped.", module="deal_investors", record_id=share.entry_id)
    elif action == "extend":
        try:
            days = int(data.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        if days not in DAYS or share.status == "revoked":
            raise ValidationError({"days": "Pick 7, 14, 30, 60 or 90 days for a working link."})
        share.expires_at = max(share.expires_at, now) + dt.timedelta(days=days)
        share.save(update_fields=["expires_at"])
        _log(deal, user, f"Data room: {share.name}'s link now works until "
                         f"{timezone.localtime(share.expires_at):%d %b %Y}.")
    elif action == "resend":
        if share.status != "active" or share.expires_at <= now:
            raise ValidationError({"detail": "Only working links can be sent again."})
        if not invite(share, user, request):
            raise ValidationError({"detail": share.invite_error or "The email couldn't be sent."})
    elif action == "nda_received":
        if share.room.nda_mode != "signed":
            raise ValidationError({"detail": "This room doesn't ask for a signed NDA."})
        if share.nda_received_at:
            raise ValidationError({"detail": "Already marked as received."})
        share.nda_received_at, share.nda_received_by = now, user
        share.save(update_fields=["nda_received_at", "nda_received_by"])
        _log(deal, user, f"Data room: signed NDA from {share.name} received; their room is open.")
        _log(deal, user, "Signed NDA received for the data room.", module="deal_investors", record_id=share.entry_id)
    elif action == "set_aside":
        share.suggestion_set_aside = str(data.get("stage") or "")[:30]
        share.save(update_fields=["suggestion_set_aside"])
    else:
        raise ValidationError({"action": "Pick stop, extend, resend, nda_received or set_aside."})
    return share


# ---------------------------------------------------------------- investors' side


def working(share: Share) -> bool:
    room = share.room
    deal = room.deal
    return (share.status == "active" and share.expires_at > timezone.now() and room.is_open
            and room.approved_at is not None and deal.archived_at is None and deal.status not in ("withdrawn", "dropped")
            and share.entry.archived_at is None)


def find_share(token: str) -> Share | None:
    if not token or len(token) > 100:
        return None
    share = Share.objects.filter(token_hash=crypto.token_hash(token)).select_related(
        "room__deal__company", "entry__investor", "created_by").first()
    if share is None or not working(share):
        return None
    return share


def locked(share: Share) -> bool:
    return bool(share.locked_until and share.locked_until > timezone.now())


def send_code(share: Share, ip: str):
    now = timezone.now()
    if locked(share):
        raise RoomError("Too many wrong codes. This link is paused for an hour.")
    hour = now - dt.timedelta(hours=1)
    if Code.objects.filter(share=share, created_at__gte=hour).count() >= CODES_PER_HOUR:
        raise RoomError("That's the most codes we can send in an hour. Use the latest one, or try again later.")
    if Code.objects.filter(ip=ip, created_at__gte=hour).count() >= CODES_PER_HOUR * 4:
        raise RoomError("Too many codes from this network. Try again later.")
    Code.objects.filter(share=share, used_at__isnull=True).update(used_at=now)  # only the newest works
    code = crypto.new_code()
    Code.objects.create(share=share, code_hash=crypto.code_hash(share.pk, code), ip=ip[:70],
                        expires_at=now + dt.timedelta(minutes=CODE_MINUTES))
    room = share.room
    send_quietly(share, f"{code} is your code for {room.name}",
                 f"Your code for the {room.name} data room is:\n\n{code}\n\nIt works for {CODE_MINUTES} minutes. "
                 "If you didn't ask for it, you can ignore this email.")
    Visit.objects.create(share=share, kind="code_sent", ip=ip[:70])


def check_code(share: Share, code: str, ip: str, user_agent: str) -> str:
    """The visitor token for the cookie, or RoomError."""
    now = timezone.now()
    if locked(share):
        raise RoomError("Too many wrong codes. This link is paused for an hour.")
    c = Code.objects.filter(share=share, used_at__isnull=True, expires_at__gt=now).order_by("-created_at").first()
    code = "".join(ch for ch in str(code or "") if ch.isdigit())[:6]
    if c is None:
        raise RoomError("That code has run out. Ask for a new one.")
    if len(code) != 6 or not crypto.same(c.code_hash, crypto.code_hash(share.pk, code)):
        c.tries += 1
        if c.tries >= CODE_TRIES:
            c.used_at = now
        c.save(update_fields=["tries", "used_at"])
        Visit.objects.create(share=share, kind="code_wrong", ip=ip[:70])
        if Visit.objects.filter(share=share, kind="code_wrong", at__gte=now - dt.timedelta(hours=1)).count() >= WRONG_PER_HOUR:
            share.locked_until = now + dt.timedelta(hours=1)
            share.save(update_fields=["locked_until"])
            Visit.objects.create(share=share, kind="locked", ip=ip[:70])
            raise RoomError("Too many wrong codes. This link is paused for an hour.")
        if c.used_at:
            raise RoomError("Too many tries with that code. Ask for a new one.")
        raise RoomError("That code isn't right. Check the latest email and try again.")
    c.used_at = now
    c.save(update_fields=["used_at"])
    token = crypto.new_token()
    v = Visitor.objects.create(share=share, token_hash=crypto.token_hash(token), ip=ip[:70], user_agent=user_agent[:300])
    Visit.objects.create(share=share, visitor=v, kind="opened", ip=ip[:70])
    share.last_opened_at = now
    share.first_opened_at = share.first_opened_at or now
    share.save(update_fields=["last_opened_at", "first_opened_at"])
    return token


def visitor_for(share: Share, token: str | None) -> Visitor | None:
    if not token:
        return None
    v = Visitor.objects.filter(token_hash=crypto.token_hash(token), share=share, ended_at__isnull=True).first()
    if v is None:
        return None
    now = timezone.now()
    if v.last_seen < now - dt.timedelta(minutes=IDLE_MINUTES) or v.created_at < now - dt.timedelta(hours=MAX_HOURS):
        v.ended_at = now
        v.save(update_fields=["ended_at"])
        return None
    if v.last_seen < now - dt.timedelta(seconds=20):
        Visitor.objects.filter(pk=v.pk).update(last_seen=now)
    return v


def end_visitor(v: Visitor):
    Visitor.objects.filter(pk=v.pk).update(ended_at=timezone.now())


def agree_nda(share: Share, visitor: Visitor, name: str, ip: str):
    name = " ".join(str(name or "").split())[:200]
    if len(name) < 2:
        raise RoomError("Type your full name to agree.")
    now = timezone.now()
    share.nda_name, share.nda_at, share.nda_ip = name, now, ip[:70]
    share.save(update_fields=["nda_name", "nda_at", "nda_ip"])
    Visit.objects.create(share=share, visitor=visitor, kind="nda", ip=ip[:70])
    _log(share.room.deal, None, f"Data room: {name} agreed to the NDA for {share.entry.investor.firm_name}.",
         module="deal_investors", record_id=share.entry_id)


def mark_lines(share: Share, trace: str) -> list[str]:
    firm = share.entry.investor.firm_name
    when = timezone.localtime().strftime("%d %b %Y %H:%M")
    return [f"{share.name} · {firm}", share.email, when, trace]


def start_reading(share: Share, visitor: Visitor, rf: RoomFile, ip: str) -> Visit:
    v = Visit.objects.filter(share=share, visitor=visitor, file=rf, kind="viewed").first()
    if v is None:
        v = Visit.objects.create(share=share, visitor=visitor, file=rf, file_title=rf.title, kind="viewed",
                                 ip=ip[:70], trace=marks.new_trace())
    return v


def reading(visit: Visit, page) -> None:
    """A heartbeat from the viewer every 15 seconds while the page is visible."""
    now = timezone.now()
    gap = (now - visit.last_at).total_seconds()
    add = int(min(gap, 30)) if gap < 90 else 0
    pages = list(visit.pages or [])
    try:
        p = int(page)
        if 1 <= p <= 10_000 and p not in pages and len(pages) < 2000:
            pages.append(p)
    except (TypeError, ValueError):
        pass
    Visit.objects.filter(pk=visit.pk).update(seconds=visit.seconds + add, last_at=now, pages=sorted(pages))


def downloaded(share: Share, visitor: Visitor, rf: RoomFile, ip: str) -> tuple[bytes, str]:
    trace = marks.new_trace()
    data = marks.download(read_file(rf), rf.ext, mark_lines(share, trace), trace)
    Visit.objects.create(share=share, visitor=visitor, file=rf, file_title=rf.title, kind="downloaded", ip=ip[:70],
                         trace=trace)
    return data, trace


def ask(share: Share, visitor: Visitor, rf: RoomFile | None, text: str, page=None) -> Question:
    text = str(text or "").replace("\x00", "").strip()[:5000]
    if len(text) < 3:
        raise RoomError("Write your question first.")
    if Question.objects.filter(share=share, asked_at__gte=timezone.now() - dt.timedelta(hours=1)).count() >= 30:
        raise RoomError("That's a lot of questions in an hour. Please wait a little before asking more.")
    try:
        page = int(page) if page not in (None, "") else None
    except (TypeError, ValueError):
        page = None
    q = Question.objects.create(share=share, file=rf, file_title=rf.title if rf else "", page=page, text=text)
    Visit.objects.create(share=share, visitor=visitor, file=rf, file_title=rf.title if rf else "", kind="question")
    about = f" about “{rf.title}”" + (f", page {page}" if page else "") if rf else ""
    _log(share.room.deal, None, f"Question from {share.name} in the data room{about}: {text}",
         module="deal_investors", record_id=share.entry_id, meta={"room_question": q.pk})
    return q


def answer(request, q: Question, text: str) -> Question:
    user = request.user
    share = q.share
    require_share(user, share.room.deal)
    text = str(text or "").replace("\x00", "").strip()[:10000]
    if len(text) < 2:
        raise ValidationError({"answer": "Write the answer first."})
    first = not q.answer
    q.answer, q.answered_by, q.answered_at = text, user, timezone.now()
    q.save()
    _log(share.room.deal, user, f"Answered {share.name}'s data room question: {text}", module="deal_investors",
         record_id=share.entry_id, meta={"room_question": q.pk})
    if first and working(share):
        with contextlib.suppress(RoomError):  # the answer is in the room either way
            send_quietly(share, f"{share.room.name}: your question has been answered",
                         f"Hi {_first_name(share.name)},\n\nThere's an answer to your question in the {share.room.name} "
                         f"data room. Open it with your link:\n\n{link_for(share, request)}\n\n{user.full_name}")
    return q


# ---------------------------------------------------------------- what bankers see


def suggestion(share: Share, entry: DealInvestor) -> dict | None:
    """A tracker stage the investor's activity points to, for the banker to confirm."""
    if entry.status != "open":
        return None
    target = None
    if Visit.objects.filter(share=share, kind__in=("viewed", "downloaded")).exists():
        target = "collaterals_shared"
    elif share.room.nda_mode != "none" and (share.nda_at or share.nda_received_at):
        target = "nda_signed"
    if target is None or target == share.suggestion_set_aside:
        return None
    if STAGE_ORDER.index(target) <= STAGE_ORDER.index(entry.stage):
        return None
    why = "They've read files in the data room" if target == "collaterals_shared" else "They've agreed to the NDA"
    return {"stage": target, "label": STAGE_LABELS[target], "why": why}


def _nda_state(share: Share) -> dict:
    mode = share.room.nda_mode
    if mode == "none":
        return {"state": "none", "label": "No NDA"}
    if mode == "click":
        return ({"state": "done", "label": f"Agreed on screen as {share.nda_name}", "at": share.nda_at.isoformat()}
                if share.nda_at else {"state": "waiting", "label": "Not agreed yet"})
    if share.nda_received_at:
        return {"state": "done", "label": "Signed NDA received", "at": share.nda_received_at.isoformat()}
    return {"state": "waiting", "label": "Waiting for the signed NDA"}


def _who(share: Share) -> str:
    return f"{share.name} ({share.entry.investor.firm_name})"


def share_status(share: Share) -> tuple[str, str]:
    now = timezone.now()
    if share.status == "revoked":
        return "stopped", "Stopped"
    if share.status == "waiting":
        return "waiting", "Waiting for approval"
    if share.expires_at <= now:
        return "expired", "Ran out"
    if locked(share):
        return "locked", "Paused after wrong codes"
    if not share.room.is_open:
        return "closed", "Room closed"
    return "active", "Working"


def share_payload(share: Share, show_email=True) -> dict:
    stats = share.visits.aggregate(
        seconds=Sum("seconds", filter=Q(kind="viewed")),
        files=Count("file", filter=Q(kind="viewed"), distinct=True),
        downloads=Count("id", filter=Q(kind="downloaded")),
        opens=Count("id", filter=Q(kind="opened")))
    status, label = share_status(share)
    return {
        "id": share.pk, "entry": share.entry_id, "investor": {"id": share.entry.investor_id, "title": share.entry.investor.firm_name},
        "name": share.name, "email": share.email if show_email else "Hidden for your role",
        "status": status, "status_label": label,
        "expires_at": share.expires_at.isoformat(), "created_at": share.created_at.isoformat(),
        "created_by": share.created_by.full_name if share.created_by_id else None,
        "invited_at": share.invited_at.isoformat() if share.invited_at else None, "invite_error": share.invite_error,
        "nda": _nda_state(share),
        "first_opened_at": share.first_opened_at.isoformat() if share.first_opened_at else None,
        "last_opened_at": share.last_opened_at.isoformat() if share.last_opened_at else None,
        "opens": stats["opens"] or 0, "seconds": stats["seconds"] or 0, "files_read": stats["files"] or 0,
        "downloads": stats["downloads"] or 0,
        "open_questions": share.questions.filter(answered_at__isnull=True).count(),
        "suggestion": suggestion(share, share.entry),
    }


def visit_payload(v: Visit) -> dict:
    return {"id": v.pk, "at": v.at.isoformat(), "last_at": v.last_at.isoformat(), "kind": v.kind,
            "label": v.get_kind_display(), "who": _who(v.share), "share": v.share_id, "entry": v.share.entry_id,
            "file": v.file_title, "seconds": v.seconds, "pages": v.pages[:50], "trace": v.trace, "ip": v.ip}


def question_payload(q: Question, show_email=True) -> dict:
    return {"id": q.pk, "share": q.share_id, "who": _who(q.share), "entry": q.share.entry_id,
            "file": q.file_title, "page": q.page, "text": q.text, "asked_at": q.asked_at.isoformat(),
            "answer": q.answer, "answered_by": q.answered_by.full_name if q.answered_by_id else None,
            "answered_at": q.answered_at.isoformat() if q.answered_at else None}


def file_payload(rf: RoomFile) -> dict:
    stats = rf.visits.aggregate(readers=Count("share", filter=Q(kind="viewed"), distinct=True),
                                seconds=Sum("seconds", filter=Q(kind="viewed")),
                                downloads=Count("id", filter=Q(kind="downloaded")))
    return {"id": rf.pk, "document": rf.document_id, "title": rf.title, "file_name": rf.file_name, "ext": rf.ext,
            "file_type": rf.file_type, "size": rf.file_size, "mode": rf.mode, "pages": rf.pages,
            "can_view_only": rf.ext in marks.VIEWABLE, "position": rf.position,
            "added_by": rf.added_by.full_name if rf.added_by_id else None, "added_at": rf.added_at.isoformat(),
            "readers": stats["readers"] or 0, "seconds": stats["seconds"] or 0, "downloads": stats["downloads"] or 0}


def payload(request, deal: Deal) -> dict:
    from core import access as field_access

    user = request.user
    show_email = field_access.can_see("contacts", "work_email", user)
    room = get_room(deal)
    sharing = can_share(user, deal)
    from mail.models import MailAccount

    out = {
        "deal": deal.pk, "exists": room is not None,
        "can": {"share": sharing, "flag": user.has_flag("share_with_investors")},
        "rooms_domain": getattr(settings, "ROOMS_DOMAIN", "") or None,
        "base_url": base_url(request),
        "mailbox": MailAccount.objects.filter(user=user, needs_reconnect=False).exists() or bool(
            getattr(settings, "ROOMS_SMTP_URL", "")),
        "nda_modes": [{"value": k, "label": v} for k, v in Room.NDA_MODES],
        "days": list(DAYS),
    }
    if room is None:
        out.update({"room": None, "files": [], "shares": [], "activity": [], "questions": [], "by_entry": {}})
        return out
    shares = list(room.shares.select_related("entry__investor", "created_by", "room__deal"))
    share_rows = [share_payload(s, show_email) for s in shares]
    by_entry: dict[int, dict] = {}
    for row in share_rows:
        e = by_entry.setdefault(row["entry"], {"shares": 0, "opens": 0, "seconds": 0, "files_read": 0,
                                               "last_opened_at": None, "suggestion": None, "share": row["id"]})
        e["shares"] += 1
        for k in ("opens", "seconds", "files_read"):
            e[k] += row[k]
        if row["last_opened_at"] and (not e["last_opened_at"] or row["last_opened_at"] > e["last_opened_at"]):
            e["last_opened_at"] = row["last_opened_at"]
        if row["suggestion"] and (not e["suggestion"] or STAGE_ORDER.index(row["suggestion"]["stage"])
                                  > STAGE_ORDER.index(e["suggestion"]["stage"])):
            e["suggestion"] = {**row["suggestion"], "share": row["id"]}
    visits = Visit.objects.filter(share__room=room).exclude(kind="code_sent").select_related(
        "share__entry__investor")[:60]
    questions = Question.objects.filter(share__room=room).select_related("share__entry__investor", "answered_by")[:200]
    out.update({
        "room": {"id": room.pk, "name": room.name, "welcome": room.welcome, "nda_mode": room.nda_mode,
                 "nda_text": room.nda_text, "is_open": room.is_open, **approval_state(room)},
        "files": [file_payload(f) for f in room.files.select_related("added_by")],
        "shares": share_rows,
        "activity": [visit_payload(v) for v in visits],
        "questions": [question_payload(q) for q in questions],
        "by_entry": by_entry,
    })
    return out


def candidates(deal: Deal) -> list[dict]:
    entries = DealInvestor.objects.filter(deal=deal, archived_at__isnull=True).select_related("investor").order_by(
        "investor__firm_name")
    return [{"entry": e.pk, "investor": e.investor.firm_name, "stage": e.get_stage_display(), "status": e.status,
             "contacts": entry_contacts(e)} for e in entries]


def trace(user, deal: Deal, data: bytes) -> list[dict]:
    if not can_view(user, deal):
        raise PermissionDenied("You can't see this deal.")
    tags = marks.find_trace(data)
    if not tags:
        return []
    found = Visit.objects.filter(trace__in=tags, share__room__deal=deal).select_related("share__entry__investor")
    return [{"trace": v.trace, **visit_payload(v)} for v in found]


def alerts(user) -> list[dict]:
    """Unanswered investor questions on deals this person can answer, for the bell."""
    if not user.has_flag("share_with_investors"):
        return []
    qs = Question.objects.filter(answered_at__isnull=True, share__room__deal__archived_at__isnull=True).select_related(
        "share__room__deal__company", "share__entry__investor")[:200]
    out = []
    for q in qs:
        deal = q.share.room.deal
        if can_share(user, deal):
            out.append({"key": f"roomq:{q.pk}", "id": q.pk, "deal": deal.pk, "deal_title": deal.company.name,
                        "who": _who(q.share), "text": q.text[:200], "file": q.file_title, "at": q.asked_at.isoformat()})
    return out
