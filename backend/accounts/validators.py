"""Password rules beyond Django's built-in validators (see AUTH_PASSWORD_VALIDATORS in settings)."""

from __future__ import annotations

from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError


class PasswordHistoryValidator:
    """A new password can't be the current one or any of the previous `count` passwords.

    Django calls `password_changed` after every save that sets a new password (not after the
    silent re-hash when a password is upgraded to a stronger hasher), so the history fills itself."""

    def __init__(self, count: int = 12):
        self.count = count

    def _recent(self, user) -> list[str]:
        from .models import PasswordHistory

        return list(PasswordHistory.objects.filter(user=user).values_list("password", flat=True)[: self.count])

    def validate(self, password, user=None):
        if user is None or not user.pk:
            return
        # check_password without a setter: validating must never re-save the user.
        for encoded in [user.password, *self._recent(user)]:
            if encoded and check_password(password, encoded):
                raise ValidationError(
                    f"This password was used before. Pick one that isn't among the last {self.count}.",
                    code="password_reused",
                )

    def password_changed(self, password, user=None):
        from .models import PasswordHistory

        if user is None or not user.pk:
            return
        PasswordHistory.objects.create(user=user, password=user.password)
        keep = list(PasswordHistory.objects.filter(user=user).values_list("pk", flat=True)[: self.count])
        PasswordHistory.objects.filter(user=user).exclude(pk__in=keep).delete()

    def get_help_text(self):
        return f"Your password can't be one of your last {self.count} passwords."
