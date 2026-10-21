"""The Vault's encryption (decision 63, S4). Standard pieces only, from `cryptography` and
`argon2-cffi`:

- Files: AES-256-GCM with the firm's Vault key, a random 96-bit nonce per file, and the file's
  SHA-256 and key id as associated data (a file can't be swapped for another or moved between keys).
- People: an X25519 key pair each. The Vault key is sealed to the public half (ephemeral X25519,
  HKDF-SHA256, AES-256-GCM). The private half is sealed with a key from Argon2id(PIN, salt) mixed by
  HKDF with the install's master key, so a stolen database alone can't even start guessing PINs.
- Recovery: the Vault key sealed with a key from a random 256-bit recovery key shown once to an admin.
- A person's open Vault: the Vault key sits in their session sealed with a random key that only their
  browser holds (an HttpOnly cookie), so the database alone never has it either.
"""

from __future__ import annotations

import base64
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

MAGIC = b"DFV1"  # stored files start with this, then the nonce, then ciphertext + tag


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


def _hkdf(material: bytes, info: bytes, salt: bytes = b"dealflow-vault") -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(material)


# ---------------------------------------------------------------- files


def file_aad(sha256_hex: str, key_id: int) -> bytes:
    return f"dealflow-vault-file:{key_id}:{sha256_hex}".encode()


def encrypt_file(data: bytes, key: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad)


def decrypt_file(blob: bytes, key: bytes, aad: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not an encrypted Vault file")
    return AESGCM(key).decrypt(blob[4:16], blob[16:], aad)


def is_encrypted(blob_head: bytes) -> bool:
    return blob_head.startswith(MAGIC)


# ---------------------------------------------------------------- PIN-sealed private keys


def _kdf_params() -> dict:
    # Tests use lighter settings (VAULT_KDF in settings); production: 3 passes over 64 MiB.
    return getattr(settings, "VAULT_KDF", None) or {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 1}


def pin_key(pin: str, salt: bytes, params: dict | None = None) -> bytes:
    from dealflow import keys

    p = params or _kdf_params()
    stretched = hash_secret_raw(pin.encode(), salt, time_cost=p["time_cost"], memory_cost=p["memory_cost"],
                                parallelism=p["parallelism"], hash_len=32, type=Type.ID)
    return _hkdf(stretched + keys.master(), b"pin key")


def new_keypair(pin: str) -> tuple[str, dict]:
    """(public key, sealed private key) for a person."""
    priv = X25519PrivateKey.generate()
    raw_priv = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64(pub), seal_private(raw_priv, pin)


def seal_private(raw_priv: bytes, pin: str) -> dict:
    salt, nonce, params = os.urandom(16), os.urandom(12), _kdf_params()
    ct = AESGCM(pin_key(pin, salt, params)).encrypt(nonce, raw_priv, b"dealflow-vault-private")
    return {"v": 1, "salt": b64(salt), "n": b64(nonce), "c": b64(ct), "kdf": params}


def open_private(sealed: dict, pin: str) -> bytes:
    key = pin_key(pin, unb64(sealed["salt"]), sealed.get("kdf"))
    return AESGCM(key).decrypt(unb64(sealed["n"]), unb64(sealed["c"]), b"dealflow-vault-private")


# ---------------------------------------------------------------- sealing the Vault key


def seal_to_public(public_b64: str, secret: bytes, aad: bytes) -> dict:
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(unb64(public_b64)))
    key = _hkdf(shared + eph_pub + unb64(public_b64), b"vault key to person")
    nonce = os.urandom(12)
    return {"v": 1, "e": b64(eph_pub), "n": b64(nonce), "c": b64(AESGCM(key).encrypt(nonce, secret, aad))}


def open_sealed(sealed: dict, raw_priv: bytes, aad: bytes) -> bytes:
    priv = X25519PrivateKey.from_private_bytes(raw_priv)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    eph_pub = unb64(sealed["e"])
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    key = _hkdf(shared + eph_pub + pub, b"vault key to person")
    return AESGCM(key).decrypt(unb64(sealed["n"]), unb64(sealed["c"]), aad)


def seal_with_secret(secret_key: bytes, payload: bytes, aad: bytes, info: bytes) -> dict:
    nonce = os.urandom(12)
    return {"v": 1, "n": b64(nonce), "c": b64(AESGCM(_hkdf(secret_key, info)).encrypt(nonce, payload, aad))}


def open_with_secret(secret_key: bytes, sealed: dict, aad: bytes, info: bytes) -> bytes:
    return AESGCM(_hkdf(secret_key, info)).decrypt(unb64(sealed["n"]), unb64(sealed["c"]), aad)


# ---------------------------------------------------------------- recovery key text


_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no look-alikes


def recovery_text(raw: bytes) -> str:
    """32 bytes as 52 characters in groups of four, easy to print and type."""
    n = int.from_bytes(raw, "big")
    chars = []
    for _ in range(52):
        n, r = divmod(n, len(_ALPHABET))
        chars.append(_ALPHABET[r])
    s = "".join(reversed(chars))
    return "-".join(s[i:i + 4] for i in range(0, 52, 4))


def recovery_bytes(text: str) -> bytes:
    s = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    if len(s) != 52 or any(ch not in _ALPHABET for ch in s):
        raise ValueError("That isn't a recovery key.")
    n = 0
    for ch in s:
        n = n * len(_ALPHABET) + _ALPHABET.index(ch)
    if n >= 1 << 256:
        raise ValueError("That isn't a recovery key.")
    return n.to_bytes(32, "big")
