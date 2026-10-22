"""Internal: load a backup's data into the (temporary) database this process was started with and
print the number of rows of each kind as JSON. Run by core/restore_test.py, never on live data."""

import json

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Internal (backup restore test): load data into an empty temporary database and count rows."

    def add_arguments(self, parser):
        parser.add_argument("data")

    def handle(self, *args, **opts):
        db = settings.DATABASES["default"]
        if db["ENGINE"] != "django.db.backends.sqlite3" or "dealflow-restore-test-" not in str(db["NAME"]):
            raise CommandError("restore_check only runs against the restore test's temporary database.")
        call_command("migrate", verbosity=0, interactive=False)
        call_command("flush", verbosity=0, interactive=False)  # migrations seed starter rows
        call_command("loaddata", opts["data"], verbosity=0)
        # The same manager dumpdata reads from, so the counts compare like for like.
        counts = {m._meta.label_lower: m._default_manager.count() for m in apps.get_models()}
        self.stdout.write(json.dumps({k: v for k, v in counts.items() if v}))
