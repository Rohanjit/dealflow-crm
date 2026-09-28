"""Default decision-maker levels. Firms can add, rename or remove them in Settings."""

from django.db import migrations

LEVELS = ["Partner / MD", "Principal / VP", "Associate / Analyst", "Other"]


def seed(apps, schema_editor):
    ChoiceList = apps.get_model("core", "ChoiceList")
    ChoiceOption = apps.get_model("core", "ChoiceOption")
    cl, created = ChoiceList.objects.get_or_create(
        key="decision_maker_levels", defaults={"label": "Decision-maker levels", "module": "contacts"}
    )
    if created:
        ChoiceOption.objects.bulk_create([ChoiceOption(choice_list=cl, label=o, order=i) for i, o in enumerate(LEVELS)])


class Migration(migrations.Migration):
    dependencies = [("contacts", "0001_initial"), ("core", "0002_seed_choice_lists")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
