"""Backups are proven to restore (decision 63, S7). Weekly (and on demand from Settings > Security),
the newest backup is decrypted in memory-backed temporary space, every file in it is read back
(zip checksums), and its data is loaded into a fresh, separate database by a separate process; the
number of rows of every kind must match what the backup holds. The live data is never touched.
The result is kept in DATA_DIR/restore-test.json."""

from __future__ import annotations

import json
import os
# Runs this app's own manage.py with fixed arguments.
import subprocess  # nosec B404
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.utils import timezone

RESULT = "restore-test.json"


def last() -> dict | None:
    try:
        return json.loads((Path(settings.DATA_DIR) / RESULT).read_text())
    except (OSError, ValueError):
        return None


def due(days: int = 7) -> bool:
    prev = last()
    if not prev:
        return True
    from datetime import datetime

    try:
        return (timezone.now() - datetime.fromisoformat(prev["at"])).days >= days
    except (KeyError, ValueError):
        return True


def run(name: str | None = None) -> dict:
    from core import backup_crypto, backups

    started = time.monotonic()
    listed = backups.list_backups()
    result = {"at": timezone.now().isoformat(), "backup": None, "ok": False, "problems": []}
    if not listed:
        result["problems"].append("There are no backups to test.")
        return _save(result)
    name = name or listed[0]["name"]
    result["backup"] = name
    src = backups.backup_path(name)
    with tempfile.TemporaryDirectory(prefix="dealflow-restore-test-") as tmpdir:
        tmp = Path(tmpdir)
        zpath = src
        try:
            if backup_crypto.is_encrypted(src):
                zpath = tmp / "backup.zip"
                backup_crypto.decrypt(src, zpath)
            with zipfile.ZipFile(zpath) as z:
                bad = z.testzip()
                if bad:
                    result["problems"].append(f"A file inside the backup is damaged: {bad}")
                result["files"] = sum(1 for n in z.namelist() if n.startswith("files/") and not n.endswith("/"))
                (tmp / "data.json").write_bytes(z.read("data.json"))
        except Exception as e:  # damaged file, wrong key, not a backup
            result["problems"].append(f"The backup couldn't be opened: {e}")
            return _save(result)
        expected = Counter(o["model"] for o in json.loads((tmp / "data.json").read_text()))
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{tmp / 'check.sqlite3'}",
            "DEALFLOW_DB_ENCRYPTION": "0",
            "DEALFLOW_DATA_DIR": str(tmp / "data"),
            "DEALFLOW_KEYS_DIR": str(tmp / "keys"),
            "DEALFLOW_AUDIT_DIR": str(tmp / "audit"),
            "DJANGO_SECRET_KEY": "restore-test-only",
        }
        # Fixed command, no shell.
        proc = subprocess.run(  # nosec B603
            [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "restore_check", str(tmp / "data.json")],
            env=env, capture_output=True, text=True, timeout=900, cwd=str(settings.BASE_DIR),
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            result["problems"].append("Loading the backup into a fresh database failed: " + " ".join(tail)[:500])
            return _save(result, started)
        loaded = json.loads(proc.stdout.strip().splitlines()[-1])
        for model, n in sorted(expected.items()):
            if loaded.get(model) != n:
                result["problems"].append(f"{model}: {n} in the backup, {loaded.get(model, 0)} after loading.")
        result["models"] = len(expected)
        result["rows"] = sum(expected.values())
    result["ok"] = not result["problems"]
    return _save(result, started)


def _save(result: dict, started: float | None = None) -> dict:
    if started is not None:
        result["seconds"] = round(time.monotonic() - started, 1)
    result["problems"] = result["problems"][:20]
    path = Path(settings.DATA_DIR) / RESULT
    path.write_text(json.dumps(result))
    return result
