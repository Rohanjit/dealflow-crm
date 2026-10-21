"""Long-running loop for the `scheduler` container: takes the daily backup.

Later modules (task reminders) will hang their periodic jobs here too."""

import logging
import time

from django.utils import timezone

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core import guest
from core.backups import BackupError, create_backup, due_for_daily_backup

log = logging.getLogger("dealflow.scheduler")


class Command(BaseCommand):
    help = "Run scheduled jobs (daily backups) forever."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=60, help="Seconds between checks")
        parser.add_argument("--once", action="store_true", help="Check once and exit")

    def handle(self, *args, **opts):
        log.info("Scheduler started")
        from core import audit

        def system(action: str):
            audit.append({"u": {"id": None, "name": "Dealflow (scheduler)", "email": ""}, "a": action})

        housekept = None
        while True:
            close_old_connections()
            try:
                if guest.demo_reload_due():
                    guest.reload_demo()
                    log.info("Demo sample data reloaded")
                    system("Reloaded the demo sample")
            except Exception:
                log.exception("Reloading the demo sample failed")
            try:
                if due_for_daily_backup():
                    p = create_backup("daily")
                    log.info("Daily backup written to %s", p)
                    system(f"Took the daily backup ({p.name})")
            except BackupError as e:
                log.warning("Backup skipped: %s", e)
            except Exception:
                log.exception("Daily backup failed")
            try:
                from mail import campaigns as mail_campaigns
                from mail import service as mail_service

                if n := mail_campaigns.tick():
                    log.info("Campaigns: sent %s emails", n)
                mail_service.recheck_pending()
            except Exception:
                log.exception("Sending campaign emails failed")
            try:
                today = timezone.localdate()
                if housekept != today:
                    try:
                        from mail import service as mail_service

                        mail_service.remove_old_uploads()
                    except Exception:
                        log.exception("Removing old email attachments failed")
                    for done in audit.housekeeping(today):
                        system(done)
                    housekept = today
            except Exception:
                log.exception("Audit log housekeeping failed")
            if opts["once"]:
                return
            time.sleep(opts["interval"])
