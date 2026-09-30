"""Security hardening, phase 1: password history, sign-in lock-out, and invite links stored as hashes.

Existing invite links keep working: each stored token is replaced by its SHA-256, which is what the
app now looks up. There's no way back to the raw tokens, so reversing leaves the hashes in place."""

import hashlib

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def hash_existing_tokens(apps, schema_editor):
    Invite = apps.get_model("accounts", "Invite")
    for invite in Invite.objects.all():
        if len(invite.token_hash) != 64:  # raw tokens are 43 characters; hashes are 64 hex digits
            invite.token_hash = hashlib.sha256(invite.token_hash.encode()).hexdigest()
            invite.save(update_fields=["token_hash"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_vault_locked_until_user_vault_pin_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PasswordHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("password", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="password_history",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="SignInLock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.CharField(max_length=254, unique=True)),
                ("failures", models.PositiveSmallIntegerField(default=0)),
                ("last_failure_at", models.DateTimeField(blank=True, null=True)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RenameField(model_name="invite", old_name="token", new_name="token_hash"),
        migrations.AlterField(
            model_name="invite",
            name="token_hash",
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
        migrations.RunPython(hash_existing_tokens, migrations.RunPython.noop),
    ]
