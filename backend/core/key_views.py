"""Keys in Settings (decision 63, S4): the backup key to keep somewhere safe, and the Vault's key
(who holds it, the recovery key, a new key). Admins only, with their password for anything that
changes or reveals a key."""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser

from . import current
from .guest import NotInSample


class AdminOnly(IsActiveUser):
    message = "Only admins can manage keys."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin


def _password(request):
    pw = request.data.get("password")
    if not isinstance(pw, str) or not request.user.check_password(pw):
        raise ValidationError({"password": "Enter your password."})


class BackupKeyView(APIView):
    """POST {password}: the key file that unlocks this install's backups, to keep apart from them."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from dealflow import keys

        _password(request)
        text = keys.export_backup_key()
        current.note("Downloaded the backup key")
        resp = HttpResponse(text, content_type="text/plain; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="dealflow-backup-key.txt"'
        resp["Cache-Control"] = "no-store"
        return resp


class VaultKeysView(APIView):
    permission_classes = [AdminOnly]

    def get(self, request):
        from dealflow import keys
        from vault import keyring

        return Response({**keyring.summary(), "holding_key": keyring.current(request) is not None,
                         "storage": keys.status()})


def _vault_action(request):
    from vault import pin as vp

    vp.require_open(request)


class VaultGrantView(APIView):
    """Give the Vault key now to everyone waiting (needs the admin's open Vault)."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from vault import keyring

        _vault_action(request)
        key_id, key = keyring.require_key(request)
        n = keyring.grant_pending(key_id, key)
        current.note(f"Gave {n} people access to Vault files")
        return Response({"granted": n, **keyring.summary()})


class VaultRecoveryKeyView(APIView):
    """Make (or remake) the recovery key; shown once."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from vault import keyring

        _password(request)
        _vault_action(request)
        text = keyring.make_recovery(request)
        current.note("Made a new Vault recovery key")
        return Response({"recovery_key": text, **keyring.summary()})


class VaultRecoverView(APIView):
    """Use the recovery key when nobody here holds the Vault key (e.g. after moving to new hardware)."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from vault import keyring
        from vault import pin as vp

        user = request.user
        if not vp.has_pin(user):
            raise ValidationError({"detail": "Set your Vault PIN first."})
        if not vp.pin_matches(user, request.data.get("pin")):
            vp.record_failure(user)
            raise ValidationError({"pin": "Wrong PIN."})
        try:
            keyring.recover(request, user, str(request.data.get("recovery_key") or ""), str(request.data.get("pin")))
        except ValueError as e:
            current.note("Vault recovery key refused")
            raise ValidationError({"recovery_key": str(e)})
        vp.open_session(request)
        keyring.on_use(request)
        current.note("Opened the Vault with the recovery key")
        resp = Response(keyring.summary())
        keyring.set_cookie(request, resp)
        return resp


class VaultRotateView(APIView):
    """A new Vault key: every file re-encrypted. The recovery key has to be made again."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from vault import keyring

        _password(request)
        _vault_action(request)
        result = keyring.rotate(request)
        current.note(f"Changed the Vault key ({result['files']} files re-encrypted)")
        resp = Response({**result, **keyring.summary()})
        keyring.set_cookie(request, resp)
        return resp


class ScannerStatusView(APIView):
    """Settings > Security (admins): whether the virus scanner answers, its signatures, recent refusals."""

    def get(self, request):
        if not request.user.is_admin:
            raise PermissionDenied("Only admins can see the virus scanner.")
        from core import antivirus

        return Response(antivirus.status_info())


class ScanRefusalAlertsView(APIView):
    """Files the scanner refused in the last 14 days, for admins' notification bell (`virus:<id>`)."""

    def get(self, request):
        if not request.user.is_admin:
            return Response([])
        from django.utils import timezone

        from core.models import ScanRefusal

        since = timezone.now() - timezone.timedelta(days=14)
        rows = ScanRefusal.objects.filter(at__gte=since).select_related("user")[:20]
        return Response([{"id": r.id, "at": r.at.isoformat(), "place": r.get_place_display(), "file_name": r.file_name,
                          "signature": r.signature, "user": (r.user.full_name or r.user.email) if r.user else None}
                         for r in rows])


class SecurityOverviewView(APIView):
    """Settings > Security: the checklist and the numbers behind it (core/security_overview.py)."""

    permission_classes = [AdminOnly]

    def get(self, request):
        from . import security_overview

        return Response(security_overview.overview(request))


class AuditChainCheckView(APIView):
    """Check the audit log's chain now (it's also checked nightly). Needs no passphrase."""

    permission_classes = [AdminOnly]

    def post(self, request):
        from . import audit

        return Response(audit.verify_chain())


class SignOutEveryoneView(APIView):
    """Incident response: end every session except the admin's own (docs/security/incident-response.md)."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        from accounts import sessions
        from accounts.models import User

        _password(request)
        mine = request.session.session_key
        n = sum(sessions.end_all_for(u, keep=mine) for u in User.objects.all())
        current.note(f"Signed everyone else out ({n} sessions)")
        return Response({"ended": n})


class RestoreTestView(APIView):
    """Test that the newest backup restores, now (core/restore_test.py; also weekly)."""

    permission_classes = [AdminOnly]

    def post(self, request):
        from . import restore_test

        r = restore_test.run()
        current.note("Backup restore test " + ("passed" if r["ok"] else "failed"))
        return Response(r)


class AdminSecurityAlertsView(APIView):
    """Security events for admins' notification bell (core/security_overview.admin_alerts)."""

    def get(self, request):
        if not request.user.is_admin:
            return Response([])
        from . import security_overview

        return Response(security_overview.admin_alerts())
