"""Encryption for data rooms. Keys are made from the install's master key (KEYS_DIR/master.key),
so they live apart from the data and go wherever the master key goes (backups of the keys folder).

- Room files: AES-256-GCM, a random nonce each, the file's SHA-256 and room file id as associated
  data. The server can read them (it must, to watermark each page for the investor reading it).
- Links: the token is 32 random bytes; only its SHA-256 is looked up. A sealed copy is kept so the
  banker can copy or resend the link (the link alone opens nothing without the emailed code).
- Codes: HMAC-SHA256 with their own key, so a database copy can't be used to try codes offline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"DFR1"


def _key(info: bytes) -> bytes:
    from dealflow import keys

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"dealflow-rooms", info=info).derive(keys.master())


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


def file_aad(sha256_hex: str) -> bytes:
    return f"dealflow-room-file:{sha256_hex}".encode()


def encrypt_file(data: bytes, sha256_hex: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(_key(b"room files")).encrypt(nonce, data, file_aad(sha256_hex))


def decrypt_file(blob: bytes, sha256_hex: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a data room file")
    return AESGCM(_key(b"room files")).decrypt(blob[4:16], blob[16:], file_aad(sha256_hex))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def seal_token(token: str, share_hint: str) -> dict:
    nonce = os.urandom(12)
    return {"n": _b64(nonce), "c": _b64(AESGCM(_key(b"room links")).encrypt(nonce, token.encode(), share_hint.encode()))}


def open_token(sealed: dict, share_hint: str) -> str:
    return AESGCM(_key(b"room links")).decrypt(_unb64(sealed["n"]), _unb64(sealed["c"]), share_hint.encode()).decode()


def code_hash(share_id: int, code: str) -> str:
    return hmac.new(_key(b"room codes"), f"{share_id}:{code}".encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
