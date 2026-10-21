"""Virus scanning with ClamAV (decision 63, S5).

Every file someone uploads (Vault documents, email attachments, import files) is streamed to clamd
with INSTREAM before anything is stored. The scanner **fails closed**: if clamd can't be reached,
answers with an error, or couldn't check the whole file (size or archive limits), the upload is
refused. Vault files are checked again against the current signatures each time they're opened.

Only the standard library is used (a socket and clamd's documented protocol), so there's no extra
dependency to audit. `DEALFLOW_CLAMD` is "host:port" or a socket path.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import socket
import struct

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

log = logging.getLogger("dealflow.antivirus")

CHUNK = 64 * 1024
STALE_DAYS = 3  # signatures older than this are flagged on Settings > Security


class ScannerUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "scanner_unavailable"
    default_detail = ("Uploads are paused because the virus scanner isn't answering. Nothing was stored. "
                      "Try again in a few minutes, or ask your admin to check the scanner.")

    def __init__(self, detail=None, reason: str = ""):
        super().__init__({"detail": detail or self.default_detail, "code": "scanner_unavailable"})
        self.reason = reason


class Infected(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "infected"

    def __init__(self, name: str, signature: str, *, unchecked: bool = False):
        if unchecked:
            msg = (f"“{name}” couldn't be fully checked for viruses (it's too large or nested too deeply), "
                   "so it wasn't stored.")
        else:
            msg = f"“{name}” looks harmful ({signature}) and wasn't stored. Your admin has been told."
        super().__init__({"detail": msg, "code": "infected"})
        self.signature = signature


class _Failure(Exception):
    pass


# ---------------------------------------------------------------- settings


def mode() -> str:
    """"required" (the default everywhere real) or "off" (development servers and the test run)."""
    return getattr(settings, "ANTIVIRUS", "required")


def address() -> str:
    return getattr(settings, "CLAMD_ADDRESS", "") or ""


def _connect(timeout: float | None = None) -> socket.socket:
    addr = address()
    if not addr:
        raise _Failure("No scanner is set up (DEALFLOW_CLAMD).")
    timeout = timeout or getattr(settings, "CLAMD_TIMEOUT", 120)
    try:
        if addr.startswith("/"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(addr)
            return s
        host, _, port = addr.rpartition(":")
        return socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout)
    except (OSError, ValueError) as e:
        raise _Failure(f"Can't reach the scanner at {addr}: {e}") from e


def _read_reply(s: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\0"):
        part = s.recv(4096)
        if not part:
            break
        buf += part
        if len(buf) > 65536:
            break
    return buf.rstrip(b"\0").decode("utf-8", "replace").strip()


def _command(cmd: bytes, timeout: float = 5) -> str:
    s = _connect(timeout)
    try:
        s.sendall(b"z" + cmd + b"\0")
        return _read_reply(s)
    except OSError as e:
        raise _Failure(f"The scanner stopped answering: {e}") from e
    finally:
        s.close()


# ---------------------------------------------------------------- scanning


def _scan_chunks(chunks) -> str | None:
    """Stream to clamd. Returns the signature name when something is found, None when clean.
    Raises _Failure for anything that isn't a clear answer."""
    s = _connect()
    try:
        s.sendall(b"zINSTREAM\0")
        for chunk in chunks:
            for i in range(0, len(chunk), CHUNK):
                piece = chunk[i:i + CHUNK]
                s.sendall(struct.pack("!L", len(piece)) + piece)
        s.sendall(struct.pack("!L", 0))
        reply = _read_reply(s)
    except OSError as e:
        raise _Failure(f"The scanner stopped answering: {e}") from e
    finally:
        s.close()
    # "stream: OK" | "stream: <Signature> FOUND" | "INSTREAM size limit exceeded. ERROR" | "... ERROR"
    if reply.endswith(" FOUND"):
        return reply.removeprefix("stream:").strip().removesuffix(" FOUND").strip() or "Unknown"
    if reply == "stream: OK":
        return None
    if "size limit exceeded" in reply.lower():  # longer than clamd's StreamMaxLength: not checked, so refused
        return "Heuristics.Limits.Exceeded.StreamMaxLength"
    raise _Failure(f"The scanner answered “{reply[:200]}”.")


def scan_bytes(data: bytes) -> str | None:
    return _scan_chunks([data])


def _chunks_of(f):
    f.seek(0)
    if hasattr(f, "chunks"):
        yield from f.chunks(CHUNK * 16)
    else:
        while True:
            part = f.read(CHUNK * 16)
            if not part:
                break
            yield part
    f.seek(0)


def _unchecked(signature: str) -> bool:
    # With AlertExceedsMax (deploy/clamav/clamd.conf), files clamd couldn't fully check are reported as
    # Heuristics.Limits.Exceeded.*: refused, but described as "couldn't be checked" rather than harmful.
    return signature.startswith("Heuristics.Limits.Exceeded")


def _record(place: str, name: str, signature: str, user=None, sha256: str = "", document_id=None):
    from core import current
    from core.models import ScanRefusal

    ScanRefusal.objects.create(user=user if getattr(user, "pk", None) else None, place=place, file_name=name[:255],
                               signature=signature[:200], sha256=sha256, document_id=document_id)
    current.note(f"Virus scanner refused “{name}” ({signature})")
    log.warning("Refused a file (%s) %r: %s", place, name, signature)


def check_upload(f, *, name: str, place: str, user=None) -> None:
    """Scan an uploaded file before it's stored. Raises Infected or ScannerUnavailable."""
    if mode() == "off":
        return
    digest = hashlib.sha256()
    for part in _chunks_of(f):
        digest.update(part)
    try:
        found = _scan_chunks(_chunks_of(f))
    except _Failure as e:
        log.error("Upload refused, scanner unavailable: %s", e)
        raise ScannerUnavailable(reason=str(e)) from e
    if found:
        _record(place, name, found, user, digest.hexdigest())
        raise Infected(name, found, unchecked=_unchecked(found))


def check_stored(data: bytes, *, name: str, user=None, document_id=None) -> None:
    """Re-check a stored Vault file when it's opened. If the scanner is down the file is served (it was
    checked on upload); if it now matches a signature it's refused and admins are told."""
    if mode() == "off" or not address():
        return
    try:
        found = scan_bytes(data)
    except _Failure as e:
        log.warning("Couldn't re-check %r on opening: %s", name, e)
        return
    if found and not _unchecked(found):
        _record("vault_open", name, found, user, hashlib.sha256(data).hexdigest(), document_id)
        raise Infected(name, found)


# ---------------------------------------------------------------- status


def _parse_version(text: str) -> dict:
    # "ClamAV 1.4.6/28157/Sun Oct 18 06:24:14 2026"
    parts = text.split("/", 2)
    out = {"engine": parts[0].strip(), "signatures": None, "signatures_date": None}
    if len(parts) == 3:
        out["signatures"] = parts[1].strip()
        try:
            when = dt.datetime.strptime(" ".join(parts[2].split()), "%a %b %d %H:%M:%S %Y")
            out["signatures_date"] = when.date().isoformat()
        except ValueError:
            pass
    return out


def status_info() -> dict:
    from core.models import ScanRefusal

    info = {"mode": mode(), "configured": bool(address()), "available": False, "engine": None,
            "signatures": None, "signatures_date": None, "signatures_stale": False, "problem": None,
            "uploads_allowed": True}
    if address():
        try:
            if _command(b"PING") != "PONG":
                raise _Failure("The scanner didn't answer PING.")
            info.update(_parse_version(_command(b"VERSION")))
            info["available"] = True
        except _Failure as e:
            info["problem"] = str(e)
    elif mode() != "off":
        info["problem"] = "No scanner is set up (DEALFLOW_CLAMD)."
    if info["signatures_date"]:
        age = timezone.localdate() - dt.date.fromisoformat(info["signatures_date"])
        info["signatures_stale"] = age.days > STALE_DAYS
    info["uploads_allowed"] = mode() == "off" or info["available"]
    since = timezone.now() - dt.timedelta(days=30)
    info["refused_30_days"] = ScanRefusal.objects.filter(at__gte=since).count()
    info["recent"] = [
        {"id": r.id, "at": r.at.isoformat(), "place": r.get_place_display(), "file_name": r.file_name,
         "signature": r.signature, "user": (r.user.full_name or r.user.email) if r.user else None}
        for r in ScanRefusal.objects.select_related("user")[:10]
    ]
    return info


def uploads_allowed() -> bool:
    """Cheap check for screens that show "Uploads are paused" before someone picks a file."""
    if mode() == "off":
        return True
    try:
        return _command(b"PING", timeout=3) == "PONG"
    except _Failure:
        return False
