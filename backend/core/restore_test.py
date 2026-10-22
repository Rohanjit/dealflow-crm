"""Backups are proven to restore (decision 63, S7). Weekly (and on demand from Settings > Security),
the newest backup is read back in place (decrypted piece by piece in memory, never written out),
every file in it is checked against its zip checksum, and its records are streamed to a separate
process that loads them into a fresh temporary database the way a restore does; the number of rows
of every kind must match what the backup holds. That process never sees the backup key, and its
database is encrypted with a key kept only in memory. The live
data is never touched. The result is kept in DATA_DIR/restore-test.json."""

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
    try:
        z, manifest, (member, fmt) = backups.open_backup(src)
    except backups.BackupError as e:  # damaged file, wrong key, not a backup
        result["problems"].append(f"The backup couldn't be opened: {e}")
        return _save(result)
    backups.sweep_work_dirs()
    # The test database goes in the data folder (it can be as big as the live one; /tmp is memory in
    # Docker), encrypted with a throwaway key that lives only in memory-backed /tmp and is gone after.
    with tempfile.TemporaryDirectory(prefix=".dealflow-restore-test-", dir=settings.DATA_DIR) as tmpdir, \
            tempfile.TemporaryDirectory(prefix="dealflow-restore-test-keys-") as keydir:
        tmp = Path(tmpdir)
        try:
            files = 0
            for n in z.namelist():
                if n == member:
                    continue
                try:
                    with z.open(n) as f:  # reading to the end checks the member's checksum
                        while f.read(1024 * 1024):
                            pass
                except (zipfile.BadZipFile, OSError, backup_crypto.BackupKeyError) as e:
                    result["problems"].append(f"A file inside the backup is damaged: {n} ({e})")
                files += n.startswith("files/") and not n.endswith("/")
            result["files"] = files
            env = {
                **os.environ,
                "DATABASE_URL": f"sqlite:///{tmp / 'check.sqlite3'}",
                "DEALFLOW_DB_ENCRYPTION": "1",
                "DEALFLOW_DATA_DIR": str(tmp / "data"),
                "DEALFLOW_KEYS_DIR": keydir,
                "DEALFLOW_AUDIT_DIR": str(tmp / "audit"),
                "DJANGO_SECRET_KEY": "restore-test-only",
            }
            with tempfile.TemporaryFile(dir=tmp) as out, tempfile.TemporaryFile(dir=tmp) as err:
                # Fixed command, no shell. Records go through a pipe, so no readable copy is written.
                proc = subprocess.Popen(  # nosec B603
                    [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "restore_check", "--format", fmt, "-"],
                    env=env, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=str(settings.BASE_DIR),
                )
                expected, failure = _feed(z, member, fmt, proc)
                try:
                    proc.wait(timeout=900)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                out.seek(0)
                err.seek(0)
                stdout, stderr = out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")
        finally:
            backups.close_backup(z)
        if failure:
            result["problems"].append(f"The backup's records couldn't be read: {failure}")
            return _save(result, started)
        if proc.returncode != 0:
            tail = (stderr or stdout).strip().splitlines()[-3:]
            result["problems"].append("Loading the backup into a fresh database failed: " + " ".join(tail)[:500])
            return _save(result, started)
        loaded = json.loads(stdout.strip().splitlines()[-1])
        for model, n in sorted(expected.items()):
            if loaded.get(model) != n:
                result["problems"].append(f"{model}: {n} in the backup, {loaded.get(model, 0)} after loading.")
        result["models"] = len(expected)
        result["rows"] = sum(expected.values())
    result["ok"] = not result["problems"]
    return _save(result, started)


def _feed(z, member: str, fmt: str, proc) -> tuple[Counter, str | None]:
    """Send the backup's records to the checking process, counting them by kind on the way."""
    expected: Counter = Counter()
    failure = None
    try:
        with z.open(member) as src:
            if fmt == "jsonl":
                for line in src:
                    if line.strip():
                        expected[json.loads(line)["model"]] += 1
                    proc.stdin.write(line)
            else:  # older backups: one JSON array, counted whole (only small, old backups are like this)
                raw = src.read()
                expected.update(o["model"] for o in json.loads(raw))
                proc.stdin.write(raw)
    except BrokenPipeError:
        pass  # the checking process stopped early; its own error is reported
    except Exception as e:  # damaged or unreadable records
        failure = str(e)[:300]
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
    return expected, failure


def _save(result: dict, started: float | None = None) -> dict:
    if started is not None:
        result["seconds"] = round(time.monotonic() - started, 1)
    result["problems"] = result["problems"][:20]
    path = Path(settings.DATA_DIR) / RESULT
    path.write_text(json.dumps(result))
    return result
