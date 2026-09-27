from django.core.management.base import BaseCommand

from core.backups import create_backup


class Command(BaseCommand):
    help = "Take a backup now (database + uploaded files) into the backups folder."

    def add_arguments(self, parser):
        parser.add_argument("--kind", default="manual", choices=["manual", "daily"])

    def handle(self, *args, **opts):
        p = create_backup(opts["kind"])
        self.stdout.write(self.style.SUCCESS(f"Backup written to {p}"))
