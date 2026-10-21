"""Email from Dealflow (decision 62): each person's own Gmail or Outlook, one-to-one emails and
campaigns, every send confirmed in the mailbox's Sent folder and logged on the records it was about."""

from django.conf import settings
from django.db import models

PROVIDERS = [("gmail", "Gmail"), ("outlook", "Outlook")]


class MailSettings(models.Model):
    """Singleton (pk=1): the firm's app registrations with Google and Microsoft, and sending limits.
    Secrets are stored encrypted (mail/crypto.py) and never sent back to the browser."""

    google_client_id = models.CharField(max_length=255, blank=True)
    google_client_secret = models.TextField(blank=True)
    microsoft_client_id = models.CharField(max_length=255, blank=True)
    microsoft_client_secret = models.TextField(blank=True)
    microsoft_tenant = models.CharField(max_length=100, default="common")
    # The address people open Dealflow at (https://crm.firm.com). Google and Microsoft send people
    # back to <this>/api/mail/callback/<provider>/ after they connect; empty = the address in use.
    public_url = models.CharField(max_length=300, blank=True)
    # Per mailbox, kept under the providers' own limits so accounts aren't flagged.
    gmail_daily_limit = models.PositiveIntegerField(default=400)
    outlook_daily_limit = models.PositiveIntegerField(default=1000)
    per_minute = models.PositiveSmallIntegerField(default=20)
    opt_out_text = models.CharField(
        max_length=300, default="If you'd rather not receive emails like this, reply “unsubscribe” and we'll stop."
    )

    @classmethod
    def load(cls) -> "MailSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class MailAccount(models.Model):
    """A person's connected mailbox. Tokens are encrypted; disconnecting deletes them."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mailbox")
    provider = models.CharField(max_length=10, choices=PROVIDERS)
    email = models.CharField(max_length=255)
    tokens = models.TextField(blank=True)  # encrypted JSON {access, refresh, expires}
    connected_at = models.DateTimeField(auto_now_add=True)
    needs_reconnect = models.BooleanField(default=False)
    last_error = models.CharField(max_length=300, blank=True)
    sent_day = models.DateField(null=True, blank=True)
    sent_today = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.email} ({self.provider})"


class EmailTemplate(models.Model):
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_templates")
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]


class Campaign(models.Model):
    STATUSES = [("draft", "Draft"), ("scheduled", "Scheduled"), ("sending", "Sending"), ("paused", "Paused"),
                ("done", "Sent"), ("cancelled", "Cancelled")]

    name = models.CharField(max_length=120)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campaigns")
    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    # [{kind: "vault", id} | {kind: "upload", token, name, size, content_type}]
    attachments = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="draft")
    send_at = models.DateTimeField(null=True, blank=True)  # empty = as soon as it's started
    paused_reason = models.CharField(max_length=300, blank=True)
    resume_at = models.DateTimeField(null=True, blank=True)  # paused by a sending limit: carries on then
    source = models.JSONField(default=dict, blank=True)  # how recipients were picked, for the screen
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class CampaignRecipient(models.Model):
    STATUSES = [("queued", "Waiting"), ("sending", "Sending"), ("sent", "Sent"), ("failed", "Failed"),
                ("skipped", "Skipped")]

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    email = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True)
    merge = models.JSONField(default=dict, blank=True)  # the values filled into {first_name} and the rest
    status = models.CharField(max_length=10, choices=STATUSES, default="queued")
    reason = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_email = models.ForeignKey("SentEmail", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["pk"]
        indexes = [models.Index(fields=["campaign", "status"])]


class SentEmail(models.Model):
    """One email that left someone's mailbox through Dealflow, and whether its Sent folder has it."""

    STATUSES = [("pending", "Waiting for confirmation"), ("confirmed", "Confirmed sent"),
                ("unconfirmed", "Not found in Sent")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    provider = models.CharField(max_length=10, choices=PROVIDERS)
    from_email = models.CharField(max_length=255)
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=300, blank=True)
    message_id = models.CharField(max_length=300, blank=True)
    thread_id = models.CharField(max_length=300, blank=True)
    web_link = models.CharField(max_length=1000, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="pending")
    sent_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    checks = models.PositiveSmallIntegerField(default=0)
    checked_at = models.DateTimeField(null=True, blank=True)
    note_ids = models.JSONField(default=list, blank=True)  # the log entries it wrote
    contact_ids = models.JSONField(default=list, blank=True)  # Last contacted moves once confirmed
    campaign = models.ForeignKey(Campaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-sent_at", "-pk"]
        indexes = [models.Index(fields=["status", "provider"])]
