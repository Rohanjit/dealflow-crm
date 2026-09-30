"""Default document categories. Firms can add, rename or remove options in Settings."""

from django.db import migrations

CATEGORIES = [
    "NDA", "Engagement letter", "Teaser", "IM", "Financial model", "Term sheet", "DD report",
    "Definitive agreement", "Other",
]


def seed(apps, schema_editor):
    ChoiceList = apps.get_model("core", "ChoiceList")
    ChoiceOption = apps.get_model("core", "ChoiceOption")
    cl, created = ChoiceList.objects.get_or_create(
        key="document_categories", defaults={"label": "Document categories", "module": "vault"}
    )
    if created:
        ChoiceOption.objects.bulk_create([ChoiceOption(choice_list=cl, label=c, order=i) for i, c in enumerate(CATEGORIES)])


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial"), ("core", "0002_seed_choice_lists")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
