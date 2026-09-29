"""Mandates won in Outreach before Active Deals existed become deals (at Data collection),
exactly as a mandate won from now on does."""

from decimal import Decimal

from django.db import migrations
from django.utils import timezone

MULTIPLIER = {"": 1, "K": 10**3, "Lakh": 10**5, "Mn": 10**6, "Cr": 10**7, "Bn": 10**9}


def _money(o, prefix, target):
    amount = getattr(o, f"{prefix}_amount")
    unit = getattr(o, f"{prefix}_unit") or ""
    return {
        f"{target}_amount": amount,
        f"{target}_currency": getattr(o, f"{prefix}_currency") or "",
        f"{target}_unit": unit,
        f"{target}_value": None if amount is None else Decimal(amount) * MULTIPLIER.get(unit, 1),
    }


def convert(apps, schema_editor):
    Outreach = apps.get_model("outreach", "Outreach")
    Deal = apps.get_model("deals", "Deal")
    Note = apps.get_model("core", "Note")
    today = timezone.localdate()
    won = Outreach.objects.filter(stage="won", status="open", archived_at__isnull=True, deal__isnull=True)
    for o in won.order_by("pk"):
        who = o.updated_by_id or o.created_by_id
        deal = Deal.objects.create(
            company_id=o.company_id,
            outreach=o,
            transaction_type_id=o.transaction_type_id,
            source_id=o.source_id,
            lead_banker_id=o.lead_banker_id,
            success_fee_pct=o.proposed_success_fee,
            agreed_fee_terms=o.final_agreed_fees,
            mandate_date=o.stage_entered or today,
            stage="data_collection",
            status="open",
            stage_entered=today,
            created_by_id=who,
            updated_by_id=who,
            **_money(o, "expected_size", "target_raise"),
            **_money(o, "proposed_retainer", "agreed_retainer"),
        )
        deal.tags.set(o.tags.all())
        Note.objects.create(module="deals", record_id=deal.pk, kind="stage", date=today, created_by_id=who,
                            text="Created from outreach: mandate won",
                            meta={"to_stage": "data_collection", "to_status": "open"})
        Note.objects.create(module="outreach", record_id=o.pk, kind="stage", date=today, created_by_id=who,
                            text="Moved to Active Deals", meta={"deal": deal.pk})


class Migration(migrations.Migration):
    dependencies = [
        ("deals", "0002_seed_lists"),
        ("outreach", "0003_company_geography"),
        ("core", "0004_firmsettings_dashboard_days"),
    ]
    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]
