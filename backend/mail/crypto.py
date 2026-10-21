"""Mailbox tokens and the app secrets are stored encrypted with a key derived from SECRET_KEY,
so a copied database (or a backup restored on another install) doesn't carry usable tokens."""

import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"dealflow-mail", info=b"mailbox tokens").derive(
        settings.SECRET_KEY.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value) -> str:
    if value in (None, "", {}):
        return ""
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def unseal(blob: str):
    """None when empty or when it can't be read (wrong install, tampered)."""
    if not blob:
        return None
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except (InvalidToken, ValueError):
        return None
