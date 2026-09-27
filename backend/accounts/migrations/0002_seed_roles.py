"""Built-in Admin role, plus an editable starter 'Member' role."""

from django.db import migrations

MODULES = ["investors", "contacts", "outreach", "deals", "tasks", "vault"]


def seed(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Perm = apps.get_model("accounts", "RoleModulePermission")
    Role.objects.get_or_create(name="Admin", defaults={"is_admin": True})
    member, created = Role.objects.get_or_create(name="Member")
    if created:
        Perm.objects.bulk_create(
            [Perm(role=member, module=m, can_view=True, can_create=True, can_edit=True, can_delete=False)
             for m in MODULES]
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
