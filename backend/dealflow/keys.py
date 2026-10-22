"""Keys (decision 63, S4). Every key lives in KEYS_DIR, which is kept apart from the data it
protects: in Docker it is its own volume (`/keys`), so a copy of the data volume, a database file or
a backup is unreadable on its own.

    secret_key            Django's SECRET_KEY (sessions, and the sealing of mailbox tokens and
                          authenticator secrets)
    db.key                SQLCipher key of the SQLite database
    master.key            seals files waiting to be emailed, and is mixed into every Vault PIN key
    backups/<id>.key      AES-256-GCM keys for backups; `current` names the one in use, older ones
                          stay so older backups can still be restored

Keys are 32 random bytes from the OS, written once with owner-only permissions. A key that should
exist but doesn't stops the app (fail closed) rather than starting with a new one.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


class KeyMissing(RuntimeError):
    pass


def keys_dir() -> Path:
    from django.conf import settings

    return Path(settings.KEYS_DIR)


def _ensure_dir(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass


def _write_new(path: Path, text: str):
    """Write a key file once, owner-only; never overwrite one that exists."""
    _ensure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def read(name: str) -> bytes:
    path = keys_dir() / name
    try:
        raw = path.read_text().strip()
    except FileNotFoundError:
        raise KeyMissing(f"The key {name} is missing from {keys_dir()}. Restore the keys folder from where it is kept "
                         "safe; Dealflow won't make a new one over existing data.")
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise KeyMissing(f"The key {name} in {keys_dir()} is damaged.")
    if len(key) != 32:
        raise KeyMissing(f"The key {name} in {keys_dir()} is the wrong length.")
    return key


def exists(name: str) -> bool:
    return (keys_dir() / name).exists()


def create(name: str) -> bytes:
    key = secrets.token_bytes(32)
    _write_new(keys_dir() / name, key.hex())
    return key


def get_or_create(name: str) -> bytes:
    if exists(name):
        return read(name)
    try:
        return create(name)
    except FileExistsError:  # another process made it a moment ago
        return read(name)


def master() -> bytes:
    return get_or_create("master.key")


# ---------------------------------------------------------------- backup keys


def backup_key(key_id: str | None = None) -> tuple[str, bytes]:
    """(id, key): the one in use, or a named older one."""
    d = keys_dir() / "backups"
    if key_id is None:
        cur = d / "current"
        if not cur.exists():
            return new_backup_key()
        key_id = cur.read_text().strip()
    if not key_id.isalnum() or len(key_id) > 40:
        raise KeyMissing("That backup names a key Dealflow can't use.")
    return key_id, read(f"backups/{key_id}.key")


def new_backup_key() -> tuple[str, bytes]:
    d = keys_dir() / "backups"
    _ensure_dir(d)
    key_id = secrets.token_hex(6)
    key = create(f"backups/{key_id}.key")
    tmp = d / f".current-{key_id}"
    tmp.write_text(key_id)
    os.replace(tmp, d / "current")
    return key_id, key


def import_backup_key(text: str) -> str:
    """A key file from another install ("dealflow-backup-key:<id>:<hex>"), so its backups restore here."""
    try:
        prefix, key_id, hexkey = text.strip().split(":")
        key = bytes.fromhex(hexkey)
    except ValueError:
        raise KeyMissing("That isn't a Dealflow backup key file.")
    if prefix != "dealflow-backup-key" or len(key) != 32 or not key_id.isalnum() or len(key_id) > 40:
        raise KeyMissing("That isn't a Dealflow backup key file.")
    path = keys_dir() / "backups" / f"{key_id}.key"
    if path.exists():
        if read(f"backups/{key_id}.key") != key:
            raise KeyMissing("A different key with the same name is already here.")
        return key_id
    _write_new(path, key.hex())
    return key_id


def export_backup_key(key_id: str | None = None) -> str:
    key_id, key = backup_key(key_id)
    return f"dealflow-backup-key:{key_id}:{key.hex()}\n"


# ---------------------------------------------------------------- start-up


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def status() -> dict:
    """For Settings > Security: where keys live and whether that's apart from the data."""
    from django.conf import settings

    kd, dd = keys_dir(), Path(settings.DATA_DIR)
    return {
        "keys_dir": str(kd),
        "separate": not inside(kd, dd) and not inside(dd, kd),
        "db_encrypted": bool(getattr(settings, "DB_ENCRYPTED", False)),
        "postgres": "postgresql" in settings.DATABASES["default"]["ENGINE"],
        "backup_key_id": (kd / "backups" / "current").read_text().strip() if (kd / "backups" / "current").exists() else None,
    }
