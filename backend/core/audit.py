"""The audit log: every request to Dealflow, who made it, when (to the second) and what changed,
kept encrypted in append-only monthly files. It isn't stored in the database and can't be browsed
in the app; an admin fetches a date range from Settings with their password and the audit passphrase.

Encryption
  The log has its own key pair (X25519). Each entry is sealed for the public key: a one-off key
  pair per entry, a shared secret, HKDF, then AES-GCM over the compressed entry. The server can
  therefore add entries but not read them. The private key is stored encrypted with a key derived
  from the audit passphrase (scrypt), in AUDIT_DIR/key.json. Before an admin sets a passphrase the
  log uses a temporary key kept on the server; setting the passphrase re-seals those entries with
  the real key and deletes the temporary one.

Tamper evidence
  Every line carries the SHA-256 of the line before it (also bound into the encryption), so a
  deleted, edited or reordered line shows up as a break when the log is fetched.

Files
  AUDIT_DIR/YYYY-MM.log (one JSON line per entry, firm's local month; older months gzipped),
  HEAD (hash of the last line), key.json, temp-key.json, settings.json (how many years to keep),
  pruned.json (the last hash of months removed by the retention setting), failures.json.
  Restores and the guest sample never touch this folder; backups carry a copy (still encrypted).
"""

from __future__ import annotations

import base64
import datetime as dt
import fcntl
import gzip
import hashlib
import io
import json
import os
import secrets
import zlib
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.conf import settings
from django.utils import timezone

GENESIS = "0" * 64
MIN_PASSPHRASE = 12
LOCK_AFTER = 5  # wrong passphrases (per person) before fetching is locked
LOCK_MINUTES = 15
SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class AuditError(Exception):
    pass


def folder() -> Path:
    d = Path(settings.AUDIT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _raw_public(k: X25519PublicKey) -> bytes:
    return k.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(k: X25519PrivateKey) -> bytes:
    return k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def _key_id(pub: bytes) -> str:
    return hashlib.sha256(pub).hexdigest()[:12]


def _write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


@contextmanager
def _locked():
    """One writer at a time across gunicorn workers and the scheduler."""
    with open(folder() / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------- keys


def _passphrase_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, **SCRYPT).derive(passphrase.encode())


def has_passphrase() -> bool:
    return (folder() / "key.json").exists()


def _public_key() -> tuple[str, X25519PublicKey]:
    """The key new entries are sealed for: the real one, or the temporary one until a passphrase is set."""
    real = _read_json(folder() / "key.json")
    if real:
        pub = _unb64(real["public"])
        return _key_id(pub), X25519PublicKey.from_public_bytes(pub)
    temp = _read_json(folder() / "temp-key.json")
    if not temp:
        k = X25519PrivateKey.generate()
        temp = {"public": _b64(_raw_public(k.public_key())), "private": _b64(_raw_private(k)),
                "created_at": timezone.now().isoformat()}
        _write_json(folder() / "temp-key.json", temp)
    pub = _unb64(temp["public"])
    return _key_id(pub), X25519PublicKey.from_public_bytes(pub)


def _private_keys(passphrase: str | None) -> dict[str, X25519PrivateKey]:
    """Private keys by id: the real one (needs the passphrase) and the temporary one if present."""
    out = {}
    temp = _read_json(folder() / "temp-key.json")
    if temp:
        out[_key_id(_unb64(temp["public"]))] = X25519PrivateKey.from_private_bytes(_unb64(temp["private"]))
    real = _read_json(folder() / "key.json")
    if real and passphrase is not None:
        try:
            raw = AESGCM(_passphrase_key(passphrase, _unb64(real["salt"]))).decrypt(
                _unb64(real["nonce"]), _unb64(real["private"]), b"dealflow-audit-key")
        except InvalidTag:
            raise AuditError("That isn't the audit passphrase.")
        out[_key_id(_unb64(real["public"]))] = X25519PrivateKey.from_private_bytes(raw)
    return out


def _store_real_key(k: X25519PrivateKey, passphrase: str):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    sealed = AESGCM(_passphrase_key(passphrase, salt)).encrypt(nonce, _raw_private(k), b"dealflow-audit-key")
    _write_json(folder() / "key.json", {
        "version": 1, "public": _b64(_raw_public(k.public_key())), "private": _b64(sealed),
        "salt": _b64(salt), "nonce": _b64(nonce), "kdf": {"name": "scrypt", **SCRYPT},
        "created_at": timezone.now().isoformat(),
    })


def check_passphrase_strength(p: str) -> str | None:
    if len(p) < MIN_PASSPHRASE:
        return f"Use at least {MIN_PASSPHRASE} characters. A few unrelated words work well."
    if len(set(p)) < 5:
        return "Use a less repetitive passphrase."
    return None


# ---------------------------------------------------------------- sealing entries


def _hkdf(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"dealflow-audit-entry").derive(shared)


def _seal(entry: dict, prev: str, kid: str, pub: X25519PublicKey) -> str:
    eph = X25519PrivateKey.generate()
    key = _hkdf(eph.exchange(pub))
    nonce = secrets.token_bytes(12)
    body = zlib.compress(json.dumps(entry, ensure_ascii=False, default=str).encode())
    ct = AESGCM(key).encrypt(nonce, body, prev.encode())
    return json.dumps({"k": kid, "p": prev, "e": _b64(_raw_public(eph.public_key())), "n": _b64(nonce), "c": _b64(ct)},
                      separators=(",", ":"))


def _open(line: dict, keys: dict[str, X25519PrivateKey]) -> dict | None:
    k = keys.get(line["k"])
    if k is None:
        return None
    key = _hkdf(k.exchange(X25519PublicKey.from_public_bytes(_unb64(line["e"]))))
    body = AESGCM(key).decrypt(_unb64(line["n"]), _unb64(line["c"]), line["p"].encode())
    return json.loads(zlib.decompress(body))


def _hash(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def _month_file(when: dt.datetime) -> Path:
    return folder() / f"{timezone.localtime(when):%Y-%m}.log"


def append(entry: dict):
    """Add one entry to the log. Never raises: auditing must not break the app (failures are kept
    in AUDIT_DIR/errors.txt for the admin)."""
    try:
        when = timezone.now()
        entry = {"t": timezone.localtime(when).isoformat(timespec="milliseconds"), **entry}
        with _locked():
            head = folder() / "HEAD"
            prev = head.read_text().strip() if head.exists() else GENESIS
            kid, pub = _public_key()
            line = _seal(entry, prev, kid, pub)
            path = _month_file(when)
            new = not path.exists()
            with open(path, "a") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            if new:
                os.chmod(path, 0o600)
            head.write_text(_hash(line))
            if new:
                os.chmod(head, 0o600)
    except Exception as e:  # pragma: no cover - logged for the admin, never shown to the user
        try:
            with open(folder() / "errors.txt", "a") as fh:
                fh.write(f"{timezone.now().isoformat()} {type(e).__name__}: {e}\n")
        except OSError:
            pass


# ---------------------------------------------------------------- reading


def _months() -> list[Path]:
    files = {}
    for p in folder().glob("????-??.log*"):
        files.setdefault(p.name[:7], []).append(p)
    out = []
    for month in sorted(files):
        group = files[month]
        out.append(next((p for p in group if p.suffix == ".log"), group[0]))
    return out


def _lines(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if raw:
                yield raw


def read(passphrase: str, start: dt.date, end: dt.date) -> tuple[list[dict], list[str]]:
    """Entries from `start` to `end` (inclusive, firm's days), and any problems found in the chain."""
    keys = _private_keys(passphrase)
    problems: list[str] = []
    pruned = _read_json(folder() / "pruned.json") or {}
    prev = pruned.get("last_hash", GENESIS)
    out = []
    for path in _months():
        month = path.name[:7]
        for n, raw in enumerate(_lines(path), start=1):
            try:
                line = json.loads(raw)
            except ValueError:
                problems.append(f"{month}, line {n}: unreadable line.")
                continue
            if line.get("p") != prev:
                problems.append(f"{month}, line {n}: the line before it is missing or was changed.")
            prev = _hash(raw)
            if not (f"{start:%Y-%m}" <= month <= f"{end:%Y-%m}"):
                continue
            try:
                entry = _open(line, keys)
            except (InvalidTag, ValueError, zlib.error):
                problems.append(f"{month}, line {n}: this entry was altered and can't be read.")
                continue
            if entry is None:
                problems.append(f"{month}, line {n}: sealed with an older key that's been replaced.")
                continue
            day = entry["t"][:10]
            if start.isoformat() <= day <= end.isoformat():
                out.append(entry)
    head = folder() / "HEAD"
    if head.exists() and head.read_text().strip() != prev:
        problems.append("The newest entries are missing: the log ends earlier than it should.")
    return out, problems


# ---------------------------------------------------------------- passphrase and key changes


def set_passphrase(passphrase: str, current: str | None = None):
    """First passphrase, or a new one (needs the current one). The first one also re-seals the
    entries written with the temporary key."""
    if msg := check_passphrase_strength(passphrase):
        raise AuditError(msg)
    with _locked():
        if has_passphrase():
            keys = _private_keys(current or "")
            real = _read_json(folder() / "key.json")
            k = keys[_key_id(_unb64(real["public"]))]
            _store_real_key(k, passphrase)  # same key pair, new passphrase: nothing to re-seal
            return
        k = X25519PrivateKey.generate()
        _store_real_key(k, passphrase)
        _reseal_temporary(k)


def _reseal_temporary(k: X25519PrivateKey):
    """Re-seal every entry written with the temporary key for the real key, rebuilding the chain,
    then delete the temporary key."""
    temp = _read_json(folder() / "temp-key.json")
    if not temp:
        return
    old = {_key_id(_unb64(temp["public"])): X25519PrivateKey.from_private_bytes(_unb64(temp["private"]))}
    kid, pub = _key_id(_raw_public(k.public_key())), k.public_key()
    prev = (_read_json(folder() / "pruned.json") or {}).get("last_hash", GENESIS)
    for path in _months():
        out = []
        for raw in _lines(path):
            line = json.loads(raw)
            entry = _open(line, old) if line["k"] in old else None
            new = _seal(entry, prev, kid, pub) if entry is not None else json.dumps({**line, "p": prev}, separators=(",", ":"))
            out.append(new)
            prev = _hash(new)
        data = ("\n".join(out) + "\n") if out else ""
        if path.suffix == ".gz":
            with gzip.open(path.with_suffix(".tmp"), "wt") as fh:
                fh.write(data)
        else:
            path.with_suffix(".tmp").write_text(data)
        os.chmod(path.with_suffix(".tmp"), 0o600)
        path.with_suffix(".tmp").replace(path)
    (folder() / "HEAD").write_text(prev)
    (folder() / "temp-key.json").unlink(missing_ok=True)


def new_key(passphrase: str):
    """The passphrase is lost: start a new key. Entries sealed with the old key stay in place but
    can never be read again."""
    if msg := check_passphrase_strength(passphrase):
        raise AuditError(msg)
    with _locked():
        old = folder() / "key.json"
        if old.exists():
            old.replace(folder() / f"key-retired-{timezone.now():%Y%m%d%H%M%S}.json")
        _store_real_key(X25519PrivateKey.generate(), passphrase)


# ---------------------------------------------------------------- wrong passphrases


def locked_until(user_id: int) -> dt.datetime | None:
    f = (_read_json(folder() / "failures.json") or {}).get(str(user_id))
    if not f or f["count"] < LOCK_AFTER:
        return None
    until = dt.datetime.fromisoformat(f["at"]) + dt.timedelta(minutes=LOCK_MINUTES)
    return until if until > timezone.now() else None


def record_failure(user_id: int):
    with _locked():
        data = _read_json(folder() / "failures.json") or {}
        f = data.get(str(user_id))
        if f and dt.datetime.fromisoformat(f["at"]) + dt.timedelta(minutes=LOCK_MINUTES) < timezone.now():
            f = None
        data[str(user_id)] = {"count": (f["count"] if f else 0) + 1, "at": timezone.now().isoformat()}
        _write_json(folder() / "failures.json", data)


def clear_failures(user_id: int):
    with _locked():
        data = _read_json(folder() / "failures.json") or {}
        if data.pop(str(user_id), None) is not None:
            _write_json(folder() / "failures.json", data)


# ---------------------------------------------------------------- keeping and pruning


def keep_years() -> int | None:
    return (_read_json(folder() / "settings.json") or {}).get("keep_years")


def set_keep_years(years: int | None):
    with _locked():
        data = _read_json(folder() / "settings.json") or {}
        data["keep_years"] = years
        _write_json(folder() / "settings.json", data)


def housekeeping(today: dt.date | None = None) -> list[str]:
    """Daily (scheduler): compress finished months, remove months older than the kept years.
    Returns what it did, for the log."""
    today = today or timezone.localdate()
    done = []
    this_month = f"{today:%Y-%m}"
    with _locked():
        for path in _months():
            if path.suffix == ".log" and path.name[:7] < this_month:
                gz = path.with_suffix(".log.gz")
                with open(path, "rb") as src, gzip.open(gz, "wb") as dst:
                    dst.write(src.read())
                os.chmod(gz, 0o600)
                path.unlink()
        years = keep_years()
        if years:
            cutoff = f"{today.year - years:04d}-{today:%m}"
            for path in _months():
                month = path.name[:7]
                if month < cutoff:
                    last = None
                    for raw in _lines(path):
                        last = _hash(raw)
                    if last:
                        _write_json(folder() / "pruned.json", {"last_hash": last, "through": month})
                    path.unlink()
                    done.append(f"Removed the audit log for {month} (older than {years} years).")
    return done


def status() -> dict:
    months = _months()
    size = sum(p.stat().st_size for p in folder().glob("????-??.log*"))
    temp = _read_json(folder() / "temp-key.json")
    errors = folder() / "errors.txt"
    return {
        "has_passphrase": has_passphrase(),
        "since": months[0].name[:7] if months else None,
        "months": len(months),
        "size_bytes": size,
        "keep_years": keep_years(),
        "temporary_since": temp["created_at"] if temp and not has_passphrase() else None,
        "write_errors": errors.exists() and errors.stat().st_size > 0,
    }


def backup_files() -> list[Path]:
    """What backups carry (all still encrypted)."""
    return [p for p in folder().glob("*") if p.is_file() and not p.name.startswith(".") and p.name != "temp-key.json"]


def export_xlsx(entries: list[dict], problems: list[str], start: dt.date, end: dt.date, who: str) -> bytes:
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Audit log")
    cols = ["Date", "Time", "Person", "Email", "Action", "Changes", "Request", "Result", "IP address", "Details"]

    def row(values):
        cells = []
        for v in values:
            from openpyxl.cell import WriteOnlyCell

            c = WriteOnlyCell(ws, value=v)
            if isinstance(v, str):
                c.data_type = "s"  # always text, never a formula
            cells.append(c)
        ws.append(cells)

    row(cols)
    for e in entries:
        u = e.get("u") or {}
        changes = "; ".join(
            f"{c.get('title') or c.get('record')}: " + ", ".join(
                f"{x['field']} {x['from'] or '(empty)'} → {x['to'] or '(empty)'}" for x in c.get("changes", [])
            ) if c.get("changes") else (c.get("title") or c.get("record") or "")
            for c in e.get("ch", [])
        )
        row([e["t"][:10], e["t"][11:23], u.get("name", "(not signed in)" if not u else ""), u.get("email", ""),
             e.get("a", ""), changes, f"{e.get('m', '')} {e.get('path', '')}{'?' + e['q'] if e.get('q') else ''}",
             str(e.get("s", "")), e.get("ip", ""), json.dumps(e.get("b"), ensure_ascii=False) if e.get("b") else ""])
    info = wb.create_sheet("About this file")
    for line in [f"Audit log from {start} to {end}", f"Fetched by {who} on {timezone.localtime():%Y-%m-%d %H:%M:%S}",
                 f"{len(entries)} entries", "Chain check: " + ("no problems" if not problems else f"{len(problems)} problems"), *problems]:
        info.append([line])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
