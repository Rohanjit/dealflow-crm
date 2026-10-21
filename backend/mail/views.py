"""Email from Dealflow (decision 62): Settings > Email (admins), each person's mailbox, one-to-one
emails from record pages, templates and campaigns."""

from __future__ import annotations

import datetime as dt
import secrets
import urllib.parse

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser
from core import current
from core.guest import NotInSample, NotOnDemo, sample_loaded
from core.records import text_in, to_id

from . import campaigns as cmp
from . import crypto, service
from .models import Campaign, CampaignRecipient, EmailTemplate, MailAccount, MailSettings
from .providers import PROVIDERS, MailError, pkce_pair

SESSION_KEY = "mail_connect"


def _err(e: MailError):
    code = {"setup": "mail_setup", "auth": "mail_reconnect", "limit": "mail_limit"}.get(e.kind, "mail_failed")
    return Response({"detail": str(e), "code": code},
                    status=status.HTTP_429_TOO_MANY_REQUESTS if e.kind == "limit" else status.HTTP_400_BAD_REQUEST)


def _text(data, key, limit, label, required=False) -> str:
    v = data.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        raise ValidationError({key: f"{label} must be text."})
    v = text_in(v)[:limit]
    if required and not v.strip():
        raise ValidationError({key: f"Write a {label.lower()}."})
    return v


class IsAdmin(IsActiveUser):
    message = "Only admins can change this."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin


# ---------------------------------------------------------------- Settings > Email (admins)


class MailSettingsView(APIView):
    permission_classes = [IsAdmin, NotInSample, NotOnDemo]

    def _payload(self, request):
        ms = MailSettings.load()
        return {
            "gmail": {"client_id": service.app_config("gmail").client_id if service.from_env("gmail") else ms.google_client_id,
                      "has_secret": bool(crypto.unseal(ms.google_client_secret)) or service.from_env("gmail"),
                      "from_env": service.from_env("gmail"), "ready": bool(service.app_config("gmail")),
                      "redirect_uri": service.redirect_uri(request, "gmail")},
            "outlook": {"client_id": service.app_config("outlook").client_id if service.from_env("outlook")
                        else ms.microsoft_client_id,
                        "tenant": ms.microsoft_tenant,
                        "has_secret": bool(crypto.unseal(ms.microsoft_client_secret)) or service.from_env("outlook"),
                        "from_env": service.from_env("outlook"), "ready": bool(service.app_config("outlook")),
                        "redirect_uri": service.redirect_uri(request, "outlook")},
            "public_url": ms.public_url,
            "gmail_daily_limit": ms.gmail_daily_limit,
            "outlook_daily_limit": ms.outlook_daily_limit,
            "per_minute": ms.per_minute,
            "opt_out_text": ms.opt_out_text,
            "connected": MailAccount.objects.count(),
        }

    def get(self, request):
        return Response(self._payload(request))

    def put(self, request):
        pw = request.data.get("password")
        if not isinstance(pw, str) or not request.user.check_password(pw):
            raise ValidationError({"password": "Enter your password to save email settings."})
        ms = MailSettings.load()
        d = request.data
        errors = {}
        for key, attr in (("gmail_client_id", "google_client_id"), ("outlook_client_id", "microsoft_client_id")):
            if key in d:
                v = d[key]
                if not isinstance(v, str) or len(v.strip()) > 255:
                    errors[key] = "Paste the client ID (up to 255 characters)."
                else:
                    setattr(ms, attr, text_in(v).strip())
        for key, attr in (("gmail_client_secret", "google_client_secret"),
                          ("outlook_client_secret", "microsoft_client_secret")):
            if key in d and d[key] is not None:
                v = d[key]
                if not isinstance(v, str) or len(v) > 500:
                    errors[key] = "Paste the client secret."
                elif v.strip():
                    setattr(ms, attr, crypto.seal(v.strip()))
        if d.get("clear_gmail") is True:
            ms.google_client_id, ms.google_client_secret = "", ""
        if d.get("clear_outlook") is True:
            ms.microsoft_client_id, ms.microsoft_client_secret = "", ""
        if "outlook_tenant" in d:
            v = d["outlook_tenant"]
            if not isinstance(v, str) or not (v.strip() == "" or len(v) <= 100 and all(
                    ch.isalnum() or ch in ".-" for ch in v.strip())):
                errors["outlook_tenant"] = "Use “common”, “organizations” or your directory (tenant) ID or domain."
            else:
                ms.microsoft_tenant = v.strip() or "common"
        if "public_url" in d:
            v = d["public_url"]
            ok = isinstance(v, str) and len(v) <= 300
            parsed = urllib.parse.urlparse(v.strip()) if ok else None
            if not ok or (v.strip() and (parsed.scheme not in ("https", "http") or not parsed.netloc
                                         or parsed.path not in ("", "/") or parsed.query)):
                errors["public_url"] = "Enter the address people open Dealflow at, like https://crm.yourfirm.com"
            else:
                ms.public_url = v.strip().rstrip("/")
        for key, lo, hi in (("gmail_daily_limit", 1, 2000), ("outlook_daily_limit", 1, 10000), ("per_minute", 1, 60)):
            if key in d:
                v = d[key]
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    errors[key] = f"Enter a whole number from {lo} to {hi:,}."
                else:
                    setattr(ms, key, v)
        if "opt_out_text" in d:
            v = d["opt_out_text"]
            if not isinstance(v, str) or not v.strip() or len(v) > 300:
                errors["opt_out_text"] = "Campaigns need an opt-out line (up to 300 characters)."
            else:
                ms.opt_out_text = text_in(v).strip()
        if errors:
            raise ValidationError(errors)
        ms.save()
        return Response(self._payload(request))


# ---------------------------------------------------------------- my mailbox


def _providers():
    return [{"key": k, "label": p.label, "ready": bool(service.app_config(k))} for k, p in PROVIDERS.items()]


class MailAccountView(APIView):
    def get(self, request):
        return Response({
            "account": service.account_payload(request.user),
            "providers": _providers(),
            "can_campaign": request.user.has_flag("send_campaigns"),
            "merge_fields": [{"key": k, "label": label} for k, label in cmp.MERGE_FIELDS],
            "in_sample": sample_loaded(),
        })

    def delete(self, request):
        a = MailAccount.objects.filter(user=request.user).first()
        if a:
            tokens = crypto.unseal(a.tokens) or {}
            if a.provider == "gmail" and tokens.get("refresh"):
                try:  # best effort: tell Google to forget the permission too
                    from .providers import http

                    http("POST", "https://oauth2.googleapis.com/revoke",
                         {"Content-Type": "application/x-www-form-urlencoded"},
                         urllib.parse.urlencode({"token": tokens["refresh"]}).encode())
                except MailError:
                    pass
            current.note(f"Disconnected {PROVIDERS[a.provider].label} mailbox {a.email}")
            a.delete()
            Campaign.objects.filter(owner=request.user, status__in=["sending", "scheduled"]).update(
                status="paused", paused_reason="Reconnect your mailbox in Settings > My preferences to carry on.",
                resume_at=None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MailConnectView(APIView):
    permission_classes = [IsActiveUser, NotInSample, NotOnDemo]

    def post(self, request, provider):
        if provider not in PROVIDERS:
            raise NotFound("Pick Gmail or Outlook.")
        if sample_loaded():
            raise PermissionDenied("Mailboxes can't be connected while the sample firm is loaded.")
        cfg = service.app_config(provider)
        if not cfg:
            raise ValidationError({"detail": f"{PROVIDERS[provider].label} isn't set up for this install yet. "
                                             "An admin can do it in Settings > Email.", "code": "mail_setup"})
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        back = request.data.get("next")
        back = back if isinstance(back, str) and back.startswith("/") and not back.startswith("//") else "/settings"
        request.session[SESSION_KEY] = {"state": state, "verifier": verifier, "provider": provider,
                                        "next": back[:300], "at": timezone.now().timestamp()}
        url = PROVIDERS[provider].authorize_url(cfg, service.redirect_uri(request, provider), state, challenge)
        return Response({"url": url})


def _back(path: str, **params) -> HttpResponseRedirect:
    sep = "&" if "?" in path else "?"
    return HttpResponseRedirect(path + sep + urllib.parse.urlencode(params))


@require_GET
def mail_callback(request, provider):
    """Google or Microsoft sends the person back here after they agree (or decline)."""
    if not request.user.is_authenticated or not request.user.is_active:
        return HttpResponseRedirect("/login")
    pending = request.session.pop(SESSION_KEY, None) or {}
    back = pending.get("next") or "/settings"
    if provider not in PROVIDERS or pending.get("provider") != provider:
        return _back(back, mailbox="error", reason="That sign-in link has expired. Try connecting again.")
    if not secrets.compare_digest(str(request.GET.get("state", "")), str(pending.get("state", ""))) or \
            timezone.now().timestamp() - pending.get("at", 0) > 15 * 60:
        return _back(back, mailbox="error", reason="That sign-in link has expired. Try connecting again.")
    if request.GET.get("error"):
        reason = "You didn't give Dealflow permission, so nothing was connected." if \
            request.GET.get("error") in ("access_denied", "consent_required") else \
            f"{PROVIDERS[provider].label} said: {request.GET.get('error_description') or request.GET.get('error')}"[:200]
        return _back(back, mailbox="error", reason=reason)
    code = request.GET.get("code")
    cfg = service.app_config(provider)
    if not code or not cfg:
        return _back(back, mailbox="error", reason="Something was missing from the sign-in. Try again.")
    try:
        tokens, email = PROVIDERS[provider].exchange(cfg, code, service.redirect_uri(request, provider),
                                                     pending["verifier"])
    except MailError as e:
        return _back(back, mailbox="error", reason=str(e)[:200])
    service.connect(request.user, provider, tokens, email)
    current.note(f"Connected {PROVIDERS[provider].label} mailbox {email}")
    return _back(back, mailbox="connected")


mail_callback = login_required(mail_callback, login_url="/login")


# ---------------------------------------------------------------- one-to-one


class AttachmentUploadView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        from vault.files import FileRejected

        files = request.FILES.getlist("files")
        if not files:
            raise ValidationError({"files": "Choose a file."})
        if len(files) > 20:
            raise ValidationError({"files": "Attach at most 20 files."})
        out = []
        for f in files:
            try:
                out.append(service.save_upload(request.user, f))
            except FileRejected as e:
                raise ValidationError({"files": str(e)})
        return Response(out, status=status.HTTP_201_CREATED)


def _about(request, raw, action="edit"):
    try:
        return service.about_record(raw, request.user, action)
    except PermissionError as e:
        raise PermissionDenied(str(e))
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise ValidationError({"about": str(e)})


class RecipientSuggestionsView(APIView):
    def get(self, request):
        mod, obj = _about(request, request.query_params.get("about"), "view")
        return Response({"about": {"module": mod.key, "id": obj.pk, "title": mod.title_of(obj)},
                         "can_log": request.user.can(mod.key, "edit"),
                         "people": service.suggestions(mod, obj, request.user)})


class SendView(APIView):
    def post(self, request):
        d = request.data
        mod, obj = _about(request, d.get("about"))
        try:
            to = service.clean_addresses(d.get("to"), "To")
            cc = service.clean_addresses(d.get("cc"), "Cc")
            bcc = service.clean_addresses(d.get("bcc"), "Bcc")
            attachments = service.clean_attachments(d.get("attachments"), request.user, request)
        except ValueError as e:
            raise ValidationError({"detail": str(e)})
        if not to:
            raise ValidationError({"to": "Add at least one person to send to."})
        if len(to) + len(cc) + len(bcc) > service.MAX_RECIPIENTS:
            raise ValidationError({"to": f"One email can go to at most {service.MAX_RECIPIENTS} people. "
                                         "Use a campaign for more."})
        subject = " ".join(_text(d, "subject", 300, "Subject", required=True).split())
        body = _text(d, "body", 100_000, "Message", required=True)
        try:
            se = service.send_email(request.user, to=to, cc=cc, bcc=bcc, subject=subject, text=body,
                                    attachments=attachments, records=[(mod, obj)])
        except MailError as e:
            return _err(e)
        current.set_subject(mod.key, obj.pk, mod.title_of(obj))
        current.add_change({"record": f"{mod.key}:{obj.pk}", "kind": "email sent",
                            "changes": [{"field": "Email", "from": "", "to": f"{subject} → {', '.join(to)}"}]})
        return Response(service.sent_payload(se), status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- templates


def _template_payload(t: EmailTemplate, user) -> dict:
    return {"id": t.pk, "name": t.name, "subject": t.subject, "body": t.body, "shared": t.shared,
            "mine": t.owner_id == user.pk, "owner": t.owner.full_name, "updated_at": t.updated_at.isoformat()}


def _clean_template(d, partial=False) -> dict:
    out = {}
    if "name" in d or not partial:
        out["name"] = " ".join(_text(d, "name", 120, "Name", required=True).split())
    if "subject" in d or not partial:
        out["subject"] = " ".join(_text(d, "subject", 300, "Subject").split())
    if "body" in d or not partial:
        out["body"] = _text(d, "body", 100_000, "Message")
    if "shared" in d:
        if not isinstance(d["shared"], bool):
            raise ValidationError({"shared": "Send true or false."})
        out["shared"] = d["shared"]
    if bad := cmp.unknown_fields(out.get("subject", ""), out.get("body", "")):
        raise ValidationError({"body": "Unknown merge fields: " + ", ".join("{" + b + "}" for b in bad)})
    return out


class TemplateListView(APIView):
    def get(self, request):
        from django.db.models import Q

        qs = EmailTemplate.objects.filter(Q(owner=request.user) | Q(shared=True)).select_related("owner")
        return Response([_template_payload(t, request.user) for t in qs])

    def post(self, request):
        if EmailTemplate.objects.filter(owner=request.user).count() >= 200:
            raise ValidationError({"detail": "You have 200 templates; delete some first."})
        t = EmailTemplate.objects.create(owner=request.user, **_clean_template(request.data))
        return Response(_template_payload(t, request.user), status=status.HTTP_201_CREATED)


class TemplateDetailView(APIView):
    def _get(self, request, pk, write=True):
        t = EmailTemplate.objects.filter(pk=pk).select_related("owner").first()
        if not t or (t.owner_id != request.user.pk and not t.shared):
            raise NotFound("This template doesn't exist.")
        if write and t.owner_id != request.user.pk and not (request.user.is_admin and t.shared):
            raise PermissionDenied("Only the person who made this template can change it.")
        return t

    def patch(self, request, pk):
        t = self._get(request, pk)
        for k, v in _clean_template(request.data, partial=True).items():
            setattr(t, k, v)
        t.save()
        return Response(_template_payload(t, request.user))

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- campaigns


def _campaign_qs(user):
    qs = Campaign.objects.select_related("owner")
    return qs if user.is_admin else qs.filter(owner=user)


def campaign_payload(c: Campaign, user, full=False) -> dict:
    out = {
        "id": c.pk, "name": c.name, "status": c.status, "status_label": c.get_status_display(),
        "owner": {"id": c.owner_id, "name": c.owner.full_name}, "mine": c.owner_id == user.pk,
        "subject": c.subject, "send_at": c.send_at.isoformat() if c.send_at else None,
        "paused_reason": c.paused_reason, "resume_at": c.resume_at.isoformat() if c.resume_at else None,
        "created_at": c.created_at.isoformat(), "started_at": c.started_at.isoformat() if c.started_at else None,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None, "counts": cmp.counts(c),
    }
    if full:
        out.update({"body": c.body, "attachments": c.attachments, "problems": cmp.problems(c),
                    "opt_out_text": cmp.opt_out_line(), "editable": c.status in ("draft", "scheduled", "paused")
                    and c.owner_id == user.pk, "source": c.source,
                    "confirmed": CampaignRecipient.objects.filter(campaign=c, sent_email__status="confirmed").count(),
                    "per_minute": MailSettings.load().per_minute, "mailbox": service.account_payload(c.owner)})
    return out


def _add_people(request, c: Campaign, d) -> dict:
    spec = d.get("recipients")
    if spec is None:
        return {"added": 0, "skipped": 0}
    if not isinstance(spec, dict):
        raise ValidationError({"recipients": "Say who the campaign goes to."})
    try:
        query = spec.get("query")
        if query is not None and not isinstance(query, dict):
            raise ValueError("The list filters were sent wrongly.")
        people = cmp.resolve_contacts(
            request.user,
            contact_ids=spec.get("contacts") if isinstance(spec.get("contacts"), list) else None,
            investor_ids=spec.get("investors") if isinstance(spec.get("investors"), list) else None,
            query=query, module=spec.get("module") or "contacts",
            who="all" if spec.get("who") == "all" else "primary")
        result = cmp.add_recipients(c, people)
    except PermissionError as e:
        raise PermissionDenied(str(e))
    except ValueError as e:
        raise ValidationError({"recipients": str(e)})
    return result


def _clean_campaign(request, d, partial=False) -> dict:
    out = {}
    if "name" in d or not partial:
        out["name"] = " ".join(_text(d, "name", 120, "Name", required=True).split())
    if "subject" in d:
        out["subject"] = " ".join(_text(d, "subject", 300, "Subject").split())
    if "body" in d:
        out["body"] = _text(d, "body", 100_000, "Message")
    if "attachments" in d:
        try:
            out["attachments"] = service.clean_attachments(d["attachments"], request.user, request, for_campaign=True)
        except ValueError as e:
            raise ValidationError({"attachments": str(e)})
    if "send_at" in d:
        v = d["send_at"]
        if v in (None, ""):
            out["send_at"] = None
        else:
            try:
                when = dt.datetime.fromisoformat(str(v))
            except ValueError:
                raise ValidationError({"send_at": "Pick a date and time."})
            if timezone.is_naive(when):
                when = timezone.make_aware(when)
            if when > timezone.now() + dt.timedelta(days=366):
                raise ValidationError({"send_at": "Pick a time within the next year."})
            out["send_at"] = when
    if bad := cmp.unknown_fields(out.get("subject", ""), out.get("body", "")):
        raise ValidationError({"body": "Unknown merge fields: " + ", ".join("{" + b + "}" for b in bad)})
    return out


def _can_campaign(user):
    if not user.has_flag("send_campaigns"):
        raise PermissionDenied("Your role can't send campaigns. An admin can turn on “Send campaigns” for it.")


class CampaignListView(APIView):
    def get(self, request):
        qs = _campaign_qs(request.user)[:200]
        return Response([campaign_payload(c, request.user) for c in qs])

    def post(self, request):
        _can_campaign(request.user)
        values = _clean_campaign(request, request.data)
        if "subject" not in values and (tid := to_id(request.data.get("template"))):
            t = EmailTemplate.objects.filter(pk=tid).first()
            if t and (t.owner_id == request.user.pk or t.shared):
                values.update(subject=t.subject, body=t.body)
        c = Campaign.objects.create(owner=request.user, **values)
        spec = request.data.get("recipients")
        if isinstance(spec, dict):
            c.source = {k: spec[k] for k in ("module", "query", "who") if k in spec}
            c.save(update_fields=["source"])
        result = _add_people(request, c, request.data)
        current.note(f"Started a campaign draft “{c.name}”")
        return Response({**campaign_payload(c, request.user, full=True), "result": result},
                        status=status.HTTP_201_CREATED)


class CampaignDetailView(APIView):
    def _get(self, request, pk, write=False) -> Campaign:
        c = _campaign_qs(request.user).filter(pk=pk).first()
        if not c:
            raise NotFound("This campaign doesn't exist.")
        if write and c.owner_id != request.user.pk:
            raise PermissionDenied("Only the person sending this campaign can change it.")
        return c

    def get(self, request, pk):
        return Response(campaign_payload(self._get(request, pk), request.user, full=True))

    def patch(self, request, pk):
        _can_campaign(request.user)
        c = self._get(request, pk, write=True)
        if c.status not in ("draft", "scheduled", "paused"):
            raise ValidationError({"detail": "A campaign can't be changed once it has finished."})
        for k, v in _clean_campaign(request, request.data, partial=True).items():
            setattr(c, k, v)
        c.save()
        result = _add_people(request, c, request.data)
        return Response({**campaign_payload(c, request.user, full=True), "result": result})

    def delete(self, request, pk):
        c = self._get(request, pk, write=not request.user.is_admin)
        if c.status in ("sending", "scheduled"):
            raise ValidationError({"detail": "Pause or cancel the campaign before deleting it."})
        current.note(f"Deleted campaign “{c.name}”")
        c.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CampaignActionView(APIView):
    def post(self, request, pk, action):
        c = CampaignDetailView()._get(request, pk, write=action not in ("pause", "cancel") or not request.user.is_admin)
        now = timezone.now()
        if action == "start":
            _can_campaign(request.user)
            if c.status not in ("draft",):
                raise ValidationError({"detail": "This campaign has already been started."})
            if probs := cmp.problems(c):
                raise ValidationError({"detail": " ".join(probs)})
            try:
                box = service.mailbox_for(request.user)
                service.read_attachments(c.attachments or [], request.user)
            except MailError as e:
                return _err(e)
            del box  # only checked: the scheduler sends
            c.status = "scheduled" if c.send_at and c.send_at > now else "sending"
            c.started_at = now
            c.paused_reason, c.resume_at = "", None
            c.save()
            current.note(f"Started campaign “{c.name}” to {cmp.counts(c)['queued']} people")
        elif action == "pause":
            if c.status not in ("sending", "scheduled"):
                raise ValidationError({"detail": "Only a campaign that's sending or scheduled can be paused."})
            c.status, c.paused_reason, c.resume_at = "paused", f"Paused by {request.user.full_name}.", None
            c.save()
        elif action == "resume":
            _can_campaign(request.user)
            if c.status != "paused":
                raise ValidationError({"detail": "This campaign isn't paused."})
            c.status = "scheduled" if c.send_at and c.send_at > now else "sending"
            c.paused_reason, c.resume_at = "", None
            c.save()
        elif action == "cancel":
            if c.status in ("done", "cancelled"):
                raise ValidationError({"detail": "This campaign has already finished."})
            c.status, c.finished_at = "cancelled", now
            c.save()
            c.recipients.filter(status="queued").update(status="skipped", reason="Campaign cancelled")
            current.note(f"Cancelled campaign “{c.name}”")
        else:
            raise NotFound("Unknown action.")
        return Response(campaign_payload(c, request.user, full=True))


class CampaignRecipientsView(APIView):
    def get(self, request, pk):
        c = CampaignDetailView()._get(request, pk)
        qs = c.recipients.select_related("contact", "sent_email")
        st = request.query_params.get("status")
        if st in dict(CampaignRecipient.STATUSES):
            qs = qs.filter(status=st)
        q = (request.query_params.get("q") or "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
        from core.pagination import StandardPagination

        pager = StandardPagination()
        page = pager.paginate_queryset(qs, request, view=self)
        return pager.get_paginated_response([{
            "id": r.pk, "name": r.name, "email": r.email, "contact_id": r.contact_id, "status": r.status,
            "status_label": r.get_status_display(), "reason": r.reason,
            "sent_at": r.sent_at.isoformat() if r.sent_at and r.status == "sent" else None,
            "confirmed": bool(r.sent_email and r.sent_email.status == "confirmed"),
            "confirm_status": r.sent_email.status if r.sent_email else None,
            "web_link": r.sent_email.web_link if r.sent_email else "",
        } for r in page])

    def delete(self, request, pk):
        c = CampaignDetailView()._get(request, pk, write=True)
        if c.status in ("done", "cancelled"):
            raise ValidationError({"detail": "This campaign has finished."})
        ids = request.data.get("ids")
        if not isinstance(ids, list):
            raise ValidationError({"ids": "Say which people to take off."})
        ids = [i for i in (to_id(x) for x in ids) if i is not None]
        n, _ = c.recipients.filter(pk__in=ids, status__in=["queued", "skipped", "failed"]).delete()
        return Response({"removed": n, **campaign_payload(c, request.user, full=True)})


class CampaignPreviewView(APIView):
    """The email as one person will get it, plus which merge fields are empty for how many."""

    def get(self, request, pk):
        c = CampaignDetailView()._get(request, pk)
        qs = c.recipients.exclude(status="skipped").select_related("contact__investor", "contact__company")
        rid = to_id(request.query_params.get("recipient"))
        r = (qs.filter(pk=rid).first() if rid else None) or qs.first()
        values = cmp.merge_values(r.contact if r else None, c.owner)
        subject, body = cmp.compose(c, values)
        used = cmp.used_fields(c.subject, c.body)
        empty = {}
        if used:
            for other in qs.filter(contact__isnull=False)[:cmp.MAX_RECIPIENTS]:
                vals = cmp.merge_values(other.contact, c.owner)
                for k in used:
                    if not (vals.get(k) or "").strip():
                        empty[k] = empty.get(k, 0) + 1
        labels = dict(cmp.MERGE_FIELDS)
        return Response({
            "recipient": {"id": r.pk, "name": r.name, "email": r.email} if r else None,
            "subject": subject, "body": body,
            "warnings": [f"{n} {'person has' if n == 1 else 'people have'} no {labels[k].lower()}: write it as "
                         f"{{{k}|…}} to give a fallback." for k, n in sorted(empty.items())],
        })


class CampaignTestView(APIView):
    """Send the email as the first person will get it, to yourself, without logging it anywhere."""

    def post(self, request, pk):
        _can_campaign(request.user)
        c = CampaignDetailView()._get(request, pk, write=True)
        r = c.recipients.exclude(status="skipped").select_related("contact__investor", "contact__company").first()
        values = cmp.merge_values(r.contact if r else None, request.user)
        subject, body = cmp.compose(c, values)
        try:
            box = service.mailbox_for(request.user)
            files = service.read_attachments(c.attachments or [], request.user)
            from .providers import Outgoing

            out = Outgoing(from_name=request.user.full_name, from_email=box.email, to=[("", box.email)],
                           subject=f"[Test] {subject}", text=body, attachments=files, unsubscribe=True)
            sent = box.provider.send(box, out)
        except MailError as e:
            return _err(e)
        service._count(box.account)
        return Response({"to": box.email, "web_link": sent.web_link})
