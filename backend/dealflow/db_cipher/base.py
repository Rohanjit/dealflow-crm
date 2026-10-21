"""SQLite encrypted with SQLCipher (AES-256, decision 63 S4). Django's own SQLite backend, with
`sqlite3` swapped for the SQLCipher build in settings.py, and the key set first on every
connection. The key is `db.key` in KEYS_DIR, never beside the database.

A plain database from before encryption is converted on first use, under a lock so only one
process does it; the plain file is replaced, not kept."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from django.conf import settings
from django.db.backends.sqlite3 import base as sqlite_base

PLAIN_HEADER = b"SQLite format 3\x00"


def _key_pragma(key: bytes) -> str:
    return f"PRAGMA key = \"x'{key.hex()}'\""


def is_plain(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == PLAIN_HEADER
    except FileNotFoundError:
        return False


def encrypt_file(path: Path, key: bytes):
    """Turn a plain SQLite file into an encrypted one in place."""
    import sqlcipher3.dbapi2 as cipher

    tmp = path.with_name(path.name + ".encrypting")
    tmp.unlink(missing_ok=True)
    conn = cipher.connect(str(path))
    try:
        conn.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key.hex()}'\"", (str(tmp),))
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute("DETACH DATABASE encrypted")
    finally:
        conn.close()
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)


def database_key(path: Path) -> bytes:
    """The key, made on a fresh install; refused when an encrypted database has lost its key."""
    from dealflow import keys

    if keys.exists("db.key"):
        return keys.read("db.key")
    if path.exists() and path.stat().st_size and not is_plain(path):
        raise keys.KeyMissing(f"{path.name} is encrypted but db.key is missing from {keys.keys_dir()}. "
                              "Restore the keys folder; Dealflow won't start without it.")
    return keys.get_or_create("db.key")


def prepare(path: Path) -> bytes:
    """Key in hand and the file encrypted, whatever state it was left in."""
    from dealflow import keys

    keys._ensure_dir(keys.keys_dir())
    lock = keys.keys_dir() / ".db.lock"
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        key = database_key(path)
        if is_plain(path):
            encrypt_file(path, key)
    return key


class DatabaseWrapper(sqlite_base.DatabaseWrapper):
    vendor = "sqlite"
    display_name = "SQLite (SQLCipher)"

    def get_new_connection(self, conn_params):
        name = conn_params.get("database", "")
        in_memory = name == ":memory:" or "mode=memory" in str(name)
        key = None if in_memory else prepare(Path(name))
        conn = super().get_new_connection(conn_params)
        if key is not None:
            conn.execute(_key_pragma(key))
            # Fail closed: a wrong key shows up here, not halfway through a request.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
