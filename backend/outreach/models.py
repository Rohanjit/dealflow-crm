from django.conf import settings
from django.db import models

from core.models import ChoiceOption, Record, Tag, money_columns

STAGES = [
    ("identified", "Identified"),
    ("contacted", "Contacted"),
    ("meeting", "Meeting held"),
    ("proposal", "Proposal sent"),
    ("negotiating", "Negotiating"),
    ("won", "Mandate won"),
]
STATUSES = [("open", "Active"), ("on_hold", "On hold"), ("lost", "Lost")]


class Company(Record):
    """A company the firm approaches for mandates. Recorded once; every approach is an
    Outreach attempt under it, and (later) its deals point here too."""

    # Company basics
    name = models.CharField(max_length=255)
    sector = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    sub_sector = models.CharField(max_length=255, blank=True)
    geography = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    city = models.CharField(max_length=255, blank=True)
    website = models.CharField(max_length=255, blank=True)
    year_founded = models.IntegerField(null=True, blank=True)
    description = models.TextField(blank=True)

    # Financial snapshot
    financials_fy = models.CharField(max_length=20, blank=True)
    revenue_amount, revenue_currency, revenue_unit, revenue_value = money_columns("Latest revenue")
    ebitda_amount, ebitda_currency, ebitda_unit, ebitda_value = money_columns("EBITDA")
    pat_amount, pat_currency, pat_unit, pat_value = money_columns("PAT")
    valuation_amount, valuation_currency, valuation_unit, valuation_value = money_columns("Valuation")

    # Other
    tags = models.ManyToManyField(Tag, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Outreach(Record):
    """One attempt to win a mandate from a company."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="outreach")

    # Opportunity
    transaction_type = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    expected_size_amount, expected_size_currency, expected_size_unit, expected_size_value = money_columns("Expected size")
    expected_timeline = models.CharField(max_length=255, blank=True)

    # People
    lead_banker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    source = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # Commercials
    proposed_retainer_amount, proposed_retainer_currency, proposed_retainer_unit, proposed_retainer_value = (
        money_columns("Proposed retainer")
    )
    proposed_success_fee = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    final_agreed_fees = models.CharField(max_length=255, blank=True)

    # Tracking
    stage = models.CharField(max_length=20, choices=STAGES, default="identified")
    status = models.CharField(max_length=10, choices=STATUSES, default="open")
    stage_entered = models.DateField(null=True, blank=True)
    next_follow_up = models.DateField(null=True, blank=True)

    # Exit
    lost_reason = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    hold_reason = models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    revisit_date = models.DateField(null=True, blank=True)

    # Other
    tags = models.ManyToManyField(Tag, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "outreach"

    def __str__(self):
        return f"{self.company} ({self.get_stage_display()})"
