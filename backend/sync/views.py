"""Email and calendar logging API (decision 64)."""

from __future__ import annotations

import secrets

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser
from core import current
from core.guest import NotInSample, NotOnDemo, sample_loaded
from core.records import to_id
from mail import service as mail_service
from mail.providers import MailError, pkce_pair
from mail.views import SESSION_KEY

from . import engine, providers, service
from .models import Connection

OAUTH = {"google": "gmail", "microsoft": "outlook"}


class SyncView(APIView):
    def get(self, request):
        return Response({**service.mine(request.user), "in_sample": sample_loaded()})

    def put(self, request):
        return Response(service.set_reminder(request.user, request.data.get("reminder_minutes")))


class SyncConnectView(APIView):
    permission_classes = [IsActiveUser, NotInSample, NotOnDemo]

    def post(self, request, kind):
        if kind not in ("google", "microsoft", "icloud", "other"):
            raise NotFound("Pick Google, Microsoft 365, iCloud or another provider.")
        if sample_loaded():
            raise PermissionDenied("Mailboxes can't be connected while the sample firm is loaded.")
        mail, calendar = request.data.get("mail") is not False, request.data.get("calendar") is not False
        if not (mail or calendar):
            raise ValidationError({"detail": "Pick email, calendar or both."})
        if kind in OAUTH:
            provider = OAUTH[kind]
            cfg = mail_service.app_config(provider)
            if not cfg:
                raise ValidationError({"detail": f"{'Google' if kind == 'google' else 'Microsoft'} isn't set up for this "
                                                 "install yet. An admin can do it in Settings > Email.",
                                       "code": "mail_setup"})
            state = secrets.token_urlsafe(24)
            verifier, challenge = pkce_pair()
            back = request.data.get("next")
            back = back if isinstance(back, str) and back.startswith("/") and not back.startswith("//") else "/settings"
            request.session[SESSION_KEY] = {"state": state, "verifier": verifier, "provider": provider, "purpose": "sync",
                                            "kind": kind, "mail": mail, "calendar": calendar, "next": back[:300],
                                            "at": timezone.now().timestamp()}
            url = providers.authorize_url(kind, cfg, mail_service.redirect_uri(request, provider), state, challenge,
                                          mail, calendar)
            return Response({"url": url})
        conn = service.connect_password(request.user, kind, request.data)
        current.note(f"Connected {conn.get_kind_display()} logging for {conn.email}")
        return Response(service.connection_payload(conn), status=status.HTTP_201_CREATED)


def _mine(request, pk) -> Connection:
    conn = Connection.objects.filter(pk=to_id(pk), user=request.user).first()
    if conn is None:
        raise NotFound("This connection doesn't exist.")
    return conn


class SyncConnectionView(APIView):
    def patch(self, request, pk):
        conn = _mine(request, pk)
        changed = []
        for key in ("mail", "calendar"):
            v = request.data.get(key)
            if v is None:
                continue
            if not isinstance(v, bool):
                raise ValidationError({key: "Choose yes or no."})
            if v and not getattr(conn, key):
                raise ValidationError({key: "To add this, connect again and allow it."})
            if not v and getattr(conn, key):
                setattr(conn, key, False)
                changed.append(key)
        if not (conn.mail or conn.calendar):
            raise ValidationError({"detail": "Keep email or calendar on, or disconnect."})
        conn.save(update_fields=["mail", "calendar"])
        return Response(service.connection_payload(conn))

    def delete(self, request, pk):
        service.disconnect(_mine(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class SyncNowView(APIView):
    def post(self, request, pk):
        conn = _mine(request, pk)
        counts = engine.sync_connection(conn)
        engine.finish_meetings()
        conn.refresh_from_db()
        return Response({**counts, "connection": service.connection_payload(conn)})


class ItemView(APIView):
    def get(self, request, pk):
        return Response(service.open_item(request.user, to_id(pk)))


class ItemSaveView(APIView):
    def post(self, request, pk):
        index = request.data.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValidationError({"index": "Pick an attachment."})
        title = request.data.get("title") if isinstance(request.data.get("title"), str) else ""
        return Response(service.save_attachment(request, to_id(pk), index, title), status=status.HTTP_201_CREATED)


class ItemFileView(APIView):
    def post(self, request, pk):
        ref = request.data.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise ValidationError({"ref": "Pick a deal."})
        return Response(service.file_item(request.user, to_id(pk), ref))


class AgendaView(APIView):
    def get(self, request):
        return Response(service.agenda(request.user))


class MeetingsView(APIView):
    def get(self, request):
        raw = request.query_params.get("about") or ""
        mkey, _, rid = raw.partition(":")
        if mkey not in ("contacts", "investors", "companies", "outreach", "deals", "deal_investors") or not to_id(rid):
            raise ValidationError({"about": "Say which record."})
        return Response(service.meetings_about(request.user, mkey, to_id(rid)))


class ScheduleView(APIView):
    def post(self, request):
        return Response(service.schedule(request.user, request.data), status=status.HTTP_201_CREATED)


class SyncOverviewView(APIView):
    def get(self, request):
        if not request.user.is_admin:
            raise PermissionDenied("Only admins can see who has connected.")
        return Response(service.overview())


def finish_callback(request, pending: dict, code: str):
    """Called by the shared mail callback when the person comes back from Google or Microsoft."""
    kind, provider = pending.get("kind"), pending.get("provider")
    cfg = mail_service.app_config(provider)
    if not cfg:
        raise MailError("This provider isn't set up for this install.", "setup")
    tokens, address = providers.exchange(kind, cfg, code, mail_service.redirect_uri(request, provider),
                                         pending["verifier"], pending.get("mail", True), pending.get("calendar", True))
    conn = service.connect_oauth(request.user, kind, tokens, address, pending.get("mail", True),
                                 pending.get("calendar", True))
    current.note(f"Connected {conn.get_kind_display()} logging for {conn.email}")
    return conn
