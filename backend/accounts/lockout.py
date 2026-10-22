"""Sign-in lock-out (decision 63): 5 failed tries for one account lock it for 30 minutes, from
anywhere; 20 from one network address lock that address. The same message is shown whether or
not the account exists, and a correct password during a lock-out still doesn't get in."""

import datetime as dt
import hashlib

from django.db import transaction
from django.utils import timezone

from .models import SignInFailure

ACCOUNT_LIMIT = 5
ADDRESS_LIMIT = 20
WINDOW = dt.timedelta(minutes=30)
LOCK = dt.timedelta(minutes=30)
MESSAGE = "Too many sign-in attempts. Wait 30 minutes and try again, or ask an admin to reset your password."


def _keys(email: str, ip: str) -> list[tuple[str, int]]:
    acct = "a:" + hashlib.sha256(("dealflow-signin:" + email.strip().lower()).encode()).hexdigest()[:64]
    return [(acct, ACCOUNT_LIMIT), (f"ip:{ip[:70]}", ADDRESS_LIMIT)]


def client_ip(request) -> str:
    # Behind the proxy (DEALFLOW_BEHIND_PROXY), the address it saw is the last entry of
    # X-Forwarded-For (DEALFLOW_PROXY_HOPS entries from the end with several proxies); entries before
    # it come from the client and can't be trusted. Without a proxy the header is ignored.
    from django.conf import settings

    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        hops = [h.strip() for h in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]
        n = getattr(settings, "PROXY_HOPS", 1)
        if len(hops) >= n:
            return hops[-n][:70]
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def locked(email: str, ip: str) -> bool:
    now = timezone.now()
    keys = [k for k, _ in _keys(email, ip)]
    return SignInFailure.objects.filter(key__in=keys, locked_until__gt=now).exists()


@transaction.atomic
def failed(email: str, ip: str) -> bool:
    """Count a failure; True if this locked the account or address."""
    now = timezone.now()
    newly = False
    for key, limit in _keys(email, ip):
        row = SignInFailure.objects.select_for_update().filter(key=key).first()
        if row is None:
            row = SignInFailure(key=key, count=0, first_at=now)
        if row.first_at < now - WINDOW and not (row.locked_until and row.locked_until > now):
            row.count, row.first_at, row.locked_until = 0, now, None
        row.count += 1
        if row.count >= limit and not (row.locked_until and row.locked_until > now):
            row.locked_until = now + LOCK
            newly = True
        row.save()
    return newly


def succeeded(email: str):
    """A good sign-in clears the account's count (the address keeps its own)."""
    key = _keys(email, "")[0][0]
    SignInFailure.objects.filter(key=key).delete()


def clear_account(email: str):
    """An admin's password reset also lifts a lock-out."""
    succeeded(email)


def tidy():
    SignInFailure.objects.filter(first_at__lt=timezone.now() - WINDOW - LOCK).filter(
        locked_until__isnull=True).delete()
    SignInFailure.objects.filter(locked_until__lt=timezone.now() - WINDOW).delete()
