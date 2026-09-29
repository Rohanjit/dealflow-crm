"""Default dropdown lists for Outreach. Firms can add, rename or remove options in Settings."""

from django.db import migrations

LISTS = {
    "transaction_types": ("Transaction types", [
        "Equity raise", "Debt raise", "M&A sell-side", "M&A buy-side", "Pre-IPO", "Other",
    ]),
    "outreach_sources": ("Outreach sources", [
        "Referral", "Inbound", "Cold outreach", "Event", "Existing relationship",
    ]),
    "lost_reasons": ("Lost reasons", [
        "Chose another banker", "Fees not agreed", "Company dropped the plan", "Company not ready",
        "No response", "Other",
    ]),
    "hold_reasons": ("On-hold reasons", [
        "Company not ready", "Awaiting financials", "Market conditions", "Management bandwidth", "Other",
    ]),
}


def seed(apps, schema_editor):
    ChoiceList = apps.get_model("core", "ChoiceList")
    ChoiceOption = apps.get_model("core", "ChoiceOption")
    for key, (label, options) in LISTS.items():
        cl, created = ChoiceList.objects.get_or_create(key=key, defaults={"label": label, "module": "outreach"})
        if created:
            ChoiceOption.objects.bulk_create(
                [ChoiceOption(choice_list=cl, label=o, order=i) for i, o in enumerate(options)]
            )


class Migration(migrations.Migration):
    dependencies = [("outreach", "0001_initial"), ("core", "0002_seed_choice_lists")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
