"""Default dropdown list for Active Deals. Firms can add, rename or remove options in Settings."""

from django.db import migrations

LISTS = {
    "decline_reasons": ("Decline reasons", [
        "Valuation mismatch", "Sector not in focus", "Ticket size mismatch", "Stage not in focus",
        "Fund fully deployed", "Business or team concerns", "Timing", "Competing investment", "Other",
    ]),
}


def seed(apps, schema_editor):
    ChoiceList = apps.get_model("core", "ChoiceList")
    ChoiceOption = apps.get_model("core", "ChoiceOption")
    for key, (label, options) in LISTS.items():
        cl, created = ChoiceList.objects.get_or_create(key=key, defaults={"label": label, "module": "deals"})
        if created:
            ChoiceOption.objects.bulk_create(
                [ChoiceOption(choice_list=cl, label=o, order=i) for i, o in enumerate(options)]
            )


class Migration(migrations.Migration):
    dependencies = [("deals", "0001_initial"), ("core", "0002_seed_choice_lists")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
