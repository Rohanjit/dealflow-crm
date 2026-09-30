"""Sign-in lock-out.

Five wrong passwords for one email address within 30 minutes pause sign-in for that address for
30 minutes, whichever devices the attempts come from. Someone who manages users can lift it early
(resetting the person's password lifts it too).

- Counted per address typed, not per account, and the lock message is identical for addresses
  with no account, so the lock-out can't be used to find out who has one.
- Not counted per IP address: a firm's staff often share one office address, and one person's
  typos shouldn't lock everyone out. Guessing across many accounts from one address is slowed by
  the per-IP request limit on the sign-in endpoint instead.
- A wrong current password on "Change password" counts too, so a stolen session can't be used to
  guess the password.
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from .models import SignInLock

MAX_FAILURES = 5
WINDOW = dt.timedelta(minutes=30)  # failures further apart than this start the count again
LOCK = dt.timedelta(minutes=30)
KEEP = dt.timedelta(days=1)  # finished rows are pruned after this


def normalise(email) -> str:
    return str(email or "").strip().lower()[:254]


class SignInLocked(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "signin_locked"

    def __init__(self, until: dt.datetime):
        super().__init__(
            {
                "detail": (
                    "Too many wrong passwords. Sign-in for this email is paused until "
                    f"{timezone.localtime(until):%H:%M}. An admin can unlock it sooner."
                ),
                "code": "signin_locked",
            }
        )


def locked_until(email) -> dt.datetime | None:
    row = SignInLock.objects.filter(email=normalise(email), locked_until__gt=timezone.now()).first()
    return row.locked_until if row else None


def check(email):
    """Raise SignInLocked while the address is locked."""
    if until := locked_until(email):
        raise SignInLocked(until)


def record_failure(email) -> dt.datetime | None:
    """Count a wrong password. Returns when the lock ends if this failure started one."""
    key = normalise(email)
    if not key:
        return None
    now = timezone.now()
    for _ in range(2):  # a concurrent first failure can win the insert; retry once
        try:
            with transaction.atomic():
                row, _ = SignInLock.objects.select_for_update().get_or_create(email=key)
                if row.locked_until and row.locked_until > now:
                    return row.locked_until
                if not row.last_failure_at or now - row.last_failure_at > WINDOW:
                    row.failures = 0
                row.failures += 1
                row.last_failure_at = now
                started = None
                if row.failures >= MAX_FAILURES:
                    row.locked_until = started = now + LOCK
                    row.failures = 0
                row.save()
                return started
        except IntegrityError:
            continue
    return None


def clear(email):
    SignInLock.objects.filter(email=normalise(email)).delete()


def prune() -> int:
    """Drop rows for addresses that are neither locked nor recently failing (run daily)."""
    cutoff = timezone.now() - KEEP
    deleted, _ = (
        SignInLock.objects.filter(last_failure_at__lt=cutoff)
        .exclude(locked_until__gt=timezone.now())
        .delete()
    )
    return deleted
