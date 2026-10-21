import secrets
from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone

from core.modules import ACTIONS, DERIVED_PERMISSIONS, MODULES


class Role(models.Model):
    """A named set of permissions. The built-in Admin role (is_admin) has everything
    and cannot be edited or deleted."""

    name = models.CharField(max_length=80, unique=True)
    is_admin = models.BooleanField(default=False)
    manage_users = models.BooleanField(default=False)
    manage_settings = models.BooleanField(default=False)
    restore_archive = models.BooleanField(default=False)
    # Win-rate analytics broken down by banker (Insights page); firm-wide numbers need no flag.
    see_team_performance = models.BooleanField(default=False)
    # Mail-merge campaigns from one's own mailbox (decision 62); one-to-one emails need no flag.
    send_campaigns = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_admin", "name"]

    def __str__(self):
        return self.name

    def module_perms(self) -> dict:
        """{module: {view, create, edit, delete}} for every module, plus derived record types
        (companies follow Outreach and Active Deals)."""
        if self.is_admin:
            out = {m: {a: True for a in ACTIONS} for m, _ in MODULES}
        else:
            rows = {p.module: p for p in self.module_permissions.all()}
            out = {}
            for m, _ in MODULES:
                p = rows.get(m)
                out[m] = {a: bool(p and getattr(p, f"can_{a}")) for a in ACTIONS}
        for derived, sources in DERIVED_PERMISSIONS.items():
            out[derived] = {a: any(out[src][a] for src in sources) for a in ACTIONS}
        return out

    def flags(self) -> dict:
        if self.is_admin:
            return {"manage_users": True, "manage_settings": True, "restore_archive": True, "see_team_performance": True,
                    "send_campaigns": True}
        return {
            "manage_users": self.manage_users,
            "manage_settings": self.manage_settings,
            "restore_archive": self.restore_archive,
            "see_team_performance": self.see_team_performance,
            "send_campaigns": self.send_campaigns,
        }


class RoleModulePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="module_permissions")
    module = models.CharField(max_length=30, choices=MODULES)
    can_view = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    class Meta:
        unique_together = [("role", "module")]


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)


DATE_FORMATS = [
    ("DD/MM/YYYY", "DD/MM/YYYY"),
    ("MM/DD/YYYY", "MM/DD/YYYY"),
    ("YYYY-MM-DD", "YYYY-MM-DD"),
]
NUMBER_GROUPINGS = [
    ("indian", "Indian (12,34,56,789)"),
    ("international", "International (123,456,789)"),
]


class User(AbstractBaseUser):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="users")
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    # Vault PIN (see vault/pin.py): hashed like a password; wrong tries lock the Vault for a while.
    vault_pin = models.CharField(max_length=128, blank=True)
    vault_pin_failures = models.PositiveSmallIntegerField(default=0)
    vault_locked_until = models.DateTimeField(null=True, blank=True)

    # Per-user display preferences, applied only by the frontend.
    date_format = models.CharField(max_length=12, choices=DATE_FORMATS, default="DD/MM/YYYY")
    number_grouping = models.CharField(max_length=15, choices=NUMBER_GROUPINGS, default="indian")

    # The notification bell's "Clear all": keys of the items that were showing when this person
    # cleared it. They stay hidden; anything new (a new key) shows again. See ClearedAlertsView.
    cleared_alerts = models.JSONField(default=list, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name or self.email

    @property
    def is_admin(self) -> bool:
        return self.role.is_admin

    def can(self, module: str, action: str) -> bool:
        if not self.is_active:
            return False
        if self.role.is_admin:
            return True
        modules = DERIVED_PERMISSIONS.get(module, [module])
        return self.role.module_permissions.filter(module__in=modules, **{f"can_{action}": True}).exists()

    def has_flag(self, flag: str) -> bool:
        return self.is_active and self.role.flags().get(flag, False)


def _invite_token():
    return secrets.token_urlsafe(32)


def hash_invite_token(raw: str) -> str:
    """Only this hash is stored, so a copy of the database can't be used to join the firm."""
    import hashlib

    return hashlib.sha256(("dealflow-invite:" + (raw or "")).encode()).hexdigest()


INVITE_HOURS = 72


def _invite_expiry():
    return timezone.now() + timedelta(hours=INVITE_HOURS)


class Invite(models.Model):
    """One-time link an admin shares by any channel. The person sets their own
    password; the role is fixed by the admin."""

    token = models.CharField(max_length=64, unique=True)  # SHA-256 of the link's secret, never the secret
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="invites")
    note = models.CharField(max_length=150, blank=True, help_text="Who it is for, for the admin's reference")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_invite_expiry)
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, **fields) -> tuple["Invite", str]:
        """A new invite and its link secret, which is shown once and never stored."""
        raw = _invite_token()
        return cls.objects.create(token=hash_invite_token(raw), **fields), raw

    @classmethod
    def find(cls, raw: str):
        if not isinstance(raw, str) or not 20 <= len(raw) <= 100:
            return None
        return cls.objects.filter(token=hash_invite_token(raw))

    @property
    def is_usable(self) -> bool:
        return not self.revoked and self.used_at is None and self.expires_at > timezone.now()


class PasswordHistory(models.Model):
    """Hashes of a person's recent passwords, so they aren't reused (accounts/validators.py)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]


class SignInFailure(models.Model):
    """Failed sign-ins counted per account (by a hash of the email, so unknown addresses count the
    same way and nothing reveals which accounts exist) and per address (accounts/lockout.py)."""

    key = models.CharField(max_length=80, unique=True)
    count = models.PositiveIntegerField(default=0)
    first_at = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)


# ---------------------------------------------------------------- two-step login (decision 63, S2)


class TotpDevice(models.Model):
    """An authenticator app (Google Authenticator, Microsoft Authenticator, 1Password...). The
    secret is stored encrypted; `last_step` stops a code being used twice."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp")
    secret = models.TextField()  # sealed (accounts/mfa.py)
    confirmed = models.BooleanField(default=False)
    last_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Passkey(models.Model):
    """A passkey or security key (WebAuthn). Only the public key is stored."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passkeys")
    credential_id = models.CharField(max_length=1400, unique=True)  # base64url
    public_key = models.TextField()  # base64url COSE key
    sign_count = models.BigIntegerField(default=0)
    name = models.CharField(max_length=80)
    transports = models.JSONField(default=list, blank=True)
    backed_up = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class RecoveryCode(models.Model):
    """One-time codes for when the phone or key is lost. Hashed like passwords."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    hash = models.CharField(max_length=256)
    used_at = models.DateTimeField(null=True, blank=True)


class UserSession(models.Model):
    """A signed-in browser, for "Signed-in devices" and new-device alerts. Kept 90 days after it ends."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    session_key = models.CharField(max_length=40, db_index=True)
    ip = models.CharField(max_length=70, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    device = models.CharField(max_length=80, blank=True)  # "Chrome on macOS"
    device_hash = models.CharField(max_length=64, blank=True)
    method = models.CharField(max_length=20, blank=True)  # passkey / app / recovery / password
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    new_device = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_seen"]
