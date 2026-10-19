from django.conf import settings
from django.db import models

from core.models import ChoiceOption, Record, Tag
from investors.models import STRENGTH_CHOICES

CHANNEL_CHOICES = [("email", "Email"), ("call", "Call"), ("whatsapp", "WhatsApp")]


class Contact(Record):
    """A person at an investor firm or at a client company."""

    # Basics
    full_name = models.CharField(max_length=255)
    designation = models.CharField(max_length=255, blank=True)
    # "Firm / company" is one link field; each possible target has its own column.
    investor = models.ForeignKey(
        "investors.Investor", on_delete=models.SET_NULL, null=True, blank=True, related_name="contacts"
    )
    company = models.ForeignKey(
        "outreach.Company", on_delete=models.SET_NULL, null=True, blank=True, related_name="contacts"
    )
    department = models.CharField(max_length=255, blank=True)

    # Contact details
    work_email = models.CharField(max_length=255, blank=True)
    personal_email = models.CharField(max_length=255, blank=True)
    mobile = models.CharField(max_length=255, blank=True)
    office_phone = models.CharField(max_length=255, blank=True)
    linkedin = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=255, blank=True)

    # Role in decisions
    decision_level = models.ForeignKey(
        ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    primary_contact = models.BooleanField(default=False)

    # Relationship
    relationship_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    relationship_strength = models.CharField(max_length=10, choices=STRENGTH_CHOICES, blank=True)
    last_contacted = models.DateField(null=True, blank=True)
    # "Snooze 30 days" on a going-cold alert: no alert until this day passes (core/stale.py).
    stale_snoozed_until = models.DateField(null=True, blank=True, editable=False)
    introduced_by = models.CharField(max_length=255, blank=True)

    # Personal
    birthday = models.CharField(max_length=5, null=True, blank=True, help_text="MM-DD")
    birthday_year = models.PositiveSmallIntegerField(null=True, blank=True)
    assistant_name = models.CharField(max_length=255, blank=True)
    assistant_contact = models.CharField(max_length=255, blank=True)
    preferred_channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, blank=True)

    # Other
    tags = models.ManyToManyField(Tag, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name
