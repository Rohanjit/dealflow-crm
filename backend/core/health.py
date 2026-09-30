"""GET /healthz: is Dealflow able to serve? Used by the container health checks and monitoring.

Reports the database, free disk space in the data folder, the scheduler's heartbeat and when the
last backup was taken. Nothing identifying: no firm name, versions, paths, counts or errors."""

from __future__ import annotations

import shutil
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

MIN_FREE_MB = 200  # below this, uploads and backups start failing, so report unhealthy
HEARTBEAT_FILE = "scheduler.heartbeat"
HEARTBEAT_STALE_SECONDS = 300


def heartbeat_path():
    return settings.DATA_DIR / HEARTBEAT_FILE


def beat():
    """Called by the scheduler on every pass."""
    heartbeat_path().write_text(str(int(time.time())))


def _database_ok() -> bool:
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
            return c.fetchone() == (1,)
    except Exception:
        return False


def _scheduler() -> str:
    try:
        age = time.time() - heartbeat_path().stat().st_mtime
    except OSError:
        return "unknown"
    return "ok" if age < HEARTBEAT_STALE_SECONDS else "stale"


@require_GET
def healthz(request):
    from core.models import FirmSettings

    db = _database_ok()
    free_mb = shutil.disk_usage(settings.DATA_DIR).free // (1024 * 1024)
    last_backup = None
    if db:
        try:
            fs = FirmSettings.load()
            last_backup = fs.last_backup_at.isoformat() if fs.last_backup_at else None
        except Exception:
            db = False
    healthy = db and free_mb >= MIN_FREE_MB
    body = {
        "status": "ok" if healthy else "unhealthy",
        "database": "ok" if db else "unavailable",
        "disk_free_mb": free_mb,
        "disk": "ok" if free_mb >= MIN_FREE_MB else "low",
        "scheduler": _scheduler(),
        "last_backup_at": last_backup,
    }
    response = JsonResponse(body, status=200 if healthy else 503)
    response["Cache-Control"] = "no-store"
    return response
