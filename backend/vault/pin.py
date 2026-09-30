"""The Vault PIN: a second secret, on top of the role's Vault permission, that opens the Vault for a
while in one browser session.

- 4 to 8 digits, hashed like a password. Changing it needs the current PIN; setting the first one
  needs the account password. A forgotten PIN is cleared by someone who manages users.
- Five wrong tries lock the Vault for that person for 15 minutes, on every device.
- Once opened, the Vault stays open in that session until 15 minutes pass without using it, or the
  person locks it."""

from __future__ import annotations

import datetime as dt
import re

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

IDLE_MINUTES = 15
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15
SESSION_KEY = "vault_until"


def pin_problem(pin: str) -> str | None:
    if not re.fullmatch(r"\d{4,8}", pin or ""):
        return "Use 4 to 8 digits."
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        return "That PIN is too easy to guess. Avoid repeated or consecutive digits."
    return None


def has_pin(user) -> bool:
    return bool(user.vault_pin)


def set_pin(user, pin: str):
    user.vault_pin = make_password(pin)
    user.vault_pin_failures = 0
    user.vault_locked_until = None
    user.save(update_fields=["vault_pin", "vault_pin_failures", "vault_locked_until"])


def clear_pin(user):
    user.vault_pin = ""
    user.vault_pin_failures = 0
    user.vault_locked_until = None
    user.save(update_fields=["vault_pin", "vault_pin_failures", "vault_locked_until"])


def locked_out(user) -> dt.datetime | None:
    if user.vault_locked_until and user.vault_locked_until > timezone.now():
        return user.vault_locked_until
    return None


def record_failure(user) -> int:
    """Count a wrong PIN (or password); returns the tries left before the lock-out."""
    user.vault_pin_failures += 1
    if user.vault_pin_failures >= MAX_FAILURES:
        user.vault_locked_until = timezone.now() + dt.timedelta(minutes=LOCKOUT_MINUTES)
        user.vault_pin_failures = 0
        left = 0
    else:
        left = MAX_FAILURES - user.vault_pin_failures
    user.save(update_fields=["vault_pin_failures", "vault_locked_until"])
    return left


def pin_matches(user, pin: str) -> bool:
    return bool(user.vault_pin) and check_password(str(pin or ""), user.vault_pin)


def record_success(user):
    if user.vault_pin_failures:
        user.vault_pin_failures = 0
        user.save(update_fields=["vault_pin_failures"])


def open_session(request):
    request.session[SESSION_KEY] = (timezone.now() + dt.timedelta(minutes=IDLE_MINUTES)).timestamp()


def close_session(request):
    request.session.pop(SESSION_KEY, None)


def open_until(request) -> dt.datetime | None:
    ts = request.session.get(SESSION_KEY)
    if not ts:
        return None
    until = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
    return until if until > timezone.now() else None


def lockout_message(until: dt.datetime) -> str:
    return f"Too many wrong PINs. The Vault opens again at {timezone.localtime(until):%H:%M}."


def require_open(request):
    """Gate for every Vault read and write: the person needs a PIN and an open Vault in this session.
    Each use keeps it open for another 15 minutes."""
    user = request.user
    if not has_pin(user):
        raise PermissionDenied({"detail": "Set a Vault PIN to use the Vault.", "code": "vault_pin_required"})
    if until := locked_out(user):
        raise PermissionDenied({"detail": lockout_message(until), "code": "vault_locked_out"})
    if not open_until(request):
        close_session(request)
        raise PermissionDenied({"detail": "The Vault is locked. Enter your PIN to open it.", "code": "vault_locked"})
    open_session(request)
