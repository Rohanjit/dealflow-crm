"""Backups: a zip holding a Django `dumpdata` export plus uploaded files.

Being database-neutral JSON, a backup taken on SQLite restores on Postgres and back.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

FORMAT_VERSION = 1
NAME_RE = re.compile(r"^dealflow-(daily|manual|pre-restore)-\d{8}-\d{6}\.zip$")
EXCLUDE = ["contenttypes", "auth.permission", "sessions"]


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return settings.BACKUP_DIR


class _Lock:
    """Crude cross-process lock so the scheduler and a manual backup don't collide."""

    def __init__(self):
        self.path = backup_dir() / ".lock"

    def __enter__(self):
        if self.path.exists() and time.time() - self.path.stat().st_mtime > 3600:
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BackupError("Another backup or restore is running. Try again in a minute.")
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _write_zip(target: Path, kind: str):
    from .models import FirmSettings

    data = io.StringIO()
    call_command("dumpdata", exclude=EXCLUDE, natural_foreign=False, indent=None, stdout=data)
    manifest = {
        "format": FORMAT_VERSION,
        "kind": kind,
        "created_at": timezone.now().isoformat(),
        "firm_name": FirmSettings.load().firm_name,
        "database": connection.vendor,
    }
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("data.json", data.getvalue())
        media = Path(settings.MEDIA_ROOT)
        if media.exists():
            for p in media.rglob("*"):
                if p.is_file():
                    z.write(p, "files/" + p.relative_to(media).as_posix())
    tmp.replace(target)


def create_backup(kind: str = "manual") -> Path:
    from .models import FirmSettings

    assert kind in ("daily", "manual", "pre-restore")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    target = backup_dir() / f"dealflow-{kind}-{stamp}.zip"
    fs = FirmSettings.load()
    with _Lock():
        try:
            _write_zip(target, kind)
        except Exception as e:  # record the failure for the Settings screen
            FirmSettings.objects.filter(pk=1).update(last_backup_error=str(e)[:2000])
            raise
    if kind == "daily":
        FirmSettings.objects.filter(pk=1).update(last_backup_at=timezone.now(), last_backup_error="")
    prune(fs.backup_keep)
    return target


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backup_dir().glob("dealflow-*.zip"), reverse=True):
        if not NAME_RE.match(p.name):
            continue
        st = p.stat()
        out.append(
            {
                "name": p.name,
                "kind": p.name.split("-")[1] if not p.name.startswith("dealflow-pre-restore") else "pre-restore",
                "size": st.st_size,
                "created_at": timezone.datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone()).isoformat(),
            }
        )
    return out


def prune(keep: int):
    """Keep the newest `keep` backups of each kind."""
    keep = max(1, keep)
    by_kind: dict[str, list[Path]] = {}
    for b in list_backups():
        by_kind.setdefault(b["kind"], []).append(backup_dir() / b["name"])
    for paths in by_kind.values():
        for p in paths[keep:]:
            p.unlink(missing_ok=True)


def backup_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise BackupError("No such backup.")
    p = backup_dir() / name
    if not p.exists():
        raise BackupError("No such backup.")
    return p


def restore_backup(fileobj) -> dict:
    """Replace ALL data with the backup's contents. Takes a safety backup first."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        zpath = tmp / "upload.zip"
        with open(zpath, "wb") as out:
            shutil.copyfileobj(fileobj, out)
        try:
            z = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            raise BackupError("That file isn't a Dealflow backup.")
        with z:
            names = set(z.namelist())
            if "manifest.json" not in names or "data.json" not in names:
                raise BackupError("That file isn't a Dealflow backup.")
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != FORMAT_VERSION:
                raise BackupError("This backup was made by an incompatible version of Dealflow.")
            data_file = tmp / "data.json"
            data_file.write_bytes(z.read("data.json"))
            files_dir = tmp / "files"
            for n in names:
                if n.startswith("files/") and not n.endswith("/"):
                    rel = Path(n[len("files/"):])
                    if rel.is_absolute() or ".." in rel.parts:
                        raise BackupError("The backup contains an unsafe file path.")
                    dest = files_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(n) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)

        safety = create_backup("pre-restore")
        with _Lock():
            with transaction.atomic():
                call_command("flush", interactive=False, verbosity=0)
                call_command("loaddata", str(data_file), verbosity=0)
            media = Path(settings.MEDIA_ROOT)
            if media.exists():
                shutil.rmtree(media)
            if files_dir.exists():
                shutil.copytree(files_dir, media)
            else:
                media.mkdir(parents=True, exist_ok=True)
    return {"restored_from": manifest.get("created_at"), "safety_backup": safety.name}


def due_for_daily_backup(now=None) -> bool:
    from .models import FirmSettings

    fs = FirmSettings.load()
    if not fs.backups_enabled or not fs.setup_completed_at:
        return False
    now = timezone.localtime(now)
    if now.hour < fs.backup_hour:
        return False
    if fs.last_backup_at and timezone.localtime(fs.last_backup_at).date() >= now.date():
        return False
    return True
