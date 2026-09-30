from django.db.models import Q

from core.registry import Exit, FieldDef, ModuleDef, PipelineDef, proxy, register
from outreach.fields import COMPANY_FIELDS

from .models import (
    EXCLUSIVITY, INSTRUMENTS, PHASES, STAGES, STATUSES, TRACKER_STAGES, TRACKER_STATUSES, Deal, DealInvestor,
)

# ---------------------------------------------------------------- deals

CO, FS, MA, TE, TT, FE, TL, CL, OT = (
    "Company", "Financial snapshot", "Mandate", "Deal team", "Transaction terms", "Fees", "Timeline", "Closure", "Other",
)


def _company(key: str, group: str, **changes) -> FieldDef:
    """A company-level field shown on the deal; saved on the company (shared with its outreach)."""
    src = next(f for f in COMPANY_FIELDS if f.key == key)
    return proxy(src, via="company", via_module="companies", group=group, **changes)


DEAL_FIELDS = [
    FieldDef("company", "Company", "link", CO, required=True, always_on=True, list_default=True, filterable=True,
             searchable=True, targets={"companies": "company"},
             help="Pick the company, or type a new name to add it.",
             aliases=["company", "company name", "client", "deal", "name"]),
    _company("sector", CO, list_default=True),
    _company("sub_sector", CO),
    _company("geography", CO),
    _company("city", CO),
    _company("website", CO),
    _company("year_founded", CO),
    _company("description", CO),
    _company("financials_fy", FS),
    _company("revenue", FS),
    _company("ebitda", FS),
    _company("pat", FS),
    _company("valuation", FS),

    FieldDef("transaction_type", "Transaction type", "dropdown", MA, choice_list="transaction_types",
             list_default=True, filterable=True, aliases=["deal type", "mandate type", "type"]),
    FieldDef("source", "Source", "dropdown", MA, choice_list="outreach_sources", filterable=True),
    FieldDef("mandate_date", "Mandate date", "date", MA, filterable=True,
             aliases=["mandate signed", "mandate signed on", "engagement date"]),
    FieldDef("engagement_letter_ref", "Engagement letter reference", "text", MA, searchable=True,
             aliases=["engagement letter", "el reference", "el ref", "mandate reference"]),
    FieldDef("exclusivity", "Exclusivity", "choice", MA, choices=EXCLUSIVITY, filterable=True,
             aliases=["exclusive", "exclusive / non-exclusive"]),
    FieldDef("mandate_expiry", "Mandate valid until", "date", MA, filterable=True, always_on=True,
             help="Shown on the home page as the date gets close.",
             aliases=["mandate expiry", "validity", "expiry", "expiry date", "mandate end date"]),
    FieldDef("outreach", "From outreach", "link", MA, readonly=True, targets={"outreach": "outreach"},
             help="The outreach attempt that won this mandate."),

    FieldDef("lead_banker", "Lead banker", "user", TE, list_default=True, filterable=True,
             aliases=["banker", "lead", "owner", "deal lead"]),
    FieldDef("team_md", "MD", "user", TE, multi=True, filterable=True),
    FieldDef("team_vp", "VP", "user", TE, multi=True, filterable=True),
    FieldDef("team_associate", "Associate", "user", TE, multi=True, filterable=True),
    FieldDef("team_analyst", "Analyst", "user", TE, multi=True, filterable=True),

    FieldDef("target_raise", "Target raise", "money", TT, list_default=True,
             aliases=["deal size", "raise", "size", "issue size", "round size"]),
    FieldDef("instrument", "Instrument", "choice", TT, choices=INSTRUMENTS, filterable=True,
             help="Equity, CCPS and CCD are matched with equity investors, Debt with debt investors."),
    FieldDef("investment_stage", "Company stage", "dropdown", TT, choice_list="investment_stages", filterable=True,
             help="Used to suggest investors whose stage focus fits.", aliases=["stage of company", "round"]),
    FieldDef("pre_money_valuation", "Pre-money valuation expectation", "money", TT,
             aliases=["pre-money", "pre money valuation", "valuation expectation"]),
    FieldDef("primary_secondary_split", "Primary / secondary split", "text", TT,
             help="e.g. 80% primary, 20% secondary", aliases=["primary secondary split", "primary/secondary"]),
    FieldDef("use_of_funds", "Use of funds", "longtext", TT, searchable=True),

    FieldDef("agreed_fee_terms", "Agreed fees", "text", FE, help="e.g. ₹25 L retainer + 2% success fee",
             aliases=["fees", "fee terms", "final agreed fees"]),
    FieldDef("agreed_retainer", "Agreed retainer", "money", FE, aliases=["retainer"]),
    FieldDef("retainer_received", "Retainer received", "boolean", FE, filterable=True),
    FieldDef("retainer_paid", "Retainer received so far", "money", FE, aliases=["retainer received amount"]),
    FieldDef("success_fee_pct", "Success fee %", "percent", FE, aliases=["success fee", "fee %"]),
    FieldDef("minimum_success_fee", "Minimum success fee", "money", FE, aliases=["minimum fee", "min success fee"]),

    FieldDef("stage", "Stage", "choice", TL, choices=STAGES, list_default=True, filterable=True, always_on=True,
             pipeline_role="stage", aliases=["deal stage", "current stage"]),
    FieldDef("status", "Status", "choice", TL, choices=STATUSES, filterable=True, always_on=True,
             pipeline_role="status"),
    FieldDef("stage_entered", "In this stage since", "date", TL, filterable=True, always_on=True,
             pipeline_role="entered", aliases=["stage entered", "since"]),
    FieldDef("target_close_date", "Target close date", "date", TL, list_default=True, filterable=True,
             always_on=True, help="Shown on the home page as the date gets close.",
             aliases=["target close", "expected close", "expected closing", "closing target"]),

    FieldDef("amount_raised", "Amount raised", "money", CL, aliases=["raised"]),
    FieldDef("final_valuation", "Final valuation", "money", CL, aliases=["post-money", "deal valuation"]),
    FieldDef("lead_investors", "Lead investor(s)", "link", CL, multi=True, filterable=True,
             targets={"investors": "lead_investors"}, aliases=["lead investor", "lead investors"]),
    FieldDef("closing_date", "Closing date", "date", CL, filterable=True, aliases=["closed on", "close date"]),
    FieldDef("fee_earned", "Fee earned", "money", CL, aliases=["fees earned", "success fee earned"]),

    FieldDef("tags", "Tags", "tags", OT, filterable=True, aliases=["labels"]),
]

DEAL_VIEWS = [
    {"value": "active", "label": "Active"},
    {"value": "closed", "label": "Closed"},
    {"value": "on_hold", "label": "On hold"},
    {"value": "ended", "label": "Withdrawn / dropped"},
    {"value": "all", "label": "All"},
]
TEAM_FIELDS = ["lead_banker", "team_md", "team_vp", "team_associate", "team_analyst"]


def deal_filters(qs, params, user=None):
    """?view=active (default in the app) | closed | on_hold | ended | all, and ?team=me|<user id>
    (deals where that person is the lead banker or on the deal team)."""
    view = params.get("view")
    if view == "active":
        qs = qs.filter(status="open").exclude(stage="closed")
    elif view == "closed":
        qs = qs.filter(stage="closed", status="open")
    elif view == "on_hold":
        qs = qs.filter(status="on_hold")
    elif view == "ended":
        qs = qs.filter(status__in=["withdrawn", "dropped"])
    if team := params.get("team"):
        user_id = getattr(user, "pk", None) if team == "me" else team
        if str(user_id).isdigit():
            cond = Q()
            for key in TEAM_FIELDS:
                cond |= Q(**{key: int(user_id)})
            qs = qs.filter(pk__in=Deal.objects.filter(cond).values("pk"))
    return qs


def deal_subtitle(d: Deal) -> str:
    stage = d.get_stage_display() if d.status == "open" else f"{d.get_status_display()} at {d.get_stage_display()}"
    return " · ".join(p for p in (d.transaction_type.label if d.transaction_type_id else "", stage) if p)


DEALS = register(
    ModuleDef(
        key="deals",
        label="Active Deals",
        singular="Deal",
        model=Deal,
        fields=DEAL_FIELDS,
        groups=[CO, FS, MA, TE, TT, FE, TL, CL, OT],
        title_field="company",
        title_path="company__name",
        title_fn=lambda d: d.company.name,
        subtitle=deal_subtitle,
        extra_filters=deal_filters,
        segment_param="view",
        segment_options=DEAL_VIEWS,
        segment_default="active",
        pipeline=PipelineDef(
            stages=STAGES,
            groups=PHASES,
            exits=[
                Exit("on_hold", "On hold", "Put on hold", "Put on hold", paused=True),
                Exit("withdrawn", "Withdrawn by company", "Company withdrew", "Withdrawn by company"),
                Exit("dropped", "Dropped", "Drop the deal", "Dropped", tone="gray"),
            ],
            won="closed",
        ),
        timeline=True,
        importable=False,
        sections=[
            {"key": "tracker", "label": "Investor tracker", "module": "deal_investors", "link_field": "deal",
             "panel": "tracker"},
            {"key": "tasks", "label": "Tasks", "module": "tasks", "link_field": "related", "panel": "tasks",
             "order": "status,due"},
            {
                "key": "contacts",
                "label": "Company contacts",
                "module": "contacts",
                "link_field": "firm",
                "via": "company",
                "columns": ["full_name", "designation", "decision_level", "primary_contact", "work_email", "mobile"],
                "order": "-primary_contact,decision_level,full_name",
            },
            {"key": "documents", "label": "Documents", "module": "vault", "panel": "documents"},
        ],
    )
)

# ---------------------------------------------------------------- investor tracker

TI, TR, TN = "Investor", "Tracking", "Notes"

TRACKER_FIELDS = [
    FieldDef("deal", "Deal", "link", TI, required=True, always_on=True, list_default=True, filterable=True,
             targets={"deals": "deal"}),
    FieldDef("investor", "Investor", "link", TI, required=True, always_on=True, list_default=True, filterable=True,
             targets={"investors": "investor"}),
    FieldDef("stage", "Stage", "choice", TR, choices=TRACKER_STAGES, list_default=True, filterable=True,
             always_on=True, pipeline_role="stage"),
    FieldDef("status", "Status", "choice", TR, choices=TRACKER_STATUSES, list_default=True, filterable=True,
             always_on=True, pipeline_role="status"),
    FieldDef("stage_entered", "In this stage since", "date", TR, list_default=True, filterable=True, always_on=True,
             pipeline_role="entered"),
    FieldDef("decline_reason", "Decline reason", "dropdown", TR, choice_list="decline_reasons", filterable=True,
             always_on=True, pipeline_role="exit"),
    FieldDef("notes", "Notes", "longtext", TN, searchable=True),
]


def tracker_filters(qs, params, user=None):
    """Entries of archived deals stay out of lists (an investor's deal history, for one)."""
    return qs.filter(deal__archived_at__isnull=True)


def tracker_validate(values: dict, instance, importing: bool) -> dict:
    """An investor appears once on a deal's tracker."""
    if "deal" not in values and "investor" not in values:
        return {}
    saved = instance is not None and instance.pk
    deal = values["deal"][1] if values.get("deal") else (instance.deal if saved else None)
    investor = values["investor"][1] if values.get("investor") else (instance.investor if saved else None)
    if not deal or not investor:
        return {}
    clash = DealInvestor.objects.filter(deal=deal, investor=investor, archived_at__isnull=True)
    if saved:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        return {"investor": f"{investor.firm_name} is already on this deal's tracker."}
    return {}


def tracker_subtitle(r: DealInvestor) -> str:
    stage = r.get_stage_display() if r.status == "open" else f"{r.get_status_display()} at {r.get_stage_display()}"
    return f"{r.deal.company.name} · {stage}"


TRACKER = register(
    ModuleDef(
        key="deal_investors",
        label="Investor tracker",
        singular="Tracked investor",
        model=DealInvestor,
        fields=TRACKER_FIELDS,
        groups=[TI, TR, TN],
        title_field="investor",
        title_path="investor__firm_name",
        title_fn=lambda r: r.investor.firm_name,
        subtitle=tracker_subtitle,
        extra_filters=tracker_filters,
        validate=tracker_validate,
        pipeline=PipelineDef(
            stages=TRACKER_STAGES,
            exits=[
                Exit("declined", "Declined", "Mark as declined", "Declined", reason_field="decline_reason"),
                Exit("no_response", "No response", "Mark as no response", "No response", tone="gray"),
            ],
            won="selected",
        ),
        timeline=True,
        importable=False,
        in_search=False,
    )
)
