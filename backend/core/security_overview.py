"""Settings > Security overview (decision 63, S7): one place where an admin sees whether every
protection is in place and working — two-step login coverage, sign-ins and lock-outs, backups, the
scheduler, the audit log's chain, HTTPS, encryption and the virus scanner — as a checklist with the
fix for anything that isn't."""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils import timezone


def _check(key: str, ok: bool | None, label: str, fix: str = "") -> dict:
    # ok None = "doesn't apply here" (shown grey, not counted)
    return {"key": key, "ok": ok, "label": label, "fix": "" if ok else fix}


def two_step() -> dict:
    from accounts import mfa
    from accounts.models import Passkey, TotpDevice, User

    people = list(User.objects.filter(is_active=True).select_related("role"))
    with_key = set(Passkey.objects.values_list("user_id", flat=True))
    with_app = set(TotpDevice.objects.filter(confirmed=True).values_list("user_id", flat=True))
    exempt = [u for u in people if mfa.exempt(u)]
    counted = [u for u in people if u not in exempt]
    without = [u for u in counted if u.pk not in with_key and u.pk not in with_app]
    return {
        "people": len(counted),
        "enrolled": len(counted) - len(without),
        "passkey": sum(1 for u in counted if u.pk in with_key),
        "app_only": sum(1 for u in counted if u.pk in with_app and u.pk not in with_key),
        "single_method": sum(1 for u in counted if (u.pk in with_key) != (u.pk in with_app)),
        "without": [u.full_name or u.email for u in without][:20],
        "required": mfa.required(),
    }


def sign_ins() -> dict:
    from accounts.models import SignInFailure, UserSession

    now = timezone.now()
    live = Session.objects.filter(expire_date__gt=now).count()
    recent = UserSession.objects.filter(ended_at__isnull=True, last_seen__gte=now - dt.timedelta(hours=12))
    return {
        "open_sessions": live,
        "people_signed_in": recent.values("user_id").distinct().count(),
        "new_devices_14_days": UserSession.objects.filter(new_device=True, created_at__gte=now - dt.timedelta(days=14)).count(),
        "locked_now": SignInFailure.objects.filter(locked_until__gt=now).count(),
        "failures_recent": sum(SignInFailure.objects.filter(first_at__gte=now - dt.timedelta(hours=24)).values_list("count", flat=True)),
    }


def backups() -> dict:
    from core import backups as b
    from core.models import FirmSettings

    listed = b.list_backups()
    last = listed[0] if listed else None
    age = None
    if last:
        age = (timezone.now() - dt.datetime.fromisoformat(last["created_at"])).total_seconds() / 3600
    return {
        "last": last,
        "age_hours": round(age, 1) if age is not None else None,
        "encrypted": bool(last) and last["name"].endswith(".dfb"),
        "count": len(listed),
        "daily_hour": FirmSettings.load().backup_hour,
    }


def scheduler() -> dict:
    p = settings.DATA_DIR / "scheduler.alive"
    try:
        seen = dt.datetime.fromisoformat(p.read_text().strip())
    except (OSError, ValueError):
        return {"last_seen": None, "ok": False}
    return {"last_seen": seen.isoformat(), "ok": (timezone.now() - seen).total_seconds() < 300}


def https(request) -> dict:
    return {
        "domain": getattr(settings, "DOMAIN", "") or None,
        "https": bool(getattr(settings, "HTTPS", False)),
        "this_request_secure": request.is_secure(),
        "hsts_seconds": getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0,
        "local_only": request.get_host().split(":")[0] in ("localhost", "127.0.0.1", "[::1]"),
    }


def overview(request) -> dict:
    from core import antivirus, audit, restore_test
    from core.models import FirmSettings
    from dealflow import keys
    from vault import keyring

    mfa = two_step()
    signin = sign_ins()
    bk = backups()
    sched = scheduler()
    web = https(request)
    aud = audit.status()
    chain = audit.last_chain_check()
    scan = antivirus.status_info()
    store = keys.status()
    vault = keyring.summary()
    fs = FirmSettings.load()
    rt = restore_test.last()

    checks = [
        _check("two_step", mfa["enrolled"] == mfa["people"],
               f"Two-step login: {mfa['enrolled']} of {mfa['people']} people",
               "People without it can't do anything until they set it up at their next sign-in."),
        _check("two_ways", mfa["single_method"] == 0 if mfa["people"] else None,
               "Everyone has a second way in (passkey and app)" if mfa["single_method"] == 0
               else f"{mfa['single_method']} people have only one way in",
               "Ask them to add a passkey and an authenticator app in My preferences, so losing a phone doesn't lock them out."),
        _check("https", web["https"] or web["local_only"],
               "HTTPS with secure cookies and HSTS" if web["https"] else "Only reachable on this computer (no network access)",
               "Set DEALFLOW_DOMAIN and run the https profile (docs/https.md)."),
        _check("database", store.get("db_encrypted") if not store.get("postgres") else None,
               "Database encrypted" if store.get("db_encrypted") else "Database on Postgres: encrypt its disk",
               "See docs/postgres-encryption.md."),
        _check("keys_apart", store.get("separate"), "Keys kept apart from the data" if store.get("separate")
               else "Keys are inside the data folder",
               "Set DEALFLOW_KEYS_DIR to a separate volume or disk."),
        _check("vault_recovery", vault["has_recovery_key"] if vault["encrypted"] else None,
               "Vault recovery key made" if vault["has_recovery_key"] else "No Vault recovery key yet",
               "Make one below (Encryption and keys) and keep it offline."),
        _check("backups", bk["age_hours"] is not None and bk["age_hours"] <= (2 if fs.hourly_backups else 26) and bk["encrypted"],
               f"Last backup {bk['age_hours']} hours ago, encrypted" if bk["age_hours"] is not None else "No backups yet",
               "Check the scheduler is running, or take one in Settings > Backups."),
        _check("hourly", fs.hourly_backups, "Hourly backups: at most an hour of work can be lost" if fs.hourly_backups
               else "Daily backups only: up to a day of work could be lost",
               "Turn on hourly backups in Settings > Backups."),
        _check("restore_test", (rt or {}).get("ok") if rt else (False if bk["count"] else None),
               f"Latest backup test-restored on {rt['at'][:10]} ({rt.get('rows', 0)} rows, {rt.get('files', 0)} files)"
               if rt and rt.get("ok") else ("Backups haven't been test-restored yet" if not rt
                                             else "The last backup restore test failed"),
               "Test it now below; it also runs every week. If it fails, take a new backup and test again."),
        _check("scheduler", sched["ok"], "Scheduler running (backups, campaigns, housekeeping)" if sched["ok"]
               else "Scheduler isn't running: no daily backups, campaigns or housekeeping",
               "Start the scheduler container (docker compose up -d)."),
        _check("scanner", scan["available"] and not scan["signatures_stale"] if scan["mode"] != "off" else False,
               "Virus scanner working with current signatures" if scan["available"] and not scan["signatures_stale"]
               else "Virus scanning is off" if scan["mode"] == "off"
               else f"Virus signatures are out of date (from {scan['signatures_date']})" if scan["available"]
               else "Virus scanner isn't answering: uploads are paused",
               "The scanner updates itself: check it can reach the internet (database.clamav.net)." if scan["available"]
               else "Uploads are refused until the scanner answers; see Virus scanning below."),
        _check("audit_passphrase", aud["has_passphrase"], "Audit log sealed with your passphrase" if aud["has_passphrase"]
               else "Audit log still on a temporary key",
               "Set the audit passphrase in Settings > Audit log."),
        _check("audit_chain", (chain or {}).get("ok") if chain else None,
               "Audit log unbroken" if (chain or {}).get("ok") else
               ("Audit log not checked yet" if not chain else f"Audit log: {chain['problem_count']} problems found"),
               "Run the check now; if it still fails, fetch the log and compare with a backup."),
        _check("idle", fs.idle_minutes <= 60, f"Idle sign-out after {fs.idle_minutes} minutes",
               "Choose 60 minutes or less above."),
    ]
    counted = [c for c in checks if c["ok"] is not None]
    return {
        "score": {"ok": sum(1 for c in counted if c["ok"]), "total": len(counted)},
        "checks": checks,
        "two_step": mfa,
        "sign_ins": signin,
        "backups": bk,
        "scheduler": sched,
        "https": web,
        "audit": {**aud, "chain": chain},
        "restore_test": rt,
    }


def admin_alerts() -> list[dict]:
    """Security events admins hear about in the notification bell. Each key changes when something
    new happens, so a cleared alert comes back only for a new event (decision 60)."""
    import hashlib

    from accounts.models import SignInFailure
    from core import antivirus, audit, restore_test
    from core.models import FirmSettings

    out = []
    now = timezone.now()
    locks = SignInFailure.objects.filter(locked_until__gt=now)
    if locks.exists():
        n = locks.count()
        newest = locks.order_by("-id").first()
        out.append({"key": f"sec:lockout:{newest.pk}", "title": f"{n} sign-in lock-out{'s' if n > 1 else ''} in force",
                    "detail": "Five wrong passwords or codes locked an account or an address for 30 minutes."})
    fs = FirmSettings.load()
    if fs.last_backup_error:
        h = hashlib.sha256(fs.last_backup_error.encode()).hexdigest()[:12]
        out.append({"key": f"sec:backupfail:{h}", "title": "The last automatic backup failed",
                    "detail": fs.last_backup_error[:200]})
    if fs.backups_enabled and fs.setup_completed_at and not fs.sample_loaded_at:
        bk = backups()
        if bk["age_hours"] is None or bk["age_hours"] > 26:
            out.append({"key": f"sec:nobackup:{now.date()}", "title": "No backup in the last day",
                        "detail": "Check that the scheduler is running."})
    rt = restore_test.last()
    if rt and not rt.get("ok"):
        out.append({"key": f"sec:restoretest:{rt['at']}", "title": "The backup restore test failed",
                    "detail": (rt.get("problems") or [""])[0][:200]})
    chain = audit.last_chain_check()
    if chain and not chain.get("ok"):
        out.append({"key": f"sec:chain:{chain['checked_at']}", "title": "The audit log was changed outside Dealflow",
                    "detail": f"{chain['problem_count']} problems found in the chain."})
    if antivirus.mode() != "off" and not antivirus.uploads_allowed():
        out.append({"key": f"sec:scanner:{now.date()}", "title": "The virus scanner isn't answering",
                    "detail": "Uploads are paused until it's back."})
    return out
