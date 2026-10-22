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


def backups_exist() -> bool:
    from core.backups import list_backups

    return bool(list_backups())


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
                from core.backups import due_for_hourly_backup

                if due_for_hourly_backup():
                    p = create_backup("hourly")
                    log.info("Hourly backup written to %s", p)
            except BackupError as e:
                log.warning("Hourly backup skipped: %s", e)
            except Exception:
                log.exception("Hourly backup failed")
            try:
                from core import restore_test

                # Weekly proof that backups restore (into a separate, temporary database).
                if restore_test.due() and backups_exist():
                    r = restore_test.run()
                    if r["ok"]:
                        system(f"Backup restore test passed ({r['backup']}, {r.get('rows', 0)} rows)")
                    else:
                        log.error("Backup restore test failed: %s", r["problems"])
                        system(f"Backup restore test FAILED ({r.get('backup')})")
            except Exception:
                log.exception("Backup restore test failed to run")
            try:
                from mail import campaigns as mail_campaigns
                from mail import service as mail_service

                if n := mail_campaigns.tick():
                    log.info("Campaigns: sent %s emails", n)
                mail_service.recheck_pending()
            except Exception:
                log.exception("Sending campaign emails failed")
            try:
                from sync import engine as sync_engine

                sync_engine.tick()
            except Exception:
                log.exception("Reading connected mailboxes and calendars failed")
            try:
                today = timezone.localdate()
                if housekept != today:
                    try:
                        from mail import service as mail_service

                        mail_service.remove_old_uploads()
                    except Exception:
                        log.exception("Removing old email attachments failed")
                    try:
                        from django.contrib.sessions.models import Session

                        from accounts import lockout

                        lockout.tidy()
                        Session.objects.filter(expire_date__lt=timezone.now()).delete()
                    except Exception:
                        log.exception("Tidying sign-in records failed")
                    for done in audit.housekeeping(today):
                        system(done)
                    try:
                        chain = audit.verify_chain()
                        if not chain["ok"]:
                            log.error("Audit log chain check found %s problems", chain["problem_count"])
                            system(f"Audit log check found {chain['problem_count']} problems")
                    except Exception:
                        log.exception("Checking the audit log chain failed")
                    housekept = today
            except Exception:
                log.exception("Audit log housekeeping failed")
            try:
                # Heartbeat for the container's health check and Settings > Security.
                from django.conf import settings

                (settings.DATA_DIR / "scheduler.alive").write_text(timezone.now().isoformat())
            except OSError:
                log.exception("Couldn't write the scheduler heartbeat")
            if opts["once"]:
                return
            time.sleep(opts["interval"])
