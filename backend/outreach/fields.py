import re
from urllib.parse import urlparse

from core.registry import Exit, FieldDef, ModuleDef, PipelineDef, proxy, register

from .models import STAGES, STATUSES, Company, Outreach

# ---------------------------------------------------------------- companies

CB, FS, OT = "Company", "Financial snapshot", "Other"

COMPANY_FIELDS = [
    FieldDef("name", "Company name", "text", CB, required=True, list_default=True, searchable=True,
             aliases=["company", "name", "client", "target", "target company", "account", "organisation",
                      "organization", "company name"]),
    FieldDef("sector", "Sector", "dropdown", CB, choice_list="sectors", list_default=True, filterable=True,
             aliases=["industry", "vertical"]),
    FieldDef("sub_sector", "Sub-sector", "text", CB, searchable=True,
             aliases=["sub sector", "subsector", "segment", "sub industry", "niche"]),
    FieldDef("geography", "Geography", "dropdown", CB, choice_list="geographies", filterable=True,
             help="Where the company mainly operates. Used to suggest investors for its deals.",
             aliases=["region", "country", "market"]),
    FieldDef("city", "City", "text", CB, list_default=True, searchable=True, filterable=True,
             aliases=["location", "hq", "hq city", "headquarters", "based in"]),
    FieldDef("website", "Website", "url", CB, searchable=True, aliases=["url", "web", "site"]),
    FieldDef("year_founded", "Year founded", "integer", CB,
             aliases=["founded", "founded in", "year of incorporation", "incorporated", "inception", "founding year"]),
    FieldDef("description", "Description", "longtext", CB, searchable=True,
             aliases=["about", "business description", "what they do", "overview", "business"]),

    FieldDef("financials_fy", "Financials for FY", "text", FS, help="The year the figures below are for, e.g. FY25.",
             aliases=["fy", "financial year", "fiscal year", "year of financials"]),
    FieldDef("revenue", "Latest revenue", "money", FS, list_default=True,
             aliases=["revenue", "sales", "turnover", "topline", "revenues"]),
    FieldDef("ebitda", "EBITDA", "money", FS, allow_negative=True, aliases=["operating profit"]),
    FieldDef("pat", "PAT", "money", FS, allow_negative=True,
             aliases=["net profit", "profit after tax", "net income", "profit"]),
    FieldDef("valuation", "Valuation (current / last)", "money", FS,
             aliases=["valuation", "current valuation", "last valuation", "post money", "post-money valuation"]),

    FieldDef("tags", "Tags", "tags", OT, filterable=True, aliases=["labels", "categories"]),
    FieldDef("notes", "Notes", "longtext", OT, searchable=True, aliases=["remarks", "comments"]),
]

# Words that don't tell two companies apart ("Acme" and "Acme Pvt. Ltd." are the same company).
_GENERIC = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "company", "corp", "corporation", "the",
            "india", "plc", "llc"}


def company_name_key(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    core = [w for w in words if w not in _GENERIC]
    return "".join(core or words)


def _domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def company_duplicate_keys(values: dict) -> list[str]:
    keys = []
    if n := company_name_key(values.get("name", "")):
        keys.append(f"name:{n}")
    if d := _domain(values.get("website", "")):
        keys.append(f"web:{d}")
    return keys


def company_subtitle(c: Company) -> str:
    return " · ".join(p for p in (c.sector.label if c.sector_id else "", c.city) if p)


COMPANIES = register(
    ModuleDef(
        key="companies",
        label="Companies",
        singular="Company",
        model=Company,
        fields=COMPANY_FIELDS,
        groups=[CB, FS, OT],
        title_field="name",
        subtitle=company_subtitle,
        duplicate_keys=company_duplicate_keys,
        name_key=company_name_key,
        sections=[
            {
                "key": "outreach",
                "label": "Outreach",
                "module": "outreach",
                "link_field": "company",
                "params": {"view": "all"},
                "columns": ["transaction_type", "stage", "status", "expected_size", "lead_banker", "next_follow_up"],
                "order": "-created_at",
            },
            {
                "key": "contacts",
                "label": "Contacts",
                "module": "contacts",
                "link_field": "firm",
                "columns": ["full_name", "designation", "decision_level", "primary_contact", "work_email", "mobile"],
                "order": "-primary_contact,decision_level,full_name",
            },
            {
                "key": "deals",
                "label": "Deals",
                "module": "deals",
                "link_field": "company",
                "params": {"view": "all"},
                "columns": ["transaction_type", "stage", "status", "target_raise", "lead_banker", "target_close_date"],
                "order": "-created_at",
            },
        ],
    )
)

# ---------------------------------------------------------------- outreach attempts

CO, OP, PE, CM, TR, EX, OO = "Company", "Opportunity", "People", "Commercials", "Tracking", "Exit", "Other"


def _company(key: str, group: str, **changes) -> FieldDef:
    """A company-level field shown on the attempt; saved on the company."""
    src = next(f for f in COMPANY_FIELDS if f.key == key)
    return proxy(src, via="company", via_module="companies", group=group, **changes)


OUTREACH_FIELDS = [
    FieldDef("company", "Company", "link", CO, required=True, always_on=True, list_default=True, filterable=True,
             searchable=True, targets={"companies": "company"},
             help="Pick the company, or type a new name to add it.",
             aliases=["company", "company name", "name", "client", "target", "target company", "account"]),
    _company("sector", CO, list_default=True),
    _company("sub_sector", CO),
    _company("geography", CO),
    _company("city", CO, list_default=False),
    _company("website", CO),
    _company("year_founded", CO),
    _company("description", CO),
    _company("financials_fy", FS),
    _company("revenue", FS, list_default=False),
    _company("ebitda", FS),
    _company("pat", FS),
    _company("valuation", FS),

    FieldDef("transaction_type", "Transaction type", "dropdown", OP, choice_list="transaction_types",
             list_default=True, filterable=True,
             aliases=["deal type", "mandate type", "transaction", "product", "type", "deal"]),
    FieldDef("expected_size", "Expected size", "money", OP, list_default=True,
             aliases=["deal size", "size", "expected deal size", "ticket size", "amount", "raise", "issue size"]),
    FieldDef("expected_timeline", "Expected timeline", "text", OP,
             help="e.g. Q3 FY27, or 6–9 months",
             aliases=["timeline", "timing", "expected timing", "expected close", "time frame"]),

    FieldDef("lead_banker", "Lead banker", "user", PE, list_default=True, filterable=True,
             aliases=["banker", "lead", "owner", "coverage", "rm", "relationship manager", "deal lead"]),
    FieldDef("source", "Source", "dropdown", PE, choice_list="outreach_sources", filterable=True,
             aliases=["lead source", "origin", "how we got it", "referral source"]),

    FieldDef("proposed_retainer", "Proposed retainer", "money", CM,
             aliases=["retainer", "retainer fee", "proposed retainer fee"]),
    FieldDef("proposed_success_fee", "Proposed success fee %", "percent", CM,
             aliases=["success fee", "success fee %", "fee %", "success fees"]),
    FieldDef("final_agreed_fees", "Final agreed fees", "text", CM,
             help="e.g. ₹25 L retainer + 2% success fee",
             aliases=["agreed fees", "final fees", "fees agreed", "fee agreed"]),

    FieldDef("stage", "Stage", "choice", TR, choices=STAGES, list_default=True, filterable=True, always_on=True,
             pipeline_role="stage", aliases=["pipeline stage", "current stage"]),
    FieldDef("status", "Status", "choice", TR, choices=STATUSES, filterable=True, always_on=True,
             pipeline_role="status", aliases=["state", "outcome"]),
    FieldDef("stage_entered", "In this stage since", "date", TR, filterable=True, always_on=True,
             pipeline_role="entered", aliases=["stage date", "stage entered", "stage-entered date", "since"]),
    FieldDef("next_follow_up", "Next follow-up", "date", TR, list_default=True, filterable=True,
             aliases=["follow up", "follow-up", "next follow up", "follow up date", "next step date",
                      "next action date", "next follow-up date"]),

    FieldDef("lost_reason", "Lost reason", "dropdown", EX, choice_list="lost_reasons", filterable=True,
             always_on=True, pipeline_role="exit", aliases=["reason lost", "why lost", "loss reason"]),
    FieldDef("hold_reason", "On-hold reason", "dropdown", EX, choice_list="hold_reasons", filterable=True,
             always_on=True, pipeline_role="exit", aliases=["on hold reason", "hold reason", "why on hold"]),
    FieldDef("revisit_date", "Revisit on", "date", EX, filterable=True, always_on=True, pipeline_role="exit",
             aliases=["revisit", "revisit date", "revisit on", "follow up after"]),

    FieldDef("tags", "Tags", "tags", OO, filterable=True, aliases=["labels", "categories"]),
]

VIEWS = [
    {"value": "active", "label": "Active"},
    {"value": "won", "label": "Won"},
    {"value": "on_hold", "label": "On hold"},
    {"value": "lost", "label": "Lost"},
    {"value": "all", "label": "All"},
]


def outreach_view_filter(qs, params, user=None):
    """?view=active (default in the app: still being worked) | won | on_hold | lost | all."""
    view = params.get("view")
    if view == "active":
        return qs.filter(status="open").exclude(stage="won")
    if view == "won":
        return qs.filter(stage="won")
    if view in ("on_hold", "lost"):
        return qs.filter(status=view)
    return qs


def outreach_duplicate_keys(values: dict) -> list[str]:
    """A company already being worked (active or on hold) shouldn't get a second attempt by accident."""
    company = values.get("company")
    if not company or values.get("stage") == "won" or (values.get("status") or "open") == "lost":
        return []
    return [f"open:{company}"]


def outreach_subtitle(o: Outreach) -> str:
    status = o.get_stage_display() if o.status == "open" else f"{o.get_status_display()} at {o.get_stage_display()}"
    return " · ".join(p for p in (o.transaction_type.label if o.transaction_type_id else "", status) if p)


OUTREACH = register(
    ModuleDef(
        key="outreach",
        label="Outreach",
        singular="Outreach",
        model=Outreach,
        fields=OUTREACH_FIELDS,
        groups=[CO, FS, OP, PE, CM, TR, EX, OO],
        title_field="company",
        title_path="company__name",
        title_fn=lambda o: o.company.name,
        subtitle=outreach_subtitle,
        duplicate_keys=outreach_duplicate_keys,
        extra_filters=outreach_view_filter,
        segment_param="view",
        segment_options=VIEWS,
        segment_default="active",
        pipeline=PipelineDef(
            stages=STAGES,
            exits=[
                Exit("on_hold", "On hold", "Put on hold", "Put on hold", paused=True,
                     reason_field="hold_reason", date_field="revisit_date"),
                Exit("lost", "Lost", "Mark as lost", "Marked lost", reason_field="lost_reason"),
            ],
            won="won",
        ),
        timeline=True,
        sections=[
            {
                "key": "deal",
                "label": "Deal",
                "module": "deals",
                "link_field": "outreach",
                "hide_empty": True,
                "no_add": True,
                "params": {"view": "all"},
                "columns": ["transaction_type", "stage", "status", "lead_banker", "target_close_date"],
            },
            {
                "key": "contacts",
                "label": "Company contacts",
                "module": "contacts",
                "link_field": "firm",
                "via": "company",
                "columns": ["full_name", "designation", "decision_level", "primary_contact", "work_email", "mobile"],
                "order": "-primary_contact,decision_level,full_name",
            },
            {
                "key": "history",
                "label": "Other outreach to this company",
                "module": "outreach",
                "link_field": "company",
                "via": "company",
                "exclude_self": True,
                "hide_empty": True,
                "params": {"view": "all"},
                "columns": ["transaction_type", "stage", "status", "lead_banker", "stage_entered"],
                "order": "-created_at",
            },
        ],
    )
)
