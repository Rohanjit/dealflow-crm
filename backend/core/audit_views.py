"""Settings > Audit log (admins only): the passphrase, how long to keep the log, and fetching it.
Every change and fetch needs the admin's account password; reading needs the passphrase too."""

from __future__ import annotations

import datetime as dt

from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser

from . import audit, current
from .guest import NotInSample

MAX_DAYS = 366


class AdminOnly(IsActiveUser):
    message = "Only admins can open the audit log."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin


def _password(request):
    pw = request.data.get("password")
    if not isinstance(pw, str) or not request.user.check_password(pw):
        raise ValidationError({"password": "That isn't your password."})


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def _passphrase_problem(request, err: audit.AuditError):
    """A wrong passphrase counts toward a 15-minute lock for this person."""
    audit.record_failure(request.user.pk)
    until = audit.locked_until(request.user.pk)
    msg = str(err) + (f" Locked for {audit.LOCK_MINUTES} minutes after {audit.LOCK_AFTER} tries." if until else "")
    raise ValidationError({"passphrase": msg})


def _not_locked(request):
    if until := audit.locked_until(request.user.pk):
        raise PermissionDenied(f"Too many wrong passphrases. Try again after {until:%H:%M}.")


class AuditStatusView(APIView):
    permission_classes = [AdminOnly]

    def get(self, request):
        return Response({**audit.status(), "min_passphrase": audit.MIN_PASSPHRASE, "max_days": audit_max_days()})


def audit_max_days() -> int:
    return MAX_DAYS


class AuditPassphraseView(APIView):
    """Set the first passphrase, or change it (with the current one)."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        _not_locked(request)
        _password(request)
        new = _text(request.data.get("passphrase"))
        if msg := audit.check_passphrase_strength(new):
            raise ValidationError({"passphrase": msg})
        changing = audit.has_passphrase()
        try:
            audit.set_passphrase(new, _text(request.data.get("current_passphrase")) if changing else None)
        except audit.AuditError as e:
            if changing:
                audit.record_failure(request.user.pk)
                raise ValidationError({"current_passphrase": str(e)})
            raise ValidationError({"passphrase": str(e)})
        audit.clear_failures(request.user.pk)
        current.note("Audit passphrase changed" if changing else "Audit passphrase set")
        return Response(audit.status())


class AuditNewKeyView(APIView):
    """The passphrase is lost: start a new key. Entries sealed with the old one can't be read again."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        _password(request)
        if request.data.get("confirm") is not True:
            raise ValidationError({"confirm": "Confirm that older entries will stay locked for good."})
        try:
            audit.new_key(_text(request.data.get("passphrase")))
        except audit.AuditError as e:
            raise ValidationError({"passphrase": str(e)})
        current.note("Started a new audit log key; entries sealed with the old key can no longer be read")
        return Response(audit.status())


class AuditSettingsView(APIView):
    """How many years to keep (null = forever)."""

    permission_classes = [AdminOnly, NotInSample]

    def patch(self, request):
        _password(request)
        years = request.data.get("keep_years")
        if years is not None and (isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 100):
            raise ValidationError({"keep_years": "Pick between 1 and 100 years, or keep forever."})
        before = audit.keep_years()
        audit.set_keep_years(years)
        current.note(f"Keep the audit log: {before or 'forever'} → {years or 'forever'} years")
        return Response(audit.status())


class AuditFetchView(APIView):
    """Decrypt a date range and download it as an Excel file (with the result of the chain check)."""

    permission_classes = [AdminOnly, NotInSample]

    def post(self, request):
        _not_locked(request)
        _password(request)
        try:
            start = dt.date.fromisoformat(_text(request.data.get("from")))
            end = dt.date.fromisoformat(_text(request.data.get("to")))
        except ValueError:
            raise ValidationError({"from": "Pick the first and last day to fetch."})
        if end < start:
            raise ValidationError({"to": "The last day is before the first."})
        if (end - start).days >= MAX_DAYS:
            raise ValidationError({"to": f"Fetch up to {MAX_DAYS} days at a time."})
        if not audit.has_passphrase():
            raise ValidationError({"passphrase": "Set the audit passphrase first."})
        try:
            entries, problems = audit.read(_text(request.data.get("passphrase")), start, end)
        except audit.AuditError as e:
            _passphrase_problem(request, e)
        audit.clear_failures(request.user.pk)
        current.note(f"Fetched {start} to {end}: {len(entries)} entries, {len(problems)} chain problems")
        data = audit.export_xlsx(entries, problems, start, end, request.user.full_name)
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="dealflow-audit-{start}-to-{end}.xlsx"'
        resp["Cache-Control"] = "no-store"
        resp["X-Audit-Entries"] = str(len(entries))
        resp["X-Audit-Problems"] = str(len(problems))
        return resp
