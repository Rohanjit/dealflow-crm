"""Email and calendar logging (decision 64). A person connects their own mailbox and calendar;
emails and meetings with people already in Dealflow are logged on those people, their firms and
(when it's clear) their deal. Only the subject, time and participants are copied: the text of an
email, a meeting's notes and attachments stay in the mailbox and are fetched live for the owner."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Connection(models.Model):
    KINDS = [("google", "Google"), ("microsoft", "Microsoft 365"), ("icloud", "Apple iCloud"),
             ("other", "Other (IMAP / CalDAV)")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sync_connections")
    kind = models.CharField(max_length=10, choices=KINDS)
    email = models.CharField(max_length=255)
    # Sealed JSON: OAuth tokens, or the app password and server addresses (mail.crypto).
    secret = models.TextField(blank=True)
    mail = models.BooleanField(default=True)
    calendar = models.BooleanField(default=True)
    connected_at = models.DateTimeField(default=timezone.now)  # logging starts here: nothing older is read
    mail_cursor = models.JSONField(default=dict, blank=True)
    calendar_cursor = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    needs_reconnect = models.BooleanField(default=False)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [("user", "kind")]
        ordering = ["connected_at", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.email}"


class Item(models.Model):
    """One email or meeting, logged once however many colleagues' mailboxes hold it."""

    KINDS = [("email", "Email"), ("meeting", "Meeting")]

    kind = models.CharField(max_length=10, choices=KINDS)
    key = models.CharField(max_length=500, unique=True)  # e:<Message-ID> or m:<calendar uid>:<start>
    subject = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField()  # sent, or the meeting's start
    end = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    # [{"name", "email", "contact": id|None, "user": id|None, "role": from|to|cc|organizer|attendee}]
    participants = models.JSONField(default=list)
    attachments = models.JSONField(default=list, blank=True)  # [{"name", "size", "type"}]: names only
    cancelled = models.BooleanField(default=False)
    # Records it belongs to ("contacts:3", "investors:2", "deals:5", ...). Emails are logged straight
    # away; meetings once they've ended (upcoming ones show from here).
    records = models.JSONField(default=list, blank=True)
    logged = models.BooleanField(default=False)
    # Several open deals could be meant: the owner picks one (or none).
    choices = models.JSONField(default=list, blank=True)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="+")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="+")  # whose mailbox first had it: writes its log entries
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["kind", "at"])]


class Copy(models.Model):
    """Where an item lives in one connected mailbox or calendar: how its owner opens it again."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="copies")
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE, related_name="copies")
    remote_id = models.CharField(max_length=1000)
    extra = models.JSONField(default=dict, blank=True)  # e.g. the calendar it's in, attachment parts

    class Meta:
        unique_together = [("connection", "remote_id")]


class Prefs(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sync_prefs")
    # Minutes before a meeting the bell reminds; null = no reminder.
    reminder_minutes = models.PositiveSmallIntegerField(null=True, blank=True, default=15)

    @classmethod
    def for_user(cls, user) -> "Prefs":
        return cls.objects.get_or_create(user=user)[0]
