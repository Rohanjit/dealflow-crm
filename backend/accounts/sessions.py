"""Session time-outs (decision 63): signed out after the firm's idle time (Settings > Security,
15 minutes to 8 hours, default 30) and 12 hours after signing in, whatever happens.

Requests the app makes on its own (the notification bell and other polling) carry the header
X-Dealflow-Background: 1 and don't count as activity, so leaving a tab open doesn't keep a
session alive."""

import re
import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

STARTED, SEEN = "df_started", "df_seen"


def idle_seconds() -> int:
    from core.models import FirmSettings

    return max(15, min(480, FirmSettings.load().idle_minutes or 30)) * 60


def start(request, method: str = ""):
    now = int(time.time())
    request.session[STARTED] = now
    request.session[SEEN] = now
    if method and getattr(request, "user", None) is not None and request.user.is_authenticated:
        record(request, method)


def device_name(ua: str) -> str:
    """"Chrome on macOS" from a browser's user-agent text."""
    ua = ua or ""
    browser = next((name for pat, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"),
        (r"Safari/", "Safari")) if re.search(pat, ua)), "A browser")
    system = next((name for pat, name in (
        (r"iPhone|iPad|iPod", "iOS"), (r"Android", "Android"), (r"Mac OS X|Macintosh", "macOS"),
        (r"Windows", "Windows"), (r"CrOS", "ChromeOS"), (r"Linux", "Linux")) if re.search(pat, ua)), "")
    return f"{browser} on {system}" if system else browser


def record(request, method: str):
    """Note this sign-in for "Signed-in devices", and flag it if the browser is new for this person."""
    import hashlib

    from django.utils import timezone

    from .lockout import client_ip
    from .models import UserSession

    if not request.session.session_key:
        request.session.save()
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    device = device_name(ua)
    dh = hashlib.sha256(device.encode()).hexdigest()
    user = request.user
    seen_before = UserSession.objects.filter(user=user)
    new = seen_before.exists() and not seen_before.filter(
        device_hash=dh, created_at__gte=timezone.now() - timezone.timedelta(days=90)).exists()
    UserSession.objects.create(user=user, session_key=request.session.session_key, ip=client_ip(request)[:70],
                               user_agent=ua, device=device, device_hash=dh, method=method[:20], new_device=new)
    UserSession.objects.filter(ended_at__lt=timezone.now() - timezone.timedelta(days=90)).delete()


def end(session_key: str):
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    from .models import UserSession

    Session.objects.filter(session_key=session_key).delete()
    UserSession.objects.filter(session_key=session_key, ended_at__isnull=True).update(ended_at=timezone.now())


def remaining(request) -> dict:
    now = int(time.time())
    idle = idle_seconds()
    seen = request.session.get(SEEN, now)
    started = request.session.get(STARTED, now)
    return {"idle_seconds": idle, "idle_left": max(0, seen + idle - now),
            "absolute_left": max(0, started + settings.SESSION_ABSOLUTE_SECONDS - now)}


class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and hasattr(request, "session"):
            now = int(time.time())
            started = request.session.get(STARTED)
            seen = request.session.get(SEEN)
            if started is None or seen is None:
                # Sessions from before time-outs existed (or made in tests): start the clock now.
                start(request)
            else:
                expired = None
                if now - started > settings.SESSION_ABSOLUTE_SECONDS:
                    expired = "You've been signed in for 12 hours, so you were signed out. Sign in again."
                elif now - seen > idle_seconds():
                    mins = idle_seconds() // 60
                    expired = f"You were signed out after {mins} minutes without activity. Sign in again."
                if expired:
                    logout(request)
                    if request.path.startswith("/api/"):
                        return JsonResponse({"detail": expired, "code": "session_expired"}, status=401)
                elif request.headers.get("X-Dealflow-Background") != "1" and now - seen >= 15:
                    request.session[SEEN] = now
                    if now - seen >= 60:
                        from django.utils import timezone

                        from .models import UserSession

                        UserSession.objects.filter(session_key=request.session.session_key).update(
                            last_seen=timezone.now())
        return self.get_response(request)


def end_all_for(user, keep: str | None = None) -> int:
    """Sign a person out everywhere (except `keep`): every stored session that belongs to them."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    n = 0
    for sess in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if sess.session_key != keep and str(sess.get_decoded().get("_auth_user_id")) == str(user.pk):
            end(sess.session_key)
            n += 1
    return n
