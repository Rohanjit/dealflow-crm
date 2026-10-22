"""Backups: a zip holding every record (Django's serializer, one JSON object per line) plus
uploaded files and an encrypted copy of the audit log.

Being database-neutral JSON, a backup taken on SQLite restores on Postgres and back (decision 9).

Nothing is held whole in memory and nothing readable is written to disk (load test, 22 Oct 2026:
the old way needed 4.2 GB for a 584 MB database). Records are read 2,000 at a time and each line is
compressed and encrypted as it's written (`backup_crypto.EncryptingWriter`); restoring reads the
encrypted file in place (`backup_crypto.DecryptingReader`) and loads one record at a time.
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
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import TemporaryUploadedFile, UploadedFile
from django.core.files.uploadhandler import FileUploadHandler, TemporaryFileUploadHandler
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

# 1: data.json (one JSON array; still restorable). 2: data.jsonl (one record per line), stored files
# not compressed again (they're already encrypted).
FORMAT_VERSION = 2
READABLE_FORMATS = {1: ("data.json", "json"), 2: ("data.jsonl", "jsonl")}
# .dfb = encrypted (decision 63, S4); .zip = from before backups were encrypted (still restorable).
NAME_RE = re.compile(r"^dealflow-(daily|hourly|manual|pre-restore)-\d{8}-\d{6}\.(dfb|zip)$")
EXCLUDE = ["contenttypes", "auth.permission", "sessions"]
BATCH = 2000


HOURLY_KEEP = 24


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return settings.BACKUP_DIR


class _Lock:
    """One backup or restore at a time across processes (the scheduler, web workers). An operating
    system lock, so it ends with the process holding it: a backup that was killed no longer blocks
    the next one for an hour (load test finding 15)."""

    def __init__(self):
        self.path = backup_dir() / ".lock"
        self.fd = None

    def __enter__(self):
        import fcntl

        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise BackupError("Another backup or restore is running. Try again in a minute.")
        self.fd = fd
        return self

    def __exit__(self, *exc):
        os.close(self.fd)  # releases the lock; the empty file stays for the next one


def _models():
    """What a backup carries, in the order Django's dumpdata would write it."""
    from django.apps import apps
    from django.db import router

    skip_apps = {e for e in EXCLUDE if "." not in e}
    skip_models = {e.lower() for e in EXCLUDE if "." in e}
    for model in apps.get_models():
        meta = model._meta
        if meta.app_label in skip_apps or meta.label_lower in skip_models or meta.proxy:
            continue
        if router.allow_migrate_model(connection.alias, model):
            yield model


def _objects():
    """Every record, read BATCH at a time, with many-to-many values fetched per batch rather than
    one query per record (dumpdata's way: about 40% of the old backup's time)."""
    for model in _models():
        m2m = [f.name for f in model._meta.many_to_many if f.remote_field.through._meta.auto_created]
        qs = model._default_manager.using(connection.alias).order_by(model._meta.pk.name)
        if m2m:
            qs = qs.prefetch_related(*m2m)
        yield from qs.iterator(chunk_size=BATCH)


def write_data(stream, fmt: str = "jsonl"):
    """Write every record to a text stream, one at a time."""
    from django.core import serializers

    serializers.serialize(fmt, _objects(), stream=stream)


def load_data(fmt: str, stream) -> Counter:
    """Load records written by write_data (or dumpdata) into an empty database, one at a time, as
    `loaddata` does: foreign keys checked once at the end, records pointing forward saved once their
    targets exist, sequences reset. Returns the number of rows per model. Call inside a transaction."""
    from django.core import serializers
    from django.core.management.color import no_style
    from django.db import router

    using = connection.alias
    counts: Counter = Counter()
    models, deferred = set(), []
    try:
        with connection.constraint_checks_disabled():
            for obj in serializers.deserialize(fmt, stream, using=using, handle_forward_references=True):
                model = type(obj.object)
                if not router.allow_migrate_model(using, model):
                    continue
                models.add(model)
                obj.save(using=using)
                counts[model._meta.label_lower] += 1
                if obj.deferred_fields:
                    deferred.append(obj)
            for obj in deferred:
                obj.save_deferred_fields(using=using)
        connection.check_constraints(table_names=[m._meta.db_table for m in models])
    except BackupError:
        raise
    except Exception as e:
        raise BackupError(f"The backup's data couldn't be loaded ({type(e).__name__}: {str(e)[:300]}).") from e
    if counts:
        with connection.cursor() as c:
            for sql in connection.ops.sequence_reset_sql(no_style(), models):
                c.execute(sql)
    return counts


def _write_zip(out, kind: str):
    """The backup's zip, written to a binary stream that needn't be seekable."""
    from . import audit
    from .models import FirmSettings

    manifest = {
        "format": FORMAT_VERSION,
        "kind": kind,
        "created_at": timezone.now().isoformat(),
        "firm_name": FirmSettings.load().firm_name,
        "database": connection.vendor,
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        with z.open("data.jsonl", "w", force_zip64=True) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8", newline="\n", write_through=False) as text:
                write_data(text)
        media = Path(settings.MEDIA_ROOT)
        if media.exists():
            for p in sorted(media.rglob("*")):
                if p.is_file():
                    # Already encrypted (Vault, data rooms, mail uploads): compressing again gains nothing.
                    z.write(p, "files/" + p.relative_to(media).as_posix(), compress_type=zipfile.ZIP_STORED)
        # A copy of the audit log, still encrypted. Restores never put it back over the live log.
        for p in audit.backup_files():
            z.write(p, "audit/" + p.name, compress_type=zipfile.ZIP_STORED)


def create_backup(kind: str = "manual") -> Path:
    from .models import FirmSettings

    if kind not in ("daily", "hourly", "manual", "pre-restore"):
        raise ValueError(f"Unknown backup kind: {kind}")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    target = backup_dir() / f"dealflow-{kind}-{stamp}.dfb"
    fs = FirmSettings.load()
    with _Lock():
        try:
            from . import backup_crypto

            part = target.with_suffix(".dfb.part")
            try:
                with open(part, "wb") as out:
                    enc = backup_crypto.EncryptingWriter(out, {"kind": kind, "created_at": timezone.now().isoformat(),
                                                               "firm_name": fs.firm_name})
                    _write_zip(enc, kind)
                    enc.close()
                    out.flush()
                    os.fsync(out.fileno())
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
        except Exception as e:  # record the failure for the Settings screen
            FirmSettings.objects.filter(pk=1).update(last_backup_error=str(e)[:2000])
            raise
    if kind == "daily":
        FirmSettings.objects.filter(pk=1).update(last_backup_at=timezone.now(), last_backup_error="")
    elif kind == "hourly":
        FirmSettings.objects.filter(pk=1).update(last_hourly_at=timezone.now(), last_backup_error="")
    prune(fs.backup_keep)
    return target


def list_backups() -> list[dict]:
    out = []
    stamp = re.compile(r"(\d{8}-\d{6})")
    files = [p for p in backup_dir().glob("dealflow-*") if NAME_RE.match(p.name)]
    for p in sorted(files, key=lambda x: stamp.search(x.name).group(1), reverse=True):
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
    for kind, paths in by_kind.items():
        for p in paths[(HOURLY_KEEP if kind == "hourly" else keep):]:
            p.unlink(missing_ok=True)
    for p in backup_dir().glob("*.part"):  # left by a backup whose process was killed
        try:
            if time.time() - p.stat().st_mtime > 86400:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def backup_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise BackupError("No such backup.")
    p = backup_dir() / name
    if not p.exists():
        raise BackupError("No such backup.")
    return p


class DiskUploadHandler(TemporaryFileUploadHandler):
    """Uploaded backups go to a temporary file in the data folder, whatever their size. Django's
    default is the system temp folder, which in Docker is memory (1 GB) and counts against the
    container's memory. The file is still encrypted, so nothing readable is written."""

    def new_file(self, *args, **kwargs):
        FileUploadHandler.new_file(self, *args, **kwargs)
        self.file = _DataDirUpload(self.file_name, self.content_type, 0, self.charset, self.content_type_extra)


class _DataDirUpload(TemporaryUploadedFile):
    def __init__(self, name, content_type, size, charset, content_type_extra=None):
        f = tempfile.NamedTemporaryFile(prefix=".upload-", suffix=".upload", dir=settings.DATA_DIR)
        UploadedFile.__init__(self, f, name, content_type, size, charset, content_type_extra)


def open_backup(path: Path):
    """(zip, manifest, (member, format)) for an encrypted or older plain backup file; the caller
    closes the zip (which closes the reader). Raises BackupError for anything that isn't one."""
    from . import backup_crypto

    reader = None
    try:
        if backup_crypto.is_encrypted(path):
            reader = backup_crypto.DecryptingReader(path)
        else:
            reader = open(path, "rb")
        z = zipfile.ZipFile(reader)
    except backup_crypto.BackupKeyError as e:
        if reader:
            reader.close()
        raise BackupError(str(e))
    except (zipfile.BadZipFile, OSError, ValueError):
        if reader:
            reader.close()
        raise BackupError("That file isn't a Dealflow backup.")
    z._dealflow_reader = reader  # closed with the zip (zipfile doesn't close a stream it was given)
    try:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise BackupError("That file isn't a Dealflow backup.")
        manifest = json.loads(z.read("manifest.json"))
        data = READABLE_FORMATS.get(manifest.get("format"))
        if not data:
            raise BackupError("This backup was made by an incompatible version of Dealflow.")
        if data[0] not in names:
            raise BackupError("That file isn't a Dealflow backup.")
        return z, manifest, data
    except BackupError:
        close_backup(z)
        raise
    except Exception:
        close_backup(z)
        raise BackupError("That file isn't a Dealflow backup.")


def close_backup(z):
    reader = getattr(z, "_dealflow_reader", None)
    z.close()
    if reader:
        reader.close()


def sweep_work_dirs():
    """Folders left by a restore or restore test whose process was killed."""
    data = Path(settings.DATA_DIR)
    media = Path(settings.MEDIA_ROOT)
    for p in [*data.glob(".restore-*"), *data.glob(".dealflow-restore-test-*"),
              *media.parent.glob(f".{media.name}-replaced-*")]:
        try:
            if time.time() - p.stat().st_mtime > 86400:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def restore_backup(fileobj) -> dict:
    """Replace ALL data with the backup's contents. Takes a safety backup first. Works in the data
    folder (not the system temp folder, which is memory in Docker); only the uploaded files, which
    are stored encrypted, are unpacked there."""
    sweep_work_dirs()
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=settings.DATA_DIR) as tmpdir:
        tmp = Path(tmpdir)
        path = getattr(fileobj, "temporary_file_path", None)
        if path:
            path = Path(path())
        else:
            path = tmp / "upload.dfb"
            with open(path, "wb") as out:
                shutil.copyfileobj(fileobj, out, 1024 * 1024)
        z, manifest, (member, fmt) = open_backup(path)
        try:
            files_dir = tmp / "files"
            for n in z.namelist():
                if n.startswith("files/") and not n.endswith("/"):
                    rel = Path(n[len("files/"):])
                    if rel.is_absolute() or ".." in rel.parts:
                        raise BackupError("The backup contains an unsafe file path.")
                    dest = files_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(n) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)

            safety = create_backup("pre-restore")
            with z.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
                replace_all_data((fmt, text), files_dir if files_dir.exists() else None)
        finally:
            close_backup(z)
    return {"restored_from": manifest.get("created_at"), "safety_backup": safety.name}


def replace_all_data(data, files_dir: Path | None):
    """Swap every table for the records in `data` (a dump file, or a (format, text stream) pair),
    and uploaded files for `files_dir` (or nothing). Used by restore and by leaving the guest sample
    (core/guest.py)."""
    with _Lock():
        with transaction.atomic():
            if connection.vendor == "postgresql":
                # Postgres refuses TRUNCATE while deferred FK checks are pending in the transaction.
                with connection.cursor() as c:
                    c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            call_command("flush", interactive=False, verbosity=0)
            if connection.vendor == "postgresql":
                # Records can point at ones loaded later (a contact at a company from Outreach),
                # so foreign keys are checked at commit rather than row by row.
                with connection.cursor() as c:
                    c.execute("SET CONSTRAINTS ALL DEFERRED")
            if isinstance(data, (str, Path)):
                fmt = "jsonl" if str(data).endswith(".jsonl") else "json"
                with open(data, encoding="utf-8") as f:
                    load_data(fmt, f)
            else:
                load_data(*data)
        media = Path(settings.MEDIA_ROOT)
        old = media.with_name(f".{media.name}-replaced-{int(time.time())}")
        if media.exists():
            media.rename(old)
        if files_dir is not None:
            shutil.move(str(files_dir), str(media))  # a rename when both are in the data folder
        else:
            media.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(old, ignore_errors=True)
    from . import stuck

    stuck.forget()  # figures held by this worker described the data that was just replaced


def dump_data(target: Path):
    """Everything backups carry, as one JSON file (the guest sample's empty install)."""
    with open(target, "w", encoding="utf-8") as out:
        write_data(out, "json")


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


def due_for_hourly_backup(now=None) -> bool:
    from .models import FirmSettings

    fs = FirmSettings.load()
    if not (fs.backups_enabled and fs.hourly_backups and fs.setup_completed_at):
        return False
    now = timezone.localtime(now)
    last = fs.last_hourly_at and timezone.localtime(fs.last_hourly_at)
    return not last or (last.date(), last.hour) != (now.date(), now.hour)
