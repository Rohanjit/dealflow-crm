import json

from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import flag_required
from core.guest import NotInSample, is_guest
from core.sample import GUEST_PIN
from core.records import apply_values, clean_values, serialize, with_related
from core.registry import enabled_map, get_module

from . import pin as vp
from .files import ACCEPTED, KINDS, FileRejected, check, clean_name, max_bytes
from .models import Document

INLINE_TYPES = {k.content_type for k in KINDS.values() if k.inline}


def _require(user, action: str):
    if not user.can("vault", action):
        verb = {"view": "open", "create": "add to", "edit": "edit in", "delete": "delete from"}[action]
        raise PermissionDenied(f"Your role can't {verb} the Vault.")


def status_payload(request) -> dict:
    user = request.user
    until = vp.open_until(request)
    locked = vp.locked_out(user)
    return {
        "can_view": user.can("vault", "view"),
        "has_pin": vp.has_pin(user),
        "open": bool(until) and vp.has_pin(user) and not locked,
        "open_until": until.isoformat() if until else None,
        "locked_out_until": locked.isoformat() if locked else None,
        "idle_minutes": vp.IDLE_MINUTES,
        "accepted": ACCEPTED,
        "max_file_mb": max_bytes() // (1024 * 1024),
        # Guests can't set a PIN (they have no password), so the sample comes with one.
        "sample_pin": GUEST_PIN if is_guest(user) else None,
    }


class VaultStatusView(APIView):
    """Whether this person has a PIN and whether the Vault is open in this session."""

    def get(self, request):
        return Response(status_payload(request))


class VaultPinView(APIView):
    """Set the first PIN (with the account password) or change it (with the current PIN)."""

    def post(self, request):
        user = request.user
        _require(user, "view")
        if is_guest(user):
            # Guests share the sample's PIN (on a demo site, with each other).
            raise PermissionDenied("The sample's Vault PIN can't be changed.")
        if until := vp.locked_out(user):
            raise PermissionDenied({"detail": vp.lockout_message(until), "code": "vault_locked_out"})
        new = str(request.data.get("pin") or "")
        if msg := vp.pin_problem(new):
            raise ValidationError({"pin": msg})
        if vp.has_pin(user):
            if not vp.pin_matches(user, request.data.get("current_pin")):
                left = vp.record_failure(user)
                raise ValidationError({"current_pin": "That isn't your current PIN." + _tries(left)})
        elif not user.check_password(str(request.data.get("password") or "")):
            left = vp.record_failure(user)
            raise ValidationError({"password": "That isn't your password." + _tries(left)})
        vp.set_pin(user, new)
        vp.open_session(request)
        return Response(status_payload(request))


def _tries(left: int) -> str:
    if left == 0:
        return f" The Vault is locked for {vp.LOCKOUT_MINUTES} minutes."
    return f" {left} {'try' if left == 1 else 'tries'} left." if left <= 3 else ""


class VaultUnlockView(APIView):
    def post(self, request):
        user = request.user
        _require(user, "view")
        if not vp.has_pin(user):
            raise PermissionDenied({"detail": "Set a Vault PIN first.", "code": "vault_pin_required"})
        if until := vp.locked_out(user):
            raise PermissionDenied({"detail": vp.lockout_message(until), "code": "vault_locked_out"})
        if not vp.pin_matches(user, request.data.get("pin")):
            if is_guest(user):
                # The PIN is shown on screen; a shared guest mustn't be able to lock everyone out.
                raise ValidationError({"pin": f"Wrong PIN. The sample's PIN is {GUEST_PIN}."})
            left = vp.record_failure(user)
            raise ValidationError({"pin": "Wrong PIN." + _tries(left)})
        vp.record_success(user)
        vp.open_session(request)
        return Response(status_payload(request))


class VaultLockView(APIView):
    def post(self, request):
        vp.close_session(request)
        return Response(status_payload(request))


class VaultUploadView(APIView):
    """Upload one or more files with shared details (category, tags, description). Every file is
    checked first; if any is refused, nothing is stored."""

    parser_classes = [MultiPartParser]

    def post(self, request):
        _require(request.user, "create")
        vp.require_open(request)
        mod = get_module("vault")
        files = request.FILES.getlist("files")
        if not files:
            raise ValidationError({"files": "Choose at least one file."})
        if len(files) > 50:
            raise ValidationError({"files": "Upload up to 50 files at a time."})
        try:
            meta = json.loads(request.data.get("data") or "{}")
            assert isinstance(meta, dict)
        except (ValueError, AssertionError):
            raise ValidationError({"detail": "The upload form was incomplete. Try again."})
        titles = meta.pop("titles", None) or []
        for k in ("file_name", "file_type", "file_size", "created_by", "created_at"):
            meta.pop(k, None)

        checked, problems = [], []
        for f in files:
            try:
                checked.append(check(f))
            except FileRejected as e:
                problems.append(str(e))
        if problems:
            raise ValidationError({"files": problems})

        enabled = enabled_map("vault")
        prepared = []
        for i, (f, (ext, kind, sha)) in enumerate(zip(files, checked)):
            name = clean_name(f.name)
            given = str(titles[i]).strip() if i < len(titles) and titles[i] else ""
            title = given or (name.rpartition(".")[0] if "." in name else name)
            values, errors = clean_values(mod, {**meta, "title": title[:255]}, enabled=enabled)
            if errors:
                raise ValidationError(errors)
            prepared.append((f, name, ext, kind, sha, values))

        created = []
        with transaction.atomic():
            for f, name, ext, kind, sha, values in prepared:
                doc = Document(file=f, file_name=name, file_type=kind.label, file_size=f.size,
                               content_type=kind.content_type, sha256=sha)
                doc._ext = ext
                created.append(apply_values(doc, mod, values, request.user))
        docs = {d.pk: d for d in with_related(Document.objects.filter(pk__in=[d.pk for d in created]), mod)}
        return Response([serialize(docs[d.pk], mod) for d in created], status=status.HTTP_201_CREATED)


class VaultDownloadView(APIView):
    """Download a document (or open a PDF or image in the browser with ?inline=1)."""

    def get(self, request, pk):
        _require(request.user, "view")
        vp.require_open(request)
        doc = Document.objects.filter(pk=pk, archived_at__isnull=True).first()
        if not doc:
            raise NotFound("This document doesn't exist or has been archived.")
        inline = request.query_params.get("inline") == "1" and doc.content_type in INLINE_TYPES
        try:
            fh = doc.file.open("rb")
        except (FileNotFoundError, ValueError):
            raise NotFound("The file is missing from the server. Restore it from a backup.")
        resp = FileResponse(fh, as_attachment=not inline, filename=doc.file_name, content_type=doc.content_type)
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Cache-Control"] = "private, no-store"
        if inline and doc.content_type.startswith("image/"):
            resp["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
        return resp


class UserVaultPinResetView(APIView):
    """Clear someone's forgotten PIN; they set a new one (with their password) next time."""

    permission_classes = [flag_required("manage_users"), NotInSample]

    def post(self, request, pk):
        from accounts.views import _guard_admin_changes

        u = get_object_or_404(User, pk=pk)
        _guard_admin_changes(request.user, u, None)
        vp.clear_pin(u)
        return Response({"id": u.pk, "has_vault_pin": False, "reset_at": timezone.now().isoformat()})
