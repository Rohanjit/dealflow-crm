import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.db.models import Q

from core import stale
from core.registry import UNIT_MULTIPLIER, FieldDef, ModuleDef, register, this_year

from .models import INSTRUMENT_CHOICES, STRENGTH_CHOICES, Investor

B, P, F, T, R, O = "Basics", "Investment preferences", "Firm profile", "Track record", "Relationship", "Other"

FIELDS = [
    FieldDef("firm_name", "Firm name", "text", B, required=True, list_default=True, searchable=True,
             aliases=["name", "investor", "investor name", "fund", "fund name", "firm", "company"]),
    FieldDef("investor_type", "Investor type", "dropdown", B, choice_list="investor_types",
             list_default=True, filterable=True,
             aliases=["type", "category"]),
    FieldDef("website", "Website", "url", B, searchable=True,
             aliases=["url", "web", "site"]),
    FieldDef("hq_city", "HQ city", "text", B, list_default=True, searchable=True,
             aliases=["city", "hq", "headquarters", "location", "base"]),
    FieldDef("country", "Country", "text", B, searchable=True, filterable=True),
    FieldDef("description", "Short description", "longtext", B, searchable=True),

    FieldDef("sectors", "Sectors", "dropdown", P, choice_list="sectors", multi=True,
             list_default=True, filterable=True,
             aliases=["sector", "sector focus", "industries", "industry"]),
    FieldDef("stages", "Stages", "dropdown", P, choice_list="investment_stages", multi=True,
             filterable=True,
             aliases=["stage", "stage focus", "investment stage"]),
    FieldDef("min_ticket", "Min ticket size", "money", P, list_default=True,
             aliases=["min ticket", "ticket min", "minimum ticket", "min cheque", "min check"]),
    FieldDef("max_ticket", "Max ticket size", "money", P, list_default=True,
             aliases=["max ticket", "ticket max", "maximum ticket", "max cheque", "max check"]),
    FieldDef("geography_focus", "Geography focus", "dropdown", P, choice_list="geographies",
             multi=True, filterable=True,
             aliases=["geography", "geographies", "region", "regions"]),
    FieldDef("instrument", "Equity / debt", "choice", P, choices=INSTRUMENT_CHOICES, filterable=True,
             aliases=["instrument", "equity or debt", "equity/debt"]),

    FieldDef("fund_size", "Fund size / AUM", "money", F,
             aliases=["aum", "fund size", "corpus"]),
    FieldDef("fund_vintage", "Current fund vintage (year)", "integer", F, min_value=1900, max_value=this_year(3),
             aliases=["vintage", "fund vintage"]),
    FieldDef("actively_deploying", "Actively deploying", "tristate", F, filterable=True,
             aliases=["active", "deploying"]),

    FieldDef("portfolio_companies", "Notable portfolio companies", "longtext", T, searchable=True,
             aliases=["portfolio", "portfolio companies"]),
    FieldDef("past_deals", "Past deals", "longtext", T, searchable=True),

    FieldDef("relationship_owner", "Relationship owner", "user", R, list_default=True, filterable=True,
             aliases=["owner", "rm", "relationship manager", "coverage"]),
    FieldDef("relationship_strength", "Relationship strength", "choice", R, choices=STRENGTH_CHOICES,
             list_default=True, filterable=True,
             aliases=["strength", "relationship"]),
    FieldDef("last_contacted", "Last contacted", "date", R, list_default=True, filterable=True,
             aliases=["last contact", "last contacted on", "last touch"]),

    FieldDef("tags", "Tags", "tags", O, filterable=True),
    FieldDef("notes", "Notes", "longtext", O, searchable=True,
             aliases=["remarks", "comments"]),
]


# Words that don't tell two firms apart ("Blume" and "Blume Ventures" are the same firm).
_GENERIC = {
    "ventures", "venture", "capital", "partners", "partner", "advisors", "advisers", "advisory", "fund", "funds",
    "llp", "ltd", "limited", "pvt", "private", "inc", "llc", "management", "investments", "investment",
    "group", "holdings", "india", "the", "co", "company", "asset", "assets", "alternate", "alternative",
}


def _norm_name(name: str) -> str:
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


def duplicate_keys(values: dict) -> list[str]:
    """Keys that identify the same firm: normalised name and website domain."""
    keys = []
    if n := _norm_name(values.get("firm_name", "")):
        keys.append(f"name:{n}")
    if d := _domain(values.get("website", "")):
        keys.append(f"web:{d}")
    return keys


def ticket_filter(qs, params, user=None):
    """?ticket=50&ticket_currency=INR&ticket_unit=Cr → investors whose ticket range covers it.
    Only compares within the same currency (no conversion). Units are scaled."""
    raw = params.get("ticket")
    currency = params.get("ticket_currency")
    if not raw or not currency:
        return qs
    try:
        value = Decimal(raw) * UNIT_MULTIPLIER.get(params.get("ticket_unit", ""), Decimal(1))
    except (InvalidOperation, TypeError):
        return qs
    min_ok = Q(min_ticket_value__isnull=True) | Q(min_ticket_currency=currency, min_ticket_value__lte=value)
    max_ok = Q(max_ticket_value__isnull=True) | Q(max_ticket_currency=currency, max_ticket_value__gte=value)
    has_any = Q(min_ticket_value__isnull=False) | Q(max_ticket_value__isnull=False)
    return qs.filter(min_ok & max_ok & has_any)


def subtitle(inv: Investor) -> str:
    parts = [inv.investor_type.label if inv.investor_type_id else "", inv.hq_city]
    return " · ".join(p for p in parts if p)


MODULE = register(
    ModuleDef(
        key="investors",
        label="Investors",
        singular="Investor",
        model=Investor,
        fields=FIELDS,
        groups=[B, P, F, T, R, O],
        title_field="firm_name",
        subtitle=subtitle,
        subtitle_fields=["investor_type", "hq_city"],
        duplicate_keys=duplicate_keys,
        timeline=True,
        extra_filters=stale.make_filter("investors", ticket_filter),
        quick_filters=[stale.QUICK_FILTER],
        name_key=_norm_name,
        sections=[
            {
                "key": "contacts",
                "label": "Linked contacts",
                "module": "contacts",
                "link_field": "firm",
                "columns": ["full_name", "designation", "decision_level", "primary_contact", "work_email", "mobile"],
                "order": "-primary_contact,decision_level,full_name",
            },
            {"key": "tasks", "label": "Tasks", "module": "tasks", "link_field": "related", "panel": "tasks",
             "order": "status,due"},
            {"key": "documents", "label": "Documents", "module": "vault", "panel": "documents"},
            {
                "key": "deal_history",
                "label": "Deal history",
                "module": "deal_investors",
                "link_field": "investor",
                "add_label": "Add to a deal",
                "columns": ["deal", "stage", "status", "stage_entered", "decline_reason"],
                "order": "-created_at",
            },
        ],
    )
)
