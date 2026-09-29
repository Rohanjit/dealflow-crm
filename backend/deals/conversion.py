"""Mandates won in Outreach become deals, carrying what's known about the opportunity."""

from django.utils import timezone

from core.models import Note
from core.records import apply_values
from core.registry import get_module

from .models import Deal


def _money(obj, prefix: str) -> dict:
    return {
        "amount": getattr(obj, f"{prefix}_amount"),
        "currency": getattr(obj, f"{prefix}_currency") or "",
        "unit": getattr(obj, f"{prefix}_unit") or "",
    }


def deal_from_outreach(o, user) -> Deal | None:
    """Create the deal for a won outreach attempt, once. Returns None if it already has one
    (archived deals count: restore those from the Archive)."""
    if Deal.objects.filter(outreach=o).exists():
        return None
    values = {
        "company": ("companies", o.company),
        "transaction_type": o.transaction_type,
        "source": o.source,
        "lead_banker": o.lead_banker,
        "target_raise": _money(o, "expected_size"),
        "agreed_retainer": _money(o, "proposed_retainer"),
        "success_fee_pct": o.proposed_success_fee,
        "agreed_fee_terms": o.final_agreed_fees,
        "mandate_date": o.stage_entered or timezone.localdate(),
        "stage": "data_collection",
        "tags": [t.name for t in o.tags.all()],
    }
    deal = apply_values(Deal(outreach=o), get_module("deals"), values, user,
                        log_text="Created from outreach: mandate won")
    Note.objects.create(
        module="outreach", record_id=o.pk, kind="stage", date=timezone.localdate(),
        text="Moved to Active Deals", meta={"deal": deal.pk}, created_by=user,
    )
    return deal
