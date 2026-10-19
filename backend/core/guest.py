"""Guest mode: look around Dealflow with sample data, without an account.

- On a fresh install (nobody has set it up yet), "Look around first" loads the sample firm
  (core/sample.py) and signs the visitor in as its Guest user. Everything works, on sample data
  only. "Set up for real" puts the install back exactly as it was before the sample was loaded and
  shows the normal set-up. Once an install is set up for real, guest mode is gone for good.
- A public demo site (DEALFLOW_DEMO=1) always holds the sample: the sign-in page offers "Continue as
  guest", set-up is refused, and the scheduler reloads the sample every day.
- While the sample is loaded, actions about people and safety (users, invites, roles, backups, PIN
  resets) are refused: they would mean nothing on sample data, and on a demo site guests share it.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import backups, sample
from .models import FirmSettings

NOT_IN_SAMPLE = "Not available while you're looking around with sample data. Set up Dealflow for real to manage this."


def pristine_path():
    return settings.DATA_DIR / "pristine.json"


def sample_loaded() -> bool:
    return FirmSettings.load().sample_loaded_at is not None


def is_guest(user) -> bool:
    return bool(user and user.is_authenticated and user.email == sample.GUEST_EMAIL and sample_loaded())


def guest_available() -> bool:
    """Demo sites always; other installs only until someone sets them up for real."""
    from accounts.models import User

    if settings.DEMO:
        return True
    fs = FirmSettings.load()
    if fs.setup_completed_at:
        return False
    return fs.sample_loaded_at is not None or not User.objects.exists()


def ensure_sample():
    """Load the sample firm unless it's already there (keeping a copy of the empty install first)."""
    from accounts.models import User

    with transaction.atomic():
        fs = FirmSettings.objects.select_for_update().get_or_create(pk=1)[0]
        if fs.sample_loaded_at:
            return
        if fs.setup_completed_at or User.objects.exists():
            raise PermissionDenied("Dealflow is already set up. Sign in instead.")
        backups.dump_data(pristine_path())
        sample.load()


def reset_to_pristine():
    """Throw away the sample (and anything guests added) and restore the empty install."""
    path = pristine_path()
    if not path.exists():
        raise PermissionDenied("The empty install can't be found, so the sample can't be removed.")
    backups.replace_all_data(path, None)
    imports = settings.IMPORT_DIR
    if imports.exists():
        for f in imports.glob("*.json"):
            f.unlink(missing_ok=True)


def reload_demo():
    """Demo sites: start the day with a fresh copy of the sample."""
    if sample_loaded():
        reset_to_pristine()
    ensure_sample()


def demo_reload_due(now=None) -> bool:
    if not settings.DEMO:
        return False
    loaded = FirmSettings.load().sample_loaded_at
    return loaded is None or timezone.localtime(loaded).date() < timezone.localdate(now)


class NotInSample(BasePermission):
    """Refuse people-and-safety actions while the sample is loaded."""

    message = NOT_IN_SAMPLE

    def has_permission(self, request, view):
        return request.method in ("GET", "HEAD", "OPTIONS") or not sample_loaded()


class NotOnDemo(BasePermission):
    """Refuse firm-wide changes on a public demo site, where every guest shares the sample."""

    message = "Not available on the demo site."

    def has_permission(self, request, view):
        return not settings.DEMO or request.method in ("GET", "HEAD", "OPTIONS")


class GuestStartView(APIView):
    """Sign in as the sample firm's Guest, loading the sample first if needed. Works before set-up
    (and always on a demo site); refused once the install is set up for real."""

    permission_classes = [AllowAny]

    def get_throttles(self):
        from accounts.views import LoginThrottle

        return [LoginThrottle()]

    def post(self, request):
        from accounts.models import User
        from accounts.views import me_payload

        if not guest_available():
            raise PermissionDenied("Guest mode isn't available here. Sign in instead.")
        ensure_sample()
        guest = User.objects.select_related("role").get(email=sample.GUEST_EMAIL)
        login(request, guest, backend="django.contrib.auth.backends.ModelBackend")
        return Response(me_payload(guest))


class GuestLeaveView(APIView):
    """"Set up for real": remove the sample and return to the empty install's set-up screen."""

    def post(self, request):
        if settings.DEMO:
            raise PermissionDenied("The demo site keeps its sample data.")
        if not is_guest(request.user):
            raise PermissionDenied("Only the guest can leave the sample.")
        logout(request)
        reset_to_pristine()
        return Response({"ok": True})
