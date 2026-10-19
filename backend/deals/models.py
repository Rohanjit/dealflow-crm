from django.conf import settings
from django.db import models

from core.models import ChoiceOption, Record, Tag, money_columns

# Active Deals: fixed stages in two phases (configurable stages are not in V1).
STAGES = [
    ("data_collection", "Data collection"),
    ("first_draft", "First draft"),
    ("pre_finalisation", "Pre-finalisation"),
    ("finalisation", "Finalisation"),
    ("investors_identified", "Investors identified"),
    ("investor_outreach", "Investor outreach"),
    ("collaterals", "Collaterals / data room"),
    ("management_meetings", "Management meetings"),
    ("term_sheet", "Term sheet"),
    ("due_diligence", "Due diligence"),
    ("documentation", "Documentation"),
    ("closed", "Closed"),
]
PHASES = [
    ("Preparation", ["data_collection", "first_draft", "pre_finalisation", "finalisation", "investors_identified"]),
    ("Execution", ["investor_outreach", "collaterals", "management_meetings", "term_sheet", "due_diligence",
                   "documentation", "closed"]),
]
STATUSES = [("open", "Active"), ("on_hold", "On hold"), ("withdrawn", "Withdrawn by company"), ("dropped", "Dropped")]
EXCLUSIVITY = [("exclusive", "Exclusive"), ("non_exclusive", "Non-exclusive")]
INSTRUMENTS = [("equity", "Equity"), ("ccps", "CCPS"), ("ccd", "CCD"), ("debt", "Debt"), ("mix", "Mix")]

# Each deal's investor tracker.
TRACKER_STAGES = [
    ("identified", "Identified"),
    ("teaser_sent", "Teaser sent"),
    ("nda_signed", "NDA signed"),
    ("collaterals_shared", "Collaterals / data room shared"),
    ("meeting_held", "Meeting held"),
    ("term_sheet_received", "Term sheet received"),
    ("selected", "Selected"),
]
TRACKER_STATUSES = [("open", "Active"), ("declined", "Declined"), ("no_response", "No response")]


def _user_fk():
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")


def _option_fk():
    return models.ForeignKey(ChoiceOption, on_delete=models.PROTECT, null=True, blank=True, related_name="+")


class Deal(Record):
    """A mandate being executed. Created automatically when an outreach attempt reaches
    Mandate won (or added by hand). Company details live on the company and are shared."""

    company = models.ForeignKey("outreach.Company", on_delete=models.PROTECT, related_name="deals")
    outreach = models.OneToOneField(
        "outreach.Outreach", on_delete=models.SET_NULL, null=True, blank=True, related_name="deal"
    )

    # Mandate
    transaction_type = _option_fk()
    source = _option_fk()
    mandate_date = models.DateField(null=True, blank=True)
    engagement_letter_ref = models.CharField(max_length=255, blank=True)
    exclusivity = models.CharField(max_length=15, choices=EXCLUSIVITY, blank=True)
    mandate_expiry = models.DateField(null=True, blank=True)

    # Deal team
    lead_banker = _user_fk()
    team_md = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    team_vp = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    team_associate = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    team_analyst = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")

    # Restricted deals (deals/access.py): only the deal team, the people in `access` and admins see them.
    # Changed through access requests, never through the generic forms.
    restricted = models.BooleanField(default=False, editable=False)
    access = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="deal_access", editable=False)

    # Transaction terms
    target_raise_amount, target_raise_currency, target_raise_unit, target_raise_value = money_columns("Target raise")
    instrument = models.CharField(max_length=10, choices=INSTRUMENTS, blank=True)
    investment_stage = _option_fk()
    pre_money_valuation_amount, pre_money_valuation_currency, pre_money_valuation_unit, pre_money_valuation_value = (
        money_columns("Pre-money valuation")
    )
    primary_secondary_split = models.CharField(max_length=255, blank=True)
    use_of_funds = models.TextField(blank=True)

    # Fees
    agreed_fee_terms = models.CharField(max_length=255, blank=True)
    agreed_retainer_amount, agreed_retainer_currency, agreed_retainer_unit, agreed_retainer_value = (
        money_columns("Agreed retainer")
    )
    retainer_received = models.BooleanField(default=False)
    retainer_paid_amount, retainer_paid_currency, retainer_paid_unit, retainer_paid_value = (
        money_columns("Retainer received so far")
    )
    success_fee_pct = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    minimum_success_fee_amount, minimum_success_fee_currency, minimum_success_fee_unit, minimum_success_fee_value = (
        money_columns("Minimum success fee")
    )

    # Timeline
    stage = models.CharField(max_length=30, choices=STAGES, default="data_collection")
    status = models.CharField(max_length=12, choices=STATUSES, default="open")
    stage_entered = models.DateField(null=True, blank=True)
    target_close_date = models.DateField(null=True, blank=True)

    # Closure
    amount_raised_amount, amount_raised_currency, amount_raised_unit, amount_raised_value = money_columns("Amount raised")
    final_valuation_amount, final_valuation_currency, final_valuation_unit, final_valuation_value = (
        money_columns("Final valuation")
    )
    lead_investors = models.ManyToManyField("investors.Investor", blank=True, related_name="+")
    closing_date = models.DateField(null=True, blank=True)
    fee_earned_amount, fee_earned_currency, fee_earned_unit, fee_earned_value = money_columns("Fee earned")

    # Other
    tags = models.ManyToManyField(Tag, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company} ({self.get_stage_display()})"

    def save(self, *args, **kwargs):
        # Ordinary saves (forms, imports, stage moves) never write `restricted`: only deals/access.py
        # does, with update_fields. Otherwise a form opened before an MD restricted the deal would
        # quietly lift the restriction when saved.
        if self.pk and not kwargs.get("update_fields") and not kwargs.get("force_insert") and not self._state.adding:
            kwargs["update_fields"] = [f.name for f in self._meta.concrete_fields if not f.primary_key and f.name != "restricted"]
        super().save(*args, **kwargs)


class DealInvestor(Record):
    """One investor on one deal's tracker: how far they've got, or why they dropped out."""

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="tracker")
    investor = models.ForeignKey("investors.Investor", on_delete=models.PROTECT, related_name="deal_entries")
    stage = models.CharField(max_length=30, choices=TRACKER_STAGES, default="identified")
    status = models.CharField(max_length=12, choices=TRACKER_STATUSES, default="open")
    stage_entered = models.DateField(null=True, blank=True)
    decline_reason = _option_fk()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "deal tracker entry"
        verbose_name_plural = "deal tracker entries"

    def __str__(self):
        return f"{self.investor} on {self.deal}"


class AccessRequest(models.Model):
    """A request about who can see a restricted deal, waiting for (or decided by) an approver:
    restrict or lift the restriction, give someone access, or someone asking for access themselves."""

    KINDS = [("restrict", "Restrict the deal"), ("unrestrict", "Lift the restriction"),
             ("add", "Give access"), ("join", "Asked for access")]
    STATUSES = [("pending", "Waiting"), ("approved", "Approved"), ("declined", "Declined"), ("withdrawn", "Withdrawn")]

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="access_requests")
    kind = models.CharField(max_length=12, choices=KINDS)
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="pending")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
