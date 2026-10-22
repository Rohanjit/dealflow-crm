"""Investor data rooms (decision 64).

One room per deal. Its files are copies of Vault documents, encrypted with the rooms key (made from
the install's master key), because investors have no Vault PIN and the server has to read a file to
watermark it. Each investor on the deal's tracker gets their own link (a share); every opening needs a
one-time code emailed to that investor. Everything an investor does is kept as visits.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def _stored_path(instance, filename: str) -> str:
    now = timezone.now()
    return f"rooms/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.dfr"


def _user_fk(**kw):
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="+", **kw)


NDA_TEXT = """By opening this data room you agree that:

1. Everything in it is confidential and is shared only so that you can consider an investment in the company.
2. You will not copy, forward or show it to anyone outside your firm and your professional advisers, who are bound by the same duty.
3. You will not contact the company's employees, customers or suppliers about it without our agreement.
4. You will delete or return the material if we ask, or if you decide not to proceed.

Your name, the time and your internet address are recorded when you agree."""


class Room(models.Model):
    NDA_MODES = [("none", "No NDA"), ("click", "Agree on screen"), ("signed", "Signed NDA required")]

    deal = models.OneToOneField("deals.Deal", on_delete=models.CASCADE, related_name="room")
    name = models.CharField(max_length=200)
    welcome = models.TextField(blank=True)
    nda_mode = models.CharField(max_length=10, choices=NDA_MODES, default="click")
    nda_text = models.TextField(default=NDA_TEXT)
    is_open = models.BooleanField(default=True)  # closing the room stops every link
    # Where the room was set up from, for links when no investor address (DEALFLOW_ROOMS_DOMAIN) is set.
    link_base = models.CharField(max_length=300, blank=True)
    # The first share on a deal needs an approver (the deal's MD, else lead banker, else an admin).
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = _user_fk()
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class RoomFile(models.Model):
    MODES = [("view", "View only"), ("download", "Download with watermark")]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="files")
    document = models.ForeignKey("vault.Document", on_delete=models.CASCADE, related_name="room_files")
    title = models.CharField(max_length=255)
    mode = models.CharField(max_length=10, choices=MODES, default="view")
    position = models.PositiveIntegerField(default=0)
    stored = models.FileField(upload_to=_stored_path, max_length=255)
    file_name = models.CharField(max_length=255)
    ext = models.CharField(max_length=10)
    file_type = models.CharField(max_length=20)
    file_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    pages = models.PositiveIntegerField(default=0)  # PDFs (and 1 for images): what can be shown page by page
    added_by = _user_fk()
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "added_at", "id"]
        unique_together = [("room", "document")]


class Share(models.Model):
    """One investor's link to one room."""

    STATUSES = [("waiting", "Waiting for approval"), ("active", "Active"), ("revoked", "Stopped")]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="shares")
    entry = models.ForeignKey("deals.DealInvestor", on_delete=models.CASCADE, related_name="room_shares")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=255)
    token_hash = models.CharField(max_length=64, unique=True)
    token_sealed = models.JSONField()  # so the banker can copy or resend the link
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
    expires_at = models.DateTimeField()
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = _user_fk()
    invited_at = models.DateTimeField(null=True, blank=True)
    invite_error = models.CharField(max_length=300, blank=True)
    nda_name = models.CharField(max_length=200, blank=True)
    nda_at = models.DateTimeField(null=True, blank=True)
    nda_ip = models.CharField(max_length=70, blank=True)
    nda_received_at = models.DateTimeField(null=True, blank=True)
    nda_received_by = _user_fk()
    locked_until = models.DateTimeField(null=True, blank=True)  # too many wrong codes
    first_opened_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    suggestion_set_aside = models.CharField(max_length=30, blank=True)  # tracker stage the banker said "not now" to

    class Meta:
        ordering = ["-created_at"]

    @property
    def nda_done(self) -> bool:
        mode = self.room.nda_mode
        return mode == "none" or (mode == "click" and self.nda_at is not None) or (
            mode == "signed" and self.nda_received_at is not None)


class Code(models.Model):
    """A one-time code emailed to the investor: 6 digits, 10 minutes, 5 tries."""

    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    tries = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    ip = models.CharField(max_length=70, blank=True)


class Visitor(models.Model):
    """An investor's open room in one browser (after the code): idle 30 minutes, 12 hours at most."""

    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="visitors")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ip = models.CharField(max_length=70, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)


class Visit(models.Model):
    """Something an investor did: opened the room, read a file (for how long, which pages), downloaded
    one, agreed to the NDA, asked a question; or a code sent or refused."""

    # Read as "<investor> <label>", e.g. "Priya Shah (Blume Ventures) read “IM”".
    KINDS = [("code_sent", "was emailed a code"), ("code_wrong", "entered a wrong code"),
             ("locked", "was paused after too many wrong codes"), ("opened", "opened the room"),
             ("nda", "agreed to the NDA"), ("viewed", "read"), ("downloaded", "downloaded"),
             ("question", "asked a question")]

    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="visits")
    visitor = models.ForeignKey(Visitor, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=12, choices=KINDS)
    file = models.ForeignKey(RoomFile, on_delete=models.SET_NULL, null=True, blank=True, related_name="visits")
    file_title = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(default=timezone.now)
    last_at = models.DateTimeField(default=timezone.now)
    seconds = models.PositiveIntegerField(default=0)
    pages = models.JSONField(default=list, blank=True)  # pages seen while reading
    ip = models.CharField(max_length=70, blank=True)
    trace = models.CharField(max_length=40, blank=True)  # downloads: the tag hidden in the file

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["share", "kind"]), models.Index(fields=["trace"])]


class Question(models.Model):
    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="questions")
    file = models.ForeignKey(RoomFile, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    file_title = models.CharField(max_length=255, blank=True)
    page = models.PositiveIntegerField(null=True, blank=True)
    text = models.TextField()
    asked_at = models.DateTimeField(auto_now_add=True)
    answer = models.TextField(blank=True)
    answered_by = _user_fk()
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-asked_at"]
