"""Session time-outs (decision 63): signed out after the firm's idle time (Settings > Security,
15 minutes to 8 hours, default 30) and 12 hours after signing in, whatever happens.

Requests the app makes on its own (the notification bell and other polling) carry the header
X-Dealflow-Background: 1 and don't count as activity, so leaving a tab open doesn't keep a
session alive."""

import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

STARTED, SEEN = "df_started", "df_seen"


def idle_seconds() -> int:
    from core.models import FirmSettings

    return max(15, min(480, FirmSettings.load().idle_minutes or 30)) * 60


def start(request):
    now = int(time.time())
    request.session[STARTED] = now
    request.session[SEEN] = now


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
        return self.get_response(request)
