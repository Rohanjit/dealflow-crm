"""Investor suggestions for a deal: simple, rules-based, using what's already recorded.

Each factor scores full marks when it fits, nothing when it clearly doesn't, and a little when
either side hasn't been filled in (so missing data doesn't bury an investor). No learning from
past outcomes in V1."""

from __future__ import annotations

from decimal import Decimal

from core.registry import CURRENCIES
from investors.models import Investor

from .models import Deal, DealInvestor

# (factor, full marks, marks when unknown)
FACTORS = [
    ("sector", 25, 10),
    ("ticket", 20, 8),
    ("stage", 15, 6),
    ("geography", 15, 6),
    ("instrument", 15, 6),
    ("deploying", 10, 5),
]
WEIGHT = {k: (full, unknown) for k, full, unknown in FACTORS}
EQUITY_LIKE = {"equity", "ccps", "ccd"}  # compulsorily convertible instruments sit with equity investors
SYMBOL = dict(CURRENCIES)


def money_text(amount, currency: str, unit: str) -> str:
    if amount is None:
        return ""
    s = format(Decimal(amount).normalize(), "f")
    return " ".join(p for p in (SYMBOL.get(currency, currency), s, unit) if p)


def deal_kind(deal: Deal) -> str | None:
    """equity | debt | both, from the instrument, else from the transaction type's name."""
    if deal.instrument in EQUITY_LIKE:
        return "equity"
    if deal.instrument == "debt":
        return "debt"
    if deal.instrument == "mix":
        return "both"
    label = deal.transaction_type.label.lower() if deal.transaction_type_id else ""
    if "debt" in label:
        return "debt"
    if "equity" in label or "ipo" in label:
        return "equity"
    return None


def _reason(factor: str, fit: str, text: str) -> dict:
    full, unknown = WEIGHT[factor]
    points = {"yes": full, "partial": full // 2, "unknown": unknown, "no": 0}[fit]
    return {"factor": factor, "fit": fit, "text": text, "points": points}


def _labels(options) -> str:
    names = [o.label for o in options]
    return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")


def facts(deal: Deal) -> dict:
    c = deal.company
    return {
        "sector": c.sector.label if c.sector_id else None,
        "stage": deal.investment_stage.label if deal.investment_stage_id else None,
        "geography": c.geography.label if c.geography_id else None,
        "raise": money_text(deal.target_raise_amount, deal.target_raise_currency, deal.target_raise_unit) or None,
        "kind": deal_kind(deal),
    }


def score(deal: Deal, inv: Investor) -> tuple[int, list[dict]]:
    c = deal.company
    reasons = []

    # Sector
    sectors = list(inv.sectors.all())
    if not c.sector_id:
        reasons.append(_reason("sector", "unknown", "Company sector not set"))
    elif not sectors:
        reasons.append(_reason("sector", "unknown", "No sector focus recorded"))
    elif any(o.pk == c.sector_id for o in sectors):
        reasons.append(_reason("sector", "yes", f"Invests in {c.sector.label}"))
    else:
        reasons.append(_reason("sector", "no", f"Sectors: {_labels(sectors)}"))

    # Ticket size vs the raise (same currency only; there's no conversion)
    reasons.append(_ticket(deal, inv))

    # Stage
    stages = list(inv.stages.all())
    if not deal.investment_stage_id:
        reasons.append(_reason("stage", "unknown", "Company stage not set"))
    elif not stages:
        reasons.append(_reason("stage", "unknown", "No stage focus recorded"))
    elif any(o.pk == deal.investment_stage_id for o in stages):
        reasons.append(_reason("stage", "yes", f"Invests at {deal.investment_stage.label}"))
    else:
        reasons.append(_reason("stage", "no", f"Stages: {_labels(stages)}"))

    # Geography ("Global" covers everywhere)
    geos = list(inv.geography_focus.all())
    if not c.geography_id:
        reasons.append(_reason("geography", "unknown", "Company geography not set"))
    elif not geos:
        reasons.append(_reason("geography", "unknown", "No geography focus recorded"))
    elif any(o.pk == c.geography_id for o in geos):
        reasons.append(_reason("geography", "yes", f"Covers {c.geography.label}"))
    elif any(o.label.strip().lower() == "global" for o in geos):
        reasons.append(_reason("geography", "yes", "Invests globally"))
    else:
        reasons.append(_reason("geography", "no", f"Focus: {_labels(geos)}"))

    # Equity / debt
    kind = deal_kind(deal)
    if not kind:
        reasons.append(_reason("instrument", "unknown", "Deal instrument not set"))
    elif not inv.instrument:
        reasons.append(_reason("instrument", "unknown", "Equity / debt preference not recorded"))
    elif inv.instrument == "both" or kind == "both" or inv.instrument == kind:
        what = {"equity": "Equity", "debt": "Debt", "both": "Equity and debt"}[inv.instrument]
        reasons.append(_reason("instrument", "yes", f"{what} investor"))
    else:
        reasons.append(_reason("instrument", "no", f"{inv.get_instrument_display()} only"))

    # Actively deploying
    if inv.actively_deploying == "yes":
        reasons.append(_reason("deploying", "yes", "Actively deploying"))
    elif inv.actively_deploying == "no":
        reasons.append(_reason("deploying", "no", "Not actively deploying"))
    else:
        reasons.append(_reason("deploying", "unknown", "Not known if deploying"))

    return sum(r["points"] for r in reasons), reasons


def _ticket(deal: Deal, inv: Investor) -> dict:
    raise_value, cur = deal.target_raise_value, deal.target_raise_currency
    lo_set, hi_set = inv.min_ticket_value is not None, inv.max_ticket_value is not None
    if raise_value is None:
        return _reason("ticket", "unknown", "Target raise not set")
    if not lo_set and not hi_set:
        return _reason("ticket", "unknown", "No ticket size recorded")
    lo = inv.min_ticket_value if lo_set and inv.min_ticket_currency == cur else None
    hi = inv.max_ticket_value if hi_set and inv.max_ticket_currency == cur else None
    if lo is None and hi is None:
        other = inv.min_ticket_currency if lo_set else inv.max_ticket_currency
        return _reason("ticket", "unknown", f"Tickets in {other}, raise in {cur}")
    lo_text = money_text(inv.min_ticket_amount, inv.min_ticket_currency, inv.min_ticket_unit)
    hi_text = money_text(inv.max_ticket_amount, inv.max_ticket_currency, inv.max_ticket_unit)
    if lo is not None and lo > raise_value:
        return _reason("ticket", "no", f"Minimum ticket {lo_text} is above the raise")
    if hi is not None and hi < raise_value / 10:
        return _reason("ticket", "partial", f"Tickets up to {hi_text}: a small part of the raise")
    span = f"{lo_text} – {hi_text}" if lo is not None and hi is not None else (f"from {lo_text}" if lo is not None else f"up to {hi_text}")
    return _reason("ticket", "yes", f"Tickets {span}")


def suggestions(deal: Deal, limit: int = 50, q: str = "") -> list[dict]:
    """Investors ranked by fit, leaving out archived ones and those already on the tracker."""
    tracked = DealInvestor.objects.filter(deal=deal, archived_at__isnull=True).values("investor_id")
    investors = (
        Investor.objects.filter(archived_at__isnull=True)
        .exclude(pk__in=tracked)
        .select_related("investor_type")
        .prefetch_related("sectors", "stages", "geography_focus")
    )
    if q:
        investors = investors.filter(firm_name__icontains=q)
    ranked = []
    for inv in investors:
        points, reasons = score(deal, inv)
        ranked.append((points, inv, reasons))
    ranked.sort(key=lambda r: (-r[0], r[1].firm_name.lower()))
    out = []
    for points, inv, reasons in ranked[:limit]:
        out.append({
            "investor": {
                "module": "investors",
                "id": inv.pk,
                "title": inv.firm_name,
                "subtitle": " · ".join(p for p in (inv.investor_type.label if inv.investor_type_id else "", inv.hq_city) if p),
                "archived": False,
            },
            "score": points,
            "reasons": reasons,
        })
    return out
