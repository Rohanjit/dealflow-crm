import uuid

from django.db import models
from django.utils import timezone

from core.models import ChoiceOption, Record


def _stored_path(instance, filename: str) -> str:
    """Files are stored under a random name; the original name is kept only as data (`file_name`).
    The extension comes from the allowlist check, never straight from the upload."""
    now = timezone.now()
    ext = getattr(instance, "_ext", "") or "bin"
    return f"vault/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{ext}"


class Document(Record):
    """A file in the Vault, tagged to the companies, outreach attempts, deals and investors it concerns."""

    title = models.CharField(max_length=255)
    category = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.TextField(blank=True)

    file = models.FileField(upload_to=_stored_path, max_length=255)
    file_name = models.CharField(max_length=255)  # as uploaded, for display and downloads
    file_type = models.CharField(max_length=20)  # PDF, Word, Excel, …
    file_size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    # The Vault key the stored file is encrypted with (AES-256-GCM); empty = not encrypted yet
    # (files from before encryption, or from an older backup, are encrypted when the Vault is next opened).
    encrypted_with = models.ForeignKey("VaultKey", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    companies = models.ManyToManyField("outreach.Company", blank=True, related_name="documents")
    outreach = models.ManyToManyField("outreach.Outreach", blank=True, related_name="documents")
    deals = models.ManyToManyField("deals.Deal", blank=True, related_name="documents")
    investors = models.ManyToManyField("investors.Investor", blank=True, related_name="documents")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------- encryption (decision 63, S4)


class VaultKey(models.Model):
    """The firm's Vault key (32 random bytes) is never stored as it is: only wrapped for each person
    (to their public key) and for the recovery key. A new one comes with a rotation."""

    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)


class VaultKeyWrap(models.Model):
    KINDS = [("user", "Person"), ("recovery", "Recovery key")]

    vault_key = models.ForeignKey(VaultKey, on_delete=models.CASCADE, related_name="wraps")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=10, choices=KINDS, default="user")
    sealed = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("vault_key", "user", "kind")]


class UserVaultKeypair(models.Model):
    """A person's X25519 key pair for the Vault. The private half is sealed with a key made from their
    PIN (Argon2id) mixed with the install's master key, so it opens only with both."""

    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE, related_name="vault_keypair")
    public_key = models.CharField(max_length=64)
    private_sealed = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
