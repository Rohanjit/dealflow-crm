"""Two-step login, passkeys, recovery codes and signed-in devices (decision 63, S2)."""

from __future__ import annotations

import time

from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core import current
from core.guest import NotInSample

from . import lockout, mfa, sessions
from .models import Passkey, TotpDevice, User, UserSession
from .permissions import IsActiveUser, flag_required

BACKEND = "django.contrib.auth.backends.ModelBackend"


def _str(v, limit=200) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def _recheck(request):
    """Changing two-step login once it's set up needs the password again, so a borrowed open
    laptop can't swap in someone else's phone or passkey."""
    if mfa.has_mfa(request.user):
        pw = request.data.get("password")
        if not isinstance(pw, str) or not request.user.check_password(pw):
            raise ValidationError({"password": "Enter your password to change two-step login."})


def finish_sign_in(request, user, method: str) -> Response:
    from .views import me_payload

    request.session.pop(mfa.PENDING, None)
    lockout.succeeded(user.email)
    login(request, user, backend=BACKEND)  # a new session id
    sessions.start(request, method)
    current.note(f"Signed in ({method})")
    return Response(me_payload(user))


def _pending(request):
    p = request.session.get(mfa.PENDING)
    if not p or int(time.time()) - p.get("at", 0) > mfa.PENDING_SECONDS:
        request.session.pop(mfa.PENDING, None)
        return None
    return User.objects.filter(pk=p.get("uid"), is_active=True).first()


# ---------------------------------------------------------------- signing in


class MfaVerifyView(APIView):
    """Second step with an authenticator app code or a recovery code."""

    permission_classes = [AllowAny]

    def post(self, request):
        user = _pending(request)
        if user is None:
            raise ValidationError({"detail": "That took too long. Enter your email and password again.",
                                   "code": "mfa_expired"})
        ip = lockout.client_ip(request)
        if lockout.locked(user.email, ip):
            raise PermissionDenied({"detail": lockout.MESSAGE, "code": "signin_locked"})
        method = request.data.get("method")
        code = _str(request.data.get("code"), 40)
        ok = (method == "app" and mfa.totp_verify(user, code)) or \
             (method == "recovery" and mfa.use_recovery_code(user, code))
        if not ok:
            lockout.failed(user.email, ip)
            current.note("Two-step code wrong")
            raise ValidationError({"code": "That code didn't work. Check it and try again."})
        if method == "recovery":
            current.note("Signed in with a recovery code")
        return finish_sign_in(request, user, "recovery code" if method == "recovery" else "app")


class PasskeyOptionsView(APIView):
    """Ask the browser for a passkey: the person's own after their password, or any of this
    site's passkeys for "Sign in with a passkey"."""

    permission_classes = [AllowAny]

    def post(self, request):
        user = _pending(request)
        if user is not None and not Passkey.objects.filter(user=user).exists():
            raise ValidationError({"detail": "You haven't added a passkey yet. Use your authenticator app."})
        return Response(mfa.authentication_options(request, user))


class PasskeySignInView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        pending = _pending(request)
        ip = lockout.client_ip(request)
        if pending is not None and lockout.locked(pending.email, ip):
            raise PermissionDenied({"detail": lockout.MESSAGE, "code": "signin_locked"})
        user = mfa.authenticate_passkey(request, request.data.get("credential"), pending)
        if user is None or not user.is_active:
            if pending is not None:
                lockout.failed(pending.email, ip)
            current.note("Passkey not accepted")
            raise ValidationError({"detail": "That passkey wasn't accepted. Try again, or use another way."})
        if lockout.locked(user.email, ip):
            raise PermissionDenied({"detail": lockout.MESSAGE, "code": "signin_locked"})
        return finish_sign_in(request, user, "passkey")


# ---------------------------------------------------------------- setting it up and changing it


class MfaStatusView(APIView):
    mfa_enrolment = True

    def get(self, request):
        return Response(mfa.summary(request.user))


def _after_first(request, was_enrolled: bool) -> dict:
    """The first method set up also gets recovery codes, shown once."""
    if was_enrolled:
        return {}
    return {"recovery_codes": mfa.new_recovery_codes(request.user)}


class TotpStartView(APIView):
    mfa_enrolment = True

    def post(self, request):
        _recheck(request)
        return Response(mfa.totp_start(request, request.user))


class TotpConfirmView(APIView):
    mfa_enrolment = True

    def post(self, request):
        was = mfa.has_mfa(request.user)
        if not mfa.totp_confirm(request, request.user, _str(request.data.get("code"), 20)):
            raise ValidationError({"code": "That code didn't match. Check the app's current code and try again."})
        current.note("Set up an authenticator app for two-step login")
        return Response({**mfa.summary(request.user), **_after_first(request, was)})


class TotpRemoveView(APIView):
    mfa_enrolment = True

    def post(self, request):
        _recheck(request)
        if not Passkey.objects.filter(user=request.user).exists():
            raise ValidationError({"detail": "Add a passkey first: two-step login can't be left with nothing."})
        TotpDevice.objects.filter(user=request.user).delete()
        current.note("Removed their authenticator app")
        return Response(mfa.summary(request.user))


class PasskeyRegisterOptionsView(APIView):
    mfa_enrolment = True

    def post(self, request):
        _recheck(request)
        if Passkey.objects.filter(user=request.user).count() >= 10:
            raise ValidationError({"detail": "You have 10 passkeys. Remove one first."})
        return Response(mfa.registration_options(request, request.user))


class PasskeyListView(APIView):
    mfa_enrolment = True

    def post(self, request):
        was = mfa.has_mfa(request.user)
        name = _str(request.data.get("name"), 80) or sessions.device_name(request.META.get("HTTP_USER_AGENT", ""))
        try:
            pk = mfa.register(request, request.user, request.data.get("credential"), name)
        except Exception as e:  # the library raises several kinds; none of them may get through
            raise ValidationError({"detail": f"That passkey couldn't be added. {e if isinstance(e, ValueError) else ''}".strip()})
        current.note(f"Added a passkey ({pk.name})")
        return Response({**mfa.summary(request.user), **_after_first(request, was)}, status=status.HTTP_201_CREATED)


class PasskeyDetailView(APIView):
    mfa_enrolment = True

    def _get(self, request, pk) -> Passkey:
        p = Passkey.objects.filter(pk=pk, user=request.user).first()
        if not p:
            raise NotFound("That passkey doesn't exist.")
        return p

    def patch(self, request, pk):
        p = self._get(request, pk)
        name = _str(request.data.get("name"), 80)
        if not name:
            raise ValidationError({"name": "Give it a name."})
        p.name = name
        p.save(update_fields=["name"])
        return Response(mfa.summary(request.user))

    def delete(self, request, pk):
        p = self._get(request, pk)
        _recheck(request)
        others = Passkey.objects.filter(user=request.user).exclude(pk=p.pk).exists() or \
            TotpDevice.objects.filter(user=request.user, confirmed=True).exists()
        if not others:
            raise ValidationError({"detail": "This is your only way to sign in. Add another passkey or an app first."})
        current.note(f"Removed a passkey ({p.name})")
        p.delete()
        return Response(mfa.summary(request.user))


class RecoveryCodesView(APIView):
    mfa_enrolment = True

    def post(self, request):
        if not mfa.has_mfa(request.user):
            raise ValidationError({"detail": "Set up two-step login first."})
        _recheck(request)
        current.note("Made new recovery codes (the old ones stopped working)")
        return Response({"recovery_codes": mfa.new_recovery_codes(request.user)})


# ---------------------------------------------------------------- signed-in devices


def _session_payload(s: UserSession, current_key: str) -> dict:
    return {"id": s.pk, "device": s.device or "A browser", "ip": s.ip, "method": s.method,
            "created_at": s.created_at.isoformat(), "last_seen": s.last_seen.isoformat(),
            "current": s.session_key == current_key, "new_device": s.new_device}


def _live(user):
    from django.contrib.sessions.models import Session

    live = set(Session.objects.filter(expire_date__gt=timezone.now()).values_list("session_key", flat=True))
    return [s for s in UserSession.objects.filter(user=user, ended_at__isnull=True) if s.session_key in live]


class SessionsView(APIView):
    mfa_enrolment = True

    def get(self, request):
        key = request.session.session_key or ""
        return Response([_session_payload(s, key) for s in _live(request.user)])


class SessionEndView(APIView):
    def delete(self, request, pk):
        s = UserSession.objects.filter(pk=pk, user=request.user, ended_at__isnull=True).first()
        if not s:
            raise NotFound("That session has already ended.")
        if s.session_key == request.session.session_key:
            raise ValidationError({"detail": "That's this browser. Use Sign out instead."})
        sessions.end(s.session_key)
        current.note(f"Signed out {s.device} remotely")
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionEndOthersView(APIView):
    def post(self, request):
        n = 0
        for s in _live(request.user):
            if s.session_key != request.session.session_key:
                sessions.end(s.session_key)
                n += 1
        current.note(f"Signed out everywhere else ({n})")
        return Response({"ended": n})


class SecurityAlertsView(APIView):
    """Sign-ins from a browser this person hasn't used in 90 days, for the notification bell."""

    def get(self, request):
        since = timezone.now() - timezone.timedelta(days=14)
        key = request.session.session_key or ""
        rows = UserSession.objects.filter(user=request.user, new_device=True, created_at__gte=since)[:10]
        return Response([_session_payload(s, key) for s in rows])


# ---------------------------------------------------------------- admins


class UserResetMfaView(APIView):
    permission_classes = [flag_required("manage_users"), NotInSample]

    @transaction.atomic
    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target.is_admin and not request.user.is_admin:
            raise PermissionDenied("Only an admin can reset an admin's two-step login.")
        pw = request.data.get("password")
        if not isinstance(pw, str) or not request.user.check_password(pw):
            raise ValidationError({"password": "Enter your password to reset someone's two-step login."})
        mfa.reset(target)
        sessions.end_all_for(target)
        current.note(f"Reset two-step login for {target.full_name or target.email}")
        return Response({"ok": True})
