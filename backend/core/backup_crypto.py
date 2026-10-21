"""Backups are encrypted (decision 63, S4) with an AES-256-GCM key kept in the keys folder, apart
from the backups themselves. Large backups are encrypted in 1 MiB pieces using the STREAM
construction (Hoang, Reyhanitabar, Rogaway & Vizár 2015, as in Google Tink's streaming AEAD): each
piece has its own nonce made of a random 7-byte prefix, a 4-byte counter and a last-piece flag, so
pieces can't be reordered, dropped, repeated or cut short. The plaintext header (key id, kind, date)
is bound to every piece as associated data.

File layout: b"DFBK" | 4-byte header length | header JSON | pieces, each 4-byte length + ciphertext."""

from __future__ import annotations

import json
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"DFBK"
CHUNK = 1024 * 1024


class BackupKeyError(Exception):
    pass


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    return prefix + struct.pack(">I", counter) + (b"\x01" if last else b"\x00")


def encrypt(src_path, dst_path, header: dict):
    from dealflow import keys

    key_id, key = keys.backup_key()
    header = {**header, "key": key_id, "format": 2}
    head = json.dumps(header, sort_keys=True).encode()
    aes, prefix = AESGCM(key), os.urandom(7)
    with open(src_path, "rb") as src, open(dst_path, "wb") as out:
        out.write(MAGIC + struct.pack(">I", len(head)) + head + prefix)
        counter = 0
        piece = src.read(CHUNK)
        while True:
            nxt = src.read(CHUNK)
            last = not nxt
            ct = aes.encrypt(_nonce(prefix, counter, last), piece, head)
            out.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            piece, counter = nxt, counter + 1
        out.flush()
        os.fsync(out.fileno())


def is_encrypted(path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == MAGIC


def read_header(path) -> dict:
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise BackupKeyError("Not an encrypted backup.")
        (n,) = struct.unpack(">I", f.read(4))
        if n > 65536:
            raise BackupKeyError("That backup's header is damaged.")
        return json.loads(f.read(n))


def decrypt(src_path, dst_path) -> dict:
    from dealflow import keys

    with open(src_path, "rb") as src, open(dst_path, "wb") as out:
        if src.read(4) != MAGIC:
            raise BackupKeyError("Not an encrypted backup.")
        (n,) = struct.unpack(">I", src.read(4))
        if n > 65536:
            raise BackupKeyError("That backup's header is damaged.")
        head = src.read(n)
        header = json.loads(head)
        try:
            _, key = keys.backup_key(str(header.get("key", "")))
        except keys.KeyMissing:
            raise BackupKeyError("This backup is locked with a backup key this install doesn't have. Upload that "
                                 "key file first (Settings > Backups on the install that made it: Download the backup key).")
        aes, prefix = AESGCM(key), src.read(7)
        counter, finished = 0, False
        while True:
            raw = src.read(4)
            if not raw:
                break
            if finished:
                raise BackupKeyError("That backup has extra data after its end.")
            (m,) = struct.unpack(">I", raw)
            ct = src.read(m)
            if len(ct) != m:
                raise BackupKeyError("That backup is cut short.")
            for last in (False, True):
                try:
                    out.write(aes.decrypt(_nonce(prefix, counter, last), ct, head))
                    finished = last
                    break
                except Exception:
                    if last:
                        raise BackupKeyError("That backup was changed or damaged; it can't be restored.")
            counter += 1
        if not finished:
            raise BackupKeyError("That backup is cut short.")
    return header
