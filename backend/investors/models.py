from django.conf import settings
from django.db import models

from core.models import ChoiceOption, Record, Tag
from core.registry import CURRENCIES, UNITS

INSTRUMENT_CHOICES = [("equity", "Equity"), ("debt", "Debt"), ("both", "Both")]
STRENGTH_CHOICES = [("cold", "Cold"), ("warm", "Warm"), ("strong", "Strong")]
TRISTATE = [("yes", "Yes"), ("no", "No"), ("unknown", "Unknown")]


def _money(verbose):
    """amount / currency / unit, plus a normalised value (amount x unit) for sorting and
    range filters within one currency. Never converted across currencies."""
    return (
        models.DecimalField(verbose, max_digits=20, decimal_places=4, null=True, blank=True),
        models.CharField(max_length=3, choices=CURRENCIES, blank=True),
        models.CharField(max_length=5, choices=UNITS, blank=True),
        models.DecimalField(max_digits=30, decimal_places=4, null=True, blank=True, editable=False),
    )


class Investor(Record):
    # Basics
    firm_name = models.CharField(max_length=255)
    investor_type = models.ForeignKey(
        ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    website = models.CharField(max_length=255, blank=True)
    hq_city = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    # Investment preferences
    sectors = models.ManyToManyField(ChoiceOption, blank=True, related_name="+")
    stages = models.ManyToManyField(ChoiceOption, blank=True, related_name="+")
    min_ticket_amount, min_ticket_currency, min_ticket_unit, min_ticket_value = _money("Min ticket")
    max_ticket_amount, max_ticket_currency, max_ticket_unit, max_ticket_value = _money("Max ticket")
    geography_focus = models.ManyToManyField(ChoiceOption, blank=True, related_name="+")
    instrument = models.CharField(max_length=10, choices=INSTRUMENT_CHOICES, blank=True)

    # Firm profile
    fund_size_amount, fund_size_currency, fund_size_unit, fund_size_value = _money("Fund size / AUM")
    fund_vintage = models.IntegerField(null=True, blank=True)
    actively_deploying = models.CharField(max_length=10, choices=TRISTATE, default="unknown")

    # Track record
    portfolio_companies = models.TextField(blank=True)
    past_deals = models.TextField(blank=True)

    # Relationship
    relationship_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    relationship_strength = models.CharField(max_length=10, choices=STRENGTH_CHOICES, blank=True)
    last_contacted = models.DateField(null=True, blank=True)

    # Other
    tags = models.ManyToManyField(Tag, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["firm_name"]

    def __str__(self):
        return self.firm_name
