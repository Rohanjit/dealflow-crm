"""Backups are encrypted (decision 63, S4) with an AES-256-GCM key kept in the keys folder, apart
from the backups themselves. Large backups are encrypted in 1 MiB pieces using the STREAM
construction (Hoang, Reyhanitabar, Rogaway & Vizár 2015, as in Google Tink's streaming AEAD): each
piece has its own nonce made of a random 7-byte prefix, a 4-byte counter and a last-piece flag, so
pieces can't be reordered, dropped, repeated or cut short. The plaintext header (key id, kind, date)
is bound to every piece as associated data.

File layout: b"DFBK" | 4-byte header length | header JSON | 7-byte prefix | pieces, each 4-byte
length + ciphertext.

Backups are written through `EncryptingWriter` as they are made and read back through
`DecryptingReader`, so a readable copy never touches the disk (load test, 22 Oct 2026). Every piece
but the last holds exactly CHUNK bytes, which is what lets the reader find any piece without reading
the ones before it."""

from __future__ import annotations

import io
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


class EncryptingWriter(io.RawIOBase):
    """A write-only file that encrypts what is written to it, piece by piece, into `dst` (an open
    binary file). Holds at most two pieces in memory. close() writes the last piece; it doesn't close
    `dst`. Not seekable, so zipfile writes in streaming mode (sizes after each member)."""

    def __init__(self, dst, header: dict):
        from dealflow import keys

        super().__init__()
        key_id, key = keys.backup_key()
        self.header = {**header, "key": key_id, "format": 2}
        self._head = json.dumps(self.header, sort_keys=True).encode()
        self._aes, self._prefix = AESGCM(key), os.urandom(7)
        self._dst, self._buf, self._counter, self._written = dst, bytearray(), 0, 0
        dst.write(MAGIC + struct.pack(">I", len(self._head)) + self._head + self._prefix)

    def writable(self):
        return True

    def tell(self):
        return self._written

    def write(self, b):
        if self.closed:
            raise ValueError("write to a closed backup")
        n = len(b)
        self._buf += b
        self._written += n
        # A piece goes out only once more follows it, so the last one can be marked as last.
        while len(self._buf) > CHUNK:
            self._emit(bytes(self._buf[:CHUNK]), last=False)
            del self._buf[:CHUNK]
        return n

    def _emit(self, piece: bytes, last: bool):
        ct = self._aes.encrypt(_nonce(self._prefix, self._counter, last), piece, self._head)
        self._dst.write(struct.pack(">I", len(ct)) + ct)
        self._counter += 1

    def close(self):
        if not self.closed:
            self._emit(bytes(self._buf), last=True)
            self._buf = bytearray()
            self._dst.flush()
        super().close()


def encrypt(src_path, dst_path, header: dict):
    with open(src_path, "rb") as src, open(dst_path, "wb") as out:
        w = EncryptingWriter(out, header)
        while piece := src.read(CHUNK):
            w.write(piece)
        w.close()
        out.flush()
        os.fsync(out.fileno())


def _open_header(src) -> tuple[bytes, dict]:
    if src.read(4) != MAGIC:
        raise BackupKeyError("Not an encrypted backup.")
    raw = src.read(4)
    if len(raw) != 4:
        raise BackupKeyError("That backup is cut short.")
    (n,) = struct.unpack(">I", raw)
    if n > 65536:
        raise BackupKeyError("That backup's header is damaged.")
    head = src.read(n)
    try:
        return head, json.loads(head)
    except ValueError:
        raise BackupKeyError("That backup's header is damaged.")


def _key_for(header: dict) -> bytes:
    from dealflow import keys

    try:
        return keys.backup_key(str(header.get("key", "")))[1]
    except keys.KeyMissing:
        raise BackupKeyError("This backup is locked with a backup key this install doesn't have. Upload that "
                             "key file first (Settings > Backups on the install that made it: Download the backup key).")


class DecryptingReader(io.RawIOBase):
    """Read an encrypted backup as if it were the plain zip, without decrypting it anywhere: any
    position can be read (zipfile needs to jump around), and each 1 MiB piece is checked and
    decrypted only when it's read. One piece is kept in memory at a time. Every piece is
    authenticated with its position and whether it is the last, and the last one is checked on
    opening, so a backup that was reordered, cut short, extended or changed is refused."""

    _STEP = 4 + CHUNK + 16  # one full piece on disk

    def __init__(self, path):
        super().__init__()
        self._f = open(path, "rb")
        try:
            self._head, self.header = _open_header(self._f)
            self._aes = AESGCM(_key_for(self.header))
            self._prefix = self._f.read(7)
            self._start = self._f.tell()
            body = os.fstat(self._f.fileno()).st_size - self._start
            if len(self._prefix) != 7 or body < 4 + 16:
                raise BackupKeyError("That backup is cut short.")
            self._pieces = -(-body // self._STEP)
            last_at = self._start + (self._pieces - 1) * self._STEP
            self._f.seek(last_at)
            (m,) = struct.unpack(">I", self._f.read(4))
            if last_at + 4 + m != self._start + body or not 16 <= m <= CHUNK + 16:
                raise BackupKeyError("That backup is cut short or has extra data after its end.")
            self._last_len = m
            self.size = (self._pieces - 1) * CHUNK + m - 16
            self._cache: tuple[int, bytes] | None = None
            self._piece(self._pieces - 1)  # the end is genuine: nothing was cut off or added
            self._pos = 0
        except Exception:
            self._f.close()
            raise

    def _piece(self, i: int) -> bytes:
        if self._cache and self._cache[0] == i:
            return self._cache[1]
        last = i == self._pieces - 1
        self._f.seek(self._start + i * self._STEP)
        raw = self._f.read(4)
        m = struct.unpack(">I", raw)[0] if len(raw) == 4 else -1
        if m != (self._last_len if last else CHUNK + 16):
            raise BackupKeyError("That backup was changed or damaged; it can't be restored.")
        ct = self._f.read(m)
        try:
            plain = self._aes.decrypt(_nonce(self._prefix, i, last), ct, self._head)
        except Exception:
            raise BackupKeyError("That backup was changed or damaged; it can't be restored.")
        self._cache = (i, plain)
        return plain

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._pos = base + offset
        return self._pos

    def readinto(self, b):
        want = min(len(b), self.size - self._pos)
        if want <= 0:
            return 0
        done = 0
        while done < want:
            i, off = divmod(self._pos, CHUNK)
            chunk = self._piece(i)[off:off + want - done]
            b[done:done + len(chunk)] = chunk
            done += len(chunk)
            self._pos += len(chunk)
        return done

    def close(self):
        if not self.closed:
            self._f.close()
            self._cache = None
        super().close()


def is_encrypted(path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == MAGIC


def read_header(path) -> dict:
    with open(path, "rb") as f:
        return _open_header(f)[1]


def decrypt(src_path, dst_path) -> dict:
    """Write the plain zip out (tests and tools; backups and restores read through DecryptingReader)."""
    with DecryptingReader(src_path) as r, open(dst_path, "wb") as out:
        while piece := r.read(CHUNK):
            out.write(piece)
        return r.header
