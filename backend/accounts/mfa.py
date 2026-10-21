"""Two-step login (decision 63, S2): passkeys (WebAuthn, user verification required), authenticator
apps (TOTP, RFC 6238) and one-time recovery codes. Required for everyone; the only accounts
without it are the sample firm's Guest and a public demo site, which hold no real data.

Libraries: py_webauthn for passkeys, pyotp for codes, segno for the QR picture. Nothing here
invents cryptography: secrets come from `secrets`, the TOTP secret is sealed with Fernet."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .models import Passkey, RecoveryCode, TotpDevice

ISSUER = "Dealflow"
RECOVERY_COUNT = 10
PENDING = "mfa_pending"  # session: password checked, second step to come
CHALLENGE = "webauthn_challenge"
PENDING_SECONDS = 5 * 60


# ---------------------------------------------------------------- who needs it


def required() -> bool:
    return getattr(settings, "MFA_REQUIRED", True) and not settings.DEMO


def exempt(user) -> bool:
    from core import guest

    return not required() or guest.is_guest(user)


def has_mfa(user) -> bool:
    return Passkey.objects.filter(user=user).exists() or TotpDevice.objects.filter(user=user, confirmed=True).exists()


def needs_enrolment(user) -> bool:
    return bool(user and user.is_authenticated and not exempt(user) and not has_mfa(user))


def methods(user) -> list[str]:
    out = []
    if Passkey.objects.filter(user=user).exists():
        out.append("passkey")
    if TotpDevice.objects.filter(user=user, confirmed=True).exists():
        out.append("app")
    if RecoveryCode.objects.filter(user=user, used_at__isnull=True).exists():
        out.append("recovery")
    return out


def summary(user) -> dict:
    t = TotpDevice.objects.filter(user=user, confirmed=True).first()
    return {
        "required": not exempt(user),
        "enrolled": has_mfa(user),
        "app": bool(t),
        "app_since": t.created_at.isoformat() if t else None,
        "passkeys": [{"id": p.pk, "name": p.name, "created_at": p.created_at.isoformat(),
                      "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None, "synced": p.backed_up}
                     for p in Passkey.objects.filter(user=user)],
        "recovery_left": RecoveryCode.objects.filter(user=user, used_at__isnull=True).count(),
    }


# ---------------------------------------------------------------- sealing the TOTP secret


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"dealflow-mfa", info=b"totp secrets").derive(
        settings.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def _unseal(blob: str) -> str | None:
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ---------------------------------------------------------------- authenticator app


def totp_start(request, user) -> dict:
    """A new secret to scan. While a working app exists, the new one waits in the session until
    its first code is confirmed, so a half-finished change never locks anyone out."""
    secret = pyotp.random_base32(32)  # 160 bits
    if TotpDevice.objects.filter(user=user, confirmed=True).exists():
        request.session["totp_new"] = {"s": _seal(secret), "at": int(time.time())}
        replace = True
    else:
        TotpDevice.objects.update_or_create(user=user, defaults={"secret": _seal(secret), "confirmed": False,
                                                                 "last_step": 0})
        replace = False
    return {"secret": secret, "replace": replace, **_provisioning(user, secret)}


def _provisioning(user, secret: str) -> dict:
    import segno

    from core.models import FirmSettings

    issuer = f"{ISSUER} ({FirmSettings.load().firm_name})" if FirmSettings.load().firm_name else ISSUER
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer[:60])
    svg = segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#1f1f1f", light="#ffffff")
    return {"uri": uri, "qr_svg": svg}


def _step_ok(secret: str, code: str, last_step: int) -> int | None:
    """The time step the code belongs to (now, or 30 s either side), if it's new."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // 30
    for step in (now, now - 1, now + 1):
        if step > last_step and secrets.compare_digest(totp.at(step * 30), code):
            return step
    return None


@transaction.atomic
def totp_confirm(request, user, code: str) -> bool:
    """Confirm a new app with its first code."""
    new = request.session.get("totp_new")
    if new and int(time.time()) - new.get("at", 0) < 15 * 60:
        secret = _unseal(new["s"])
    else:
        dev = TotpDevice.objects.filter(user=user, confirmed=False).first()
        secret = _unseal(dev.secret) if dev else None
    if not secret:
        return False
    step = _step_ok(secret, code, 0)
    if step is None:
        return False
    TotpDevice.objects.update_or_create(user=user, defaults={"secret": _seal(secret), "confirmed": True,
                                                             "last_step": step})
    request.session.pop("totp_new", None)
    return True


@transaction.atomic
def totp_verify(user, code: str) -> bool:
    dev = TotpDevice.objects.select_for_update().filter(user=user, confirmed=True).first()
    if not dev:
        return False
    secret = _unseal(dev.secret)
    if not secret:
        return False
    step = _step_ok(secret, code, dev.last_step)
    if step is None:
        return False
    dev.last_step = step
    dev.save(update_fields=["last_step"])
    return True


# ---------------------------------------------------------------- recovery codes


def _format(raw: str) -> str:
    return f"{raw[:5]}-{raw[5:]}"


def new_recovery_codes(user) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_COUNT)]
    RecoveryCode.objects.filter(user=user).delete()
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, hash=make_password(c)) for c in codes])
    return [_format(c) for c in codes]


@transaction.atomic
def use_recovery_code(user, code: str) -> bool:
    raw = re.sub(r"[\s\-]", "", (code or "").lower())
    if not re.fullmatch(r"[a-z0-9]{10}", raw):
        return False
    for rc in RecoveryCode.objects.select_for_update().filter(user=user, used_at__isnull=True):
        if check_password(raw, rc.hash):
            rc.used_at = timezone.now()
            rc.save(update_fields=["used_at"])
            return True
    return False


# ---------------------------------------------------------------- passkeys


def rp(request) -> tuple[str, str]:
    """Relying party id (the host name) and the origin the browser must report. The host is one of
    ALLOWED_HOSTS, so it can't be chosen by an attacker."""
    host = request.get_host()
    rp_id = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return rp_id.strip("[]"), f"{request.scheme}://{host}"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def registration_options(request, user) -> dict:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)

    rp_id, _ = rp(request)
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=ISSUER, user_name=user.email, user_display_name=user.full_name or user.email,
        user_id=hashlib.sha256(f"dealflow-user:{user.pk}:{settings.SECRET_KEY[:16]}".encode()).digest(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb64(p.credential_id))
                             for p in Passkey.objects.filter(user=user)],
    )
    request.session[CHALLENGE] = {"c": _b64(opts.challenge), "at": int(time.time()), "kind": "register"}
    return json.loads(options_to_json(opts))


def _take_challenge(request, kind: str) -> bytes | None:
    c = request.session.pop(CHALLENGE, None)
    if not c or c.get("kind") != kind or int(time.time()) - c.get("at", 0) > 300:
        return None
    return _unb64(c["c"])


def register(request, user, credential, name: str) -> Passkey:
    from webauthn import verify_registration_response

    challenge = _take_challenge(request, "register")
    if challenge is None:
        raise ValueError("That took too long. Try adding the passkey again.")
    rp_id, origin = rp(request)
    v = verify_registration_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
                                     expected_origin=origin, require_user_verification=True)
    transports = []
    if isinstance(credential, dict):
        transports = [t for t in (credential.get("response") or {}).get("transports") or [] if isinstance(t, str)][:6]
    return Passkey.objects.create(
        user=user, credential_id=_b64(v.credential_id), public_key=_b64(v.credential_public_key),
        sign_count=v.sign_count, name=(name or "Passkey")[:80], transports=transports,
        backed_up=bool(getattr(v, "credential_backed_up", False)),
    )


def authentication_options(request, user=None) -> dict:
    """For the second step (the user's own passkeys) or passkey-only sign-in (any passkey)."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    rp_id, _ = rp(request)
    allow = [PublicKeyCredentialDescriptor(id=_unb64(p.credential_id)) for p in Passkey.objects.filter(user=user)] \
        if user is not None else []
    opts = generate_authentication_options(rp_id=rp_id, allow_credentials=allow,
                                           user_verification=UserVerificationRequirement.REQUIRED)
    request.session[CHALLENGE] = {"c": _b64(opts.challenge), "at": int(time.time()), "kind": "sign-in"}
    return json.loads(options_to_json(opts))


def authenticate_passkey(request, credential, user=None):
    """The user a passkey assertion proves (and must match `user` when given), or None."""
    from webauthn import verify_authentication_response

    challenge = _take_challenge(request, "sign-in")
    if challenge is None or not isinstance(credential, dict):
        return None
    cid = credential.get("rawId") or credential.get("id")
    if not isinstance(cid, str) or len(cid) > 1400:
        return None
    pk = Passkey.objects.select_related("user").filter(credential_id=cid.rstrip("=")).first()
    if pk is None or (user is not None and pk.user_id != user.pk):
        return None
    rp_id, origin = rp(request)
    try:
        v = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=_unb64(pk.public_key), credential_current_sign_count=pk.sign_count,
            require_user_verification=True)
    except Exception:  # every failure of the library means "not proven"
        return None
    pk.sign_count, pk.last_used_at = v.new_sign_count, timezone.now()
    pk.save(update_fields=["sign_count", "last_used_at"])
    return pk.user


def reset(user):
    """An admin's reset: every method and code goes; the person sets two-step login up again."""
    Passkey.objects.filter(user=user).delete()
    TotpDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
