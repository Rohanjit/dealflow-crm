"""Fields switched off in the old Settings > Fields now live in each module's layout (decision 57).
A customisable module with fields switched off becomes "customised", so nothing changes on screen."""

from django.db import migrations

CUSTOMISABLE = ("investors", "contacts", "companies", "outreach", "deals", "deal_investors")


def forwards(apps, schema_editor):
    FieldSwitch = apps.get_model("core", "FieldSwitch")
    ModuleLayout = apps.get_model("core", "ModuleLayout")
    for sw in FieldSwitch.objects.filter(enabled=False):
        lay, _ = ModuleLayout.objects.get_or_create(module=sw.module)
        if sw.field_key not in lay.hidden:
            lay.hidden = [*lay.hidden, sw.field_key]
        if sw.module in CUSTOMISABLE:
            lay.customised = True
        lay.save()
    FieldSwitch.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0007_firmsettings_layout_token_customfield_modulelayout")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
