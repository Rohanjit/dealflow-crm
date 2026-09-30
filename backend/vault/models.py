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

    companies = models.ManyToManyField("outreach.Company", blank=True, related_name="documents")
    outreach = models.ManyToManyField("outreach.Outreach", blank=True, related_name="documents")
    deals = models.ManyToManyField("deals.Deal", blank=True, related_name="documents")
    investors = models.ManyToManyField("investors.Investor", blank=True, related_name="documents")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
