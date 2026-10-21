"""Who holds the Vault key, and the key in use during a request (decision 63, S4).

- The first person to open the Vault after encryption arrives makes the firm's Vault key, and every
  existing file is encrypted then.
- Anyone else who sets a PIN gets a key pair; the Vault key is sealed to them the next time anyone
  who holds it uses the Vault (or at once by an admin from Settings > Security). Until then they see
  the list but can't open files ("Your access to Vault files is being set up").
- An admin can make a recovery key (shown once) for when everyone forgets their PIN, or when the
  data is restored on another install whose master key differs.
"""

from __future__ import annotations

import os
import secrets

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from . import crypto
from .models import Document, UserVaultKeypair, VaultKey, VaultKeyWrap

SESSION_KEY = "vault_key_sealed"
REQUEST_ATTR = "_df_vault_key"
BATCH = 50  # files encrypted per request when catching up on older files


def cookie_name() -> str:
    return "__Host-dealflow_vault" if settings.HTTPS else "dealflow_vault"


def _aad(kind: str, *parts) -> bytes:
    return ("dealflow-" + kind + ":" + ":".join(str(p) for p in parts)).encode()


def active_key() -> VaultKey | None:
    return VaultKey.objects.filter(active=True).order_by("-pk").first()


# ---------------------------------------------------------------- PIN changes


def on_pin_set(user, pin: str, old_pin: str | None = None):
    """A first PIN makes a key pair; a changed PIN re-seals the private half."""
    kp = UserVaultKeypair.objects.filter(user=user).first()
    if kp and old_pin is not None:
        try:
            raw = crypto.open_private(kp.private_sealed, old_pin)
        except Exception:
            raw = None
        if raw is not None:
            kp.private_sealed = crypto.seal_private(raw, pin)
            kp.save(update_fields=["private_sealed"])
            return
    pub, sealed = crypto.new_keypair(pin)
    UserVaultKeypair.objects.update_or_create(user=user, defaults={"public_key": pub, "private_sealed": sealed})
    VaultKeyWrap.objects.filter(user=user).delete()  # a new key pair needs a new grant


def on_pin_cleared(user):
    UserVaultKeypair.objects.filter(user=user).delete()
    VaultKeyWrap.objects.filter(user=user).delete()


# ---------------------------------------------------------------- opening


@transaction.atomic
def unlock(request, user, pin: str) -> bytes | None:
    """After a right PIN: the Vault key for this person (made if none exists yet), or None while
    their access waits for a colleague. The key goes into the session, sealed for this browser."""
    kp = UserVaultKeypair.objects.filter(user=user).first()
    if kp is None:
        on_pin_set(user, pin)
        kp = UserVaultKeypair.objects.get(user=user)
    try:
        raw_priv = crypto.open_private(kp.private_sealed, pin)
    except Exception:
        # The key pair can't be opened (the master key differs: restored elsewhere). Start again;
        # access then comes from a colleague or the recovery key.
        on_pin_set(user, pin)
        kp = UserVaultKeypair.objects.get(user=user)
        raw_priv = crypto.open_private(kp.private_sealed, pin)
    vk = VaultKey.objects.select_for_update().filter(active=True).order_by("-pk").first()
    if vk is None:
        if VaultKey.objects.exists():
            return None  # only retired keys: odd, wait for recovery
        vk = VaultKey.objects.create()
        key = secrets.token_bytes(32)
        VaultKeyWrap.objects.create(vault_key=vk, user=user, kind="user",
                                    sealed=crypto.seal_to_public(kp.public_key, key, _aad("vault-key", vk.pk, user.pk)))
    else:
        wrap = VaultKeyWrap.objects.filter(vault_key=vk, user=user, kind="user").first()
        if wrap is None:
            return None
        key = crypto.open_sealed(wrap.sealed, raw_priv, _aad("vault-key", vk.pk, user.pk))
    _remember(request, vk.pk, key)
    return key


def _remember(request, key_id: int, key: bytes):
    browser_key = secrets.token_bytes(32)
    nonce = os.urandom(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    request.session[SESSION_KEY] = {
        "id": key_id, "n": crypto.b64(nonce),
        "c": crypto.b64(AESGCM(browser_key).encrypt(nonce, key, _aad("session", key_id, request.user.pk))),
    }
    request._df_vault_cookie = crypto.b64(browser_key)  # set on the response by the view
    setattr(request, REQUEST_ATTR, (key_id, key))


def set_cookie(request, response):
    value = getattr(request, "_df_vault_cookie", None)
    if value:
        response.set_cookie(cookie_name(), value, httponly=True, secure=settings.HTTPS, samesite="Strict", path="/")


def forget(request, response=None):
    request.session.pop(SESSION_KEY, None)
    if response is not None:
        response.delete_cookie(cookie_name(), path="/", samesite="Strict")


def current(request) -> tuple[int, bytes] | None:
    """(key id, Vault key) for this request, or None."""
    if hasattr(request, REQUEST_ATTR):
        return getattr(request, REQUEST_ATTR)
    found = None
    sealed = request.session.get(SESSION_KEY)
    cookie = request.COOKIES.get(cookie_name())
    if sealed and cookie:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            key = AESGCM(crypto.unb64(cookie)).decrypt(crypto.unb64(sealed["n"]), crypto.unb64(sealed["c"]),
                                                     _aad("session", sealed["id"], request.user.pk))
            found = (sealed["id"], key)
        except Exception:
            found = None
    setattr(request, REQUEST_ATTR, found)
    return found


def require_key(request) -> tuple[int, bytes]:
    got = current(request)
    if got is None:
        if active_key() is not None and not VaultKeyWrap.objects.filter(user=request.user, kind="user").exists():
            raise PermissionDenied({
                "detail": "Your access to Vault files is being set up. It's ready as soon as a colleague with "
                          "Vault access opens the Vault, or an admin grants it in Settings > Security.",
                "code": "vault_waiting"})
        raise PermissionDenied({"detail": "The Vault is locked. Enter your PIN to open it.", "code": "vault_locked"})
    return got


# ---------------------------------------------------------------- housekeeping with the key in hand


def pending_people():
    """People with a key pair and Vault view access but no grant to the active key."""
    from accounts.models import User

    vk = active_key()
    if vk is None:
        return []
    granted = set(VaultKeyWrap.objects.filter(vault_key=vk, kind="user").values_list("user_id", flat=True))
    out = []
    for kp in UserVaultKeypair.objects.select_related("user__role").exclude(user_id__in=granted):
        u = kp.user
        if u.is_active and u.can("vault", "view"):
            out.append(kp)
    return out


def grant_pending(key_id: int, key: bytes) -> int:
    n = 0
    for kp in pending_people():
        VaultKeyWrap.objects.get_or_create(vault_key_id=key_id, user=kp.user, kind="user", defaults={
            "sealed": crypto.seal_to_public(kp.public_key, key, _aad("vault-key", key_id, kp.user_id))})
        n += 1
    return n


def catch_up(key_id: int, key: bytes, limit: int = BATCH) -> int:
    """Encrypt files stored before encryption (or restored from an older backup)."""
    n = 0
    for doc in Document.objects.filter(encrypted_with__isnull=True)[:limit]:
        try:
            with doc.file.open("rb") as fh:
                data = fh.read()
        except (FileNotFoundError, ValueError):
            continue
        if crypto.is_encrypted(data[:4]):
            continue
        write_encrypted(doc, data, key_id, key)
        n += 1
    return n


def write_encrypted(doc: Document, data: bytes, key_id: int, key: bytes):
    """Replace the stored file with its encrypted form, under the same name."""
    from django.core.files.storage import default_storage

    blob = crypto.encrypt_file(data, key, crypto.file_aad(doc.sha256, key_id))
    path = doc.file.path if hasattr(doc.file, "path") else None
    if path:
        tmp = path + ".enc"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    else:  # other storages
        name = doc.file.name
        default_storage.delete(name)
        default_storage.save(name, __import__("io").BytesIO(blob))
    Document.objects.filter(pk=doc.pk).update(encrypted_with_id=key_id)
    doc.encrypted_with_id = key_id


def on_use(request):
    """Every Vault request with the key in hand: grant colleagues waiting, encrypt older files."""
    got = current(request)
    if got is None:
        return
    key_id, key = got
    grant_pending(key_id, key)
    catch_up(key_id, key)


def read_document(request, doc: Document) -> bytes:
    with doc.file.open("rb") as fh:
        data = fh.read()
    if doc.encrypted_with_id is None and not crypto.is_encrypted(data[:4]):
        return data
    key_id, key = require_key(request)
    if doc.encrypted_with_id != key_id:
        raise PermissionDenied({"detail": "This file is locked with an older Vault key. Ask an admin to finish "
                                          "the key change in Settings > Security.", "code": "vault_key_old"})
    return crypto.decrypt_file(data, key, crypto.file_aad(doc.sha256, key_id))


# ---------------------------------------------------------------- recovery and rotation


def make_recovery(request) -> str:
    key_id, key = require_key(request)
    raw = secrets.token_bytes(32)
    VaultKeyWrap.objects.filter(kind="recovery").delete()
    VaultKeyWrap.objects.create(vault_key_id=key_id, user=None, kind="recovery",
                                sealed=crypto.seal_with_secret(raw, key, _aad("recovery", key_id), b"recovery"))
    return crypto.recovery_text(raw)


@transaction.atomic
def recover(request, user, recovery: str, pin: str) -> int:
    """Use the recovery key: the person (with a PIN) gets the Vault key again."""
    raw = crypto.recovery_bytes(recovery)
    wrap = VaultKeyWrap.objects.filter(kind="recovery").select_related("vault_key").first()
    if wrap is None:
        raise ValueError("No recovery key has been made for this Vault.")
    try:
        key = crypto.open_with_secret(raw, wrap.sealed, _aad("recovery", wrap.vault_key_id), b"recovery")
    except Exception:
        raise ValueError("That recovery key doesn't open this Vault.")
    kp = UserVaultKeypair.objects.filter(user=user).first()
    if kp is None:
        on_pin_set(user, pin)
        kp = UserVaultKeypair.objects.get(user=user)
    VaultKeyWrap.objects.filter(vault_key_id=wrap.vault_key_id, user=user, kind="user").delete()
    VaultKeyWrap.objects.create(vault_key_id=wrap.vault_key_id, user=user, kind="user",
                                sealed=crypto.seal_to_public(kp.public_key, key, _aad("vault-key", wrap.vault_key_id, user.pk)))
    _remember(request, wrap.vault_key_id, key)
    return wrap.vault_key_id


@transaction.atomic
def rotate(request) -> dict:
    """A new Vault key: every file re-encrypted, everyone who had the old key gets the new one. The
    recovery key has to be made again."""
    old_id, old = require_key(request)
    new_vk = VaultKey.objects.create()
    new = secrets.token_bytes(32)
    files = 0
    for doc in Document.objects.all():
        try:
            with doc.file.open("rb") as fh:
                data = fh.read()
        except (FileNotFoundError, ValueError):
            continue
        if doc.encrypted_with_id == old_id:
            data = crypto.decrypt_file(data, old, crypto.file_aad(doc.sha256, old_id))
        elif doc.encrypted_with_id is not None:
            continue  # an even older key: left for the admin to sort out with a backup
        write_encrypted(doc, data, new_vk.pk, new)
        files += 1
    people = 0
    for w in VaultKeyWrap.objects.filter(vault_key_id=old_id, kind="user").select_related("user"):
        kp = UserVaultKeypair.objects.filter(user_id=w.user_id).first()
        if kp:
            VaultKeyWrap.objects.create(vault_key=new_vk, user_id=w.user_id, kind="user",
                                        sealed=crypto.seal_to_public(kp.public_key, new, _aad("vault-key", new_vk.pk, w.user_id)))
            people += 1
    VaultKey.objects.filter(pk=old_id).update(active=False)
    VaultKeyWrap.objects.filter(vault_key_id=old_id).delete()
    _remember(request, new_vk.pk, new)
    return {"files": files, "people": people}


def summary() -> dict:
    vk = active_key()
    return {
        "encrypted": vk is not None,
        "files_waiting": Document.objects.filter(encrypted_with__isnull=True).count(),
        "people_with_access": VaultKeyWrap.objects.filter(vault_key=vk, kind="user").count() if vk else 0,
        "people_waiting": len(pending_people()),
        "has_recovery_key": VaultKeyWrap.objects.filter(kind="recovery", vault_key=vk).exists() if vk else False,
        "key_since": vk.created_at.isoformat() if vk else None,
    }
