"""Default dropdown lists. Firms can add, rename or remove options in Settings.

Lists used by later modules (transaction types, reasons, document categories, ...)
are seeded by those modules' own migrations."""

from django.db import migrations

LISTS = {
    "sectors": ("Sectors", "", [
        "Technology / SaaS", "Fintech", "Financial services", "Consumer / D2C", "Retail",
        "Healthcare", "Pharma & life sciences", "Edtech", "Agritech & food", "Manufacturing",
        "Industrials", "Chemicals", "Auto & EV", "Energy & renewables", "Infrastructure",
        "Real estate", "Logistics", "Media & entertainment", "Telecom", "Other",
    ]),
    "investor_types": ("Investor types", "investors", [
        "PE", "VC", "Family office", "Strategic", "AIF", "NBFC / debt fund", "Other",
    ]),
    "investment_stages": ("Investment stages", "investors", [
        "Seed", "Early stage (Series A/B)", "Growth", "Late stage", "Pre-IPO", "Buyout", "Special situations",
    ]),
    "geographies": ("Geographies", "", [
        "India", "South Asia", "Southeast Asia", "Middle East", "US", "Europe", "UK", "Global",
    ]),
}


def seed(apps, schema_editor):
    ChoiceList = apps.get_model("core", "ChoiceList")
    ChoiceOption = apps.get_model("core", "ChoiceOption")
    for key, (label, module, options) in LISTS.items():
        cl, created = ChoiceList.objects.get_or_create(key=key, defaults={"label": label, "module": module})
        if created:
            ChoiceOption.objects.bulk_create(
                [ChoiceOption(choice_list=cl, label=o, order=i) for i, o in enumerate(options)]
            )


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
