"""Internal: load a backup's records into the (temporary) database this process was started with and
print the number of rows of each kind as JSON. Run by core/restore_test.py, never on live data. The
records arrive on standard input ("-") or from a file, and are loaded the way a restore loads them."""

import io
import json
import sys

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Internal (backup restore test): load data into an empty temporary database and count rows."

    def add_arguments(self, parser):
        parser.add_argument("data", help='a file, or "-" for standard input')
        parser.add_argument("--format", default=None, choices=["json", "jsonl"])

    def handle(self, *args, **opts):
        from core import backups

        db = settings.DATABASES["default"]
        if db["ENGINE"] not in ("django.db.backends.sqlite3", "dealflow.db_cipher") or "dealflow-restore-test-" not in str(db["NAME"]):
            raise CommandError("restore_check only runs against the restore test's temporary database.")
        call_command("migrate", verbosity=0, interactive=False)
        call_command("flush", verbosity=0, interactive=False)  # migrations seed starter rows
        fmt = opts["format"] or ("jsonl" if opts["data"].endswith(".jsonl") else "json")
        with transaction.atomic():
            if opts["data"] == "-":
                backups.load_data(fmt, io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
            else:
                with open(opts["data"], encoding="utf-8") as f:
                    backups.load_data(fmt, f)
        # The same manager dumpdata reads from, so the counts compare like for like.
        counts = {m._meta.label_lower: m._default_manager.count() for m in apps.get_models()}
        self.stdout.write(json.dumps({k: v for k, v in counts.items() if v}))
