"""Password rules beyond Django's own (decision 63): an upper length limit, and no reuse of any of
the last 12 passwords. Only hashes of old passwords are kept."""

from django.contrib.auth.hashers import Argon2PasswordHasher, check_password
from django.core.exceptions import ValidationError

HISTORY = 12


class MaximumLengthValidator:
    """Long passphrases are welcome; megabyte-long ones are a way to tie up the server."""

    limit = 256

    def validate(self, password, user=None):
        if len(password or "") > self.limit:
            raise ValidationError(f"Keep your password under {self.limit} characters.", code="password_too_long")

    def get_help_text(self):
        return f"At most {self.limit} characters."


class PasswordHistoryValidator:
    def validate(self, password, user=None):
        if user is None or not getattr(user, "pk", None):
            return
        from .models import PasswordHistory

        old = list(PasswordHistory.objects.filter(user=user).order_by("-created_at").values_list("hash", flat=True)[:HISTORY])
        if user.password and user.has_usable_password():
            old.append(user.password)
        if any(check_password(password, h) for h in old):
            raise ValidationError(f"Pick a password you haven't used before (Dealflow remembers your last {HISTORY}).",
                                  code="password_reused")

    def password_changed(self, password, user=None):
        """Called by Django after a new password is saved."""
        if user is None or not user.pk or not user.has_usable_password():
            return
        from .models import PasswordHistory

        PasswordHistory.objects.create(user=user, hash=user.password)
        keep = list(PasswordHistory.objects.filter(user=user).order_by("-created_at").values_list("pk", flat=True)[:HISTORY])
        PasswordHistory.objects.filter(user=user).exclude(pk__in=keep).delete()

    def get_help_text(self):
        return f"Not one of your last {HISTORY} passwords."


class TestArgon2Hasher(Argon2PasswordHasher):
    """Tests only (dealflow/settings.py): Argon2id with light settings so the suite runs quickly."""

    algorithm = "argon2"
    time_cost = 1
    memory_cost = 256
    parallelism = 1
