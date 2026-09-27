"""Long-running loop for the `scheduler` container: takes the daily backup.

Later modules (task reminders) will hang their periodic jobs here too."""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.backups import BackupError, create_backup, due_for_daily_backup

log = logging.getLogger("dealflow.scheduler")


class Command(BaseCommand):
    help = "Run scheduled jobs (daily backups) forever."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=60, help="Seconds between checks")
        parser.add_argument("--once", action="store_true", help="Check once and exit")

    def handle(self, *args, **opts):
        log.info("Scheduler started")
        while True:
            close_old_connections()
            try:
                if due_for_daily_backup():
                    p = create_backup("daily")
                    log.info("Daily backup written to %s", p)
            except BackupError as e:
                log.warning("Backup skipped: %s", e)
            except Exception:
                log.exception("Daily backup failed")
            if opts["once"]:
                return
            time.sleep(opts["interval"])
