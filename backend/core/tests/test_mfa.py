"""Two-step login (decision 63, S2): enrolment before anything else, authenticator apps, recovery
codes, passkeys (with a software authenticator), signed-in devices, admin reset."""

import base64
import datetime as dt
import hashlib
import json
import os
import struct
import time

import cbor2
import pyotp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from accounts import mfa
from accounts.models import Passkey, Role, TotpDevice, User, UserSession

from .base import APITestBase


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class SoftKey:
    """A passkey in software: ES256, "none" attestation, user verified."""

    def __init__(self, rp_id="testserver", origin="http://testserver"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred = os.urandom(32)
        self.rp_id, self.origin, self.count, self.user_handle = rp_id, origin, 0, b""

    def create(self, options, origin=None):
        self.user_handle = base64.urlsafe_b64decode(options["user"]["id"] + "==")
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"],
                             "origin": origin or self.origin, "crossOrigin": False}).encode()
        n = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        auth = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0) + b"\0" * 16
                + struct.pack(">H", len(self.cred)) + self.cred + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64(self.cred), "rawId": b64(self.cred), "type": "public-key",
                "response": {"clientDataJSON": b64(client), "attestationObject": b64(att), "transports": ["internal"]},
                "clientExtensionResults": {}}

    def get(self, options, origin=None, verified=True):
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"],
                             "origin": origin or self.origin, "crossOrigin": False}).encode()
        self.count += 1
        auth = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05 if verified else 0x01]) + struct.pack(">I", self.count)
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred), "rawId": b64(self.cred), "type": "public-key",
                "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth), "signature": b64(sig),
                             "userHandle": b64(self.user_handle)},
                "clientExtensionResults": {}}


def code_for(user, offset=0):
    secret = mfa._unseal(TotpDevice.objects.get(user=user).secret)
    return pyotp.TOTP(secret).at(int(time.time()) + offset)


@override_settings(MFA_REQUIRED=True)
class MfaBase(APITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.member = self.make_user("anu@firm.com", Role.objects.get(name="Member"), full_name="Anu Menon")

    def password_step(self, email="anu@firm.com", client=None):
        c = client or APIClient()
        r = c.post("/api/auth/login/", {"email": email, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return c, r.json()

    def enrol_app(self, user=None):
        user = user or self.member
        c, _ = self.password_step(user.email)
        r = c.post("/api/auth/mfa/app/start/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = c.post("/api/auth/mfa/app/confirm/", {"code": pyotp.TOTP(r.json()["secret"]).now()}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return c, r.json()


class EnrolmentTests(MfaBase):
    def test_nothing_works_until_two_step_is_set_up(self):
        c, me = self.password_step()
        self.assertTrue(me["mfa_enrol_required"])
        r = c.get("/api/m/investors/")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "mfa_enrol_required"))
        self.assertEqual(c.get("/api/auth/mfa/").status_code, 200)
        r = c.post("/api/auth/mfa/app/start/", {}, format="json")
        body = r.json()
        self.assertIn("otpauth://totp/", body["uri"])
        self.assertIn("<svg", body["qr_svg"])
        self.assertNotIn(body["secret"], TotpDevice.objects.get(user=self.member).secret)  # sealed
        self.assertEqual(c.post("/api/auth/mfa/app/confirm/", {"code": "000000"}, format="json").status_code, 400)
        r = c.post("/api/auth/mfa/app/confirm/", {"code": pyotp.TOTP(body["secret"]).now()}, format="json")
        codes = r.json()["recovery_codes"]
        self.assertEqual(len(codes), 10)
        self.assertEqual(c.get("/api/m/investors/").status_code, 200)

    def test_sample_guest_and_test_switch_are_the_only_exceptions(self):
        self.assertTrue(mfa.needs_enrolment(self.member))
        with override_settings(MFA_REQUIRED=False):
            self.assertFalse(mfa.needs_enrolment(self.member))


class SignInTests(MfaBase):
    def test_password_alone_signs_nothing_in(self):
        self.enrol_app()
        c, body = self.password_step()
        self.assertEqual(body, {"mfa_required": True, "methods": ["app", "recovery"]})
        self.assertIn(c.get("/api/auth/me/").status_code, (401, 403))
        self.assertIsNone(c.get("/api/app/").json()["user"])

    def test_app_code_once_only(self):
        self.enrol_app()
        c, _ = self.password_step()
        self.assertEqual(c.post("/api/auth/mfa/verify/", {"method": "app", "code": "123456"}, format="json").status_code, 400)
        code = code_for(self.member, 30)  # the next step, never used yet
        r = c.post("/api/auth/mfa/verify/", {"method": "app", "code": code}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(c.get("/api/m/investors/").status_code, 200)
        c2, _ = self.password_step()
        r = c2.post("/api/auth/mfa/verify/", {"method": "app", "code": code}, format="json")
        self.assertEqual(r.status_code, 400)  # replayed

    def test_wrong_codes_lock_the_account(self):
        self.enrol_app()
        c, _ = self.password_step()
        for _ in range(5):
            c.post("/api/auth/mfa/verify/", {"method": "app", "code": "111111"}, format="json")
        r = c.post("/api/auth/mfa/verify/", {"method": "app", "code": code_for(self.member, 30)}, format="json")
        self.assertEqual(r.json()["code"], "signin_locked")

    def test_recovery_code_works_once(self):
        _, body = self.enrol_app()
        code = body["recovery_codes"][0]
        c, _ = self.password_step()
        self.assertEqual(c.post("/api/auth/mfa/verify/", {"method": "recovery", "code": code.upper()}, format="json").status_code, 200)
        c2, _ = self.password_step()
        self.assertEqual(c2.post("/api/auth/mfa/verify/", {"method": "recovery", "code": code}, format="json").status_code, 400)

    def test_second_step_expires(self):
        self.enrol_app()
        c, _ = self.password_step()
        s = c.session
        s[mfa.PENDING] = {**s[mfa.PENDING], "at": int(time.time()) - 301}
        s.save()
        r = c.post("/api/auth/mfa/verify/", {"method": "app", "code": code_for(self.member, 30)}, format="json")
        self.assertEqual(r.json().get("code"), "mfa_expired")

    def test_no_second_step_without_a_password_first(self):
        self.enrol_app()
        r = APIClient().post("/api/auth/mfa/verify/", {"method": "app", "code": code_for(self.member, 30)}, format="json")
        self.assertEqual(r.status_code, 400)


class PasskeyTests(MfaBase):
    def add_passkey(self, c, key, password=None):
        body = {"password": password} if password else {}
        opts = c.post("/api/auth/mfa/passkeys/options/", body, format="json")
        self.assertEqual(opts.status_code, 200, opts.content)
        o = opts.json()
        self.assertEqual(o["authenticatorSelection"]["userVerification"], "required")
        r = c.post("/api/auth/mfa/passkeys/", {"credential": key.create(o), "name": "MacBook"}, format="json")
        return r

    def test_passkey_enrolment_second_step_and_passwordless(self):
        key = SoftKey()
        c, _ = self.password_step()
        r = self.add_passkey(c, key)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()["recovery_codes"]), 10)
        self.assertEqual(Passkey.objects.get().name, "MacBook")
        # As the second step
        c2, body = self.password_step()
        self.assertIn("passkey", body["methods"])
        o = c2.post("/api/auth/passkey/options/", {}, format="json").json()
        self.assertEqual(len(o["allowCredentials"]), 1)
        r = c2.post("/api/auth/passkey/sign-in/", {"credential": key.get(o)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # On its own ("Sign in with a passkey")
        c3 = APIClient()
        o = c3.post("/api/auth/passkey/options/", {}, format="json").json()
        self.assertEqual(o["allowCredentials"], [])
        r = c3.post("/api/auth/passkey/sign-in/", {"credential": key.get(o)}, format="json")
        self.assertEqual(r.json()["email"], "anu@firm.com")
        self.assertEqual(c3.get("/api/m/investors/").status_code, 200)
        self.assertEqual(Passkey.objects.get().sign_count, key.count)

    def test_forged_or_replayed_assertions_fail(self):
        key = SoftKey()
        c, _ = self.password_step()
        self.add_passkey(c, key)
        c2 = APIClient()
        o = c2.post("/api/auth/passkey/options/", {}, format="json").json()
        bad = key.get(o, origin="https://evil.example")
        self.assertEqual(c2.post("/api/auth/passkey/sign-in/", {"credential": bad}, format="json").status_code, 400)
        o = c2.post("/api/auth/passkey/options/", {}, format="json").json()
        no_uv = key.get(o, verified=False)
        self.assertEqual(c2.post("/api/auth/passkey/sign-in/", {"credential": no_uv}, format="json").status_code, 400)
        o = c2.post("/api/auth/passkey/options/", {}, format="json").json()
        good = key.get(o)
        self.assertEqual(c2.post("/api/auth/passkey/sign-in/", {"credential": good}, format="json").status_code, 200)
        c3 = APIClient()
        c3.post("/api/auth/passkey/options/", {}, format="json")
        self.assertEqual(c3.post("/api/auth/passkey/sign-in/", {"credential": good}, format="json").status_code, 400)

    def test_someone_elses_passkey_is_no_second_step(self):
        mine, theirs = SoftKey(), SoftKey()
        c, _ = self.password_step()
        self.add_passkey(c, mine)
        other = self.make_user("vik@firm.com", Role.objects.get(name="Member"))
        co, _ = self.password_step("vik@firm.com")
        self.add_passkey(co, theirs)
        c2, _ = self.password_step()
        o = c2.post("/api/auth/passkey/options/", {}, format="json").json()
        r = c2.post("/api/auth/passkey/sign-in/", {"credential": theirs.get(o)}, format="json")
        self.assertEqual(r.status_code, 400)
        del other

    def test_changes_need_the_password_and_never_leave_nothing(self):
        key = SoftKey()
        c, _ = self.password_step()
        self.add_passkey(c, key)
        r = c.post("/api/auth/mfa/passkeys/options/", {}, format="json")
        self.assertEqual(r.status_code, 400)  # no password
        self.assertEqual(self.add_passkey(c, SoftKey(), self.password).status_code, 201)
        ids = [p.pk for p in Passkey.objects.all()]
        self.assertEqual(c.delete(f"/api/auth/mfa/passkeys/{ids[0]}/", {"password": "wrong-one-x"}, format="json").status_code, 400)
        self.assertEqual(c.delete(f"/api/auth/mfa/passkeys/{ids[0]}/", {"password": self.password}, format="json").status_code, 200)
        r = c.delete(f"/api/auth/mfa/passkeys/{ids[1]}/", {"password": self.password}, format="json")
        self.assertEqual(r.status_code, 400)  # the last way in


class DeviceTests(MfaBase):
    def test_devices_list_end_one_and_end_others(self):
        a, _ = self.enrol_app()
        b, _ = self.password_step()
        b.post("/api/auth/mfa/verify/", {"method": "app", "code": code_for(self.member, 30)}, format="json")
        listed = a.get("/api/auth/sessions/").json()
        self.assertEqual(len(listed), 2)
        self.assertEqual(sum(s["current"] for s in listed), 1)
        other = next(s for s in listed if not s["current"])
        self.assertEqual(a.delete(f"/api/auth/sessions/{other['id']}/").status_code, 204)
        self.assertIn(b.get("/api/auth/me/").status_code, (401, 403))
        self.assertEqual(a.get("/api/auth/me/").status_code, 200)

    def test_new_browser_is_flagged(self):
        a, _ = self.enrol_app()
        c = APIClient(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) AppleWebKit Chrome/130.0 Safari/537.36")
        self.password_step(client=c)
        c.post("/api/auth/mfa/verify/", {"method": "app", "code": code_for(self.member, 30)}, format="json")
        alerts = a.get("/api/auth/security-alerts/").json()
        self.assertEqual([x["device"] for x in alerts], ["Chrome on Windows"])

    def test_admin_reset(self):
        for u in (self.admin,):
            TotpDevice.objects.create(user=u, secret=mfa._seal(pyotp.random_base32()), confirmed=True)
        a, _ = self.enrol_app()
        r = self.client.post(f"/api/users/{self.member.pk}/reset-mfa/", {"password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn(a.get("/api/auth/me/").status_code, (401, 403))  # signed out everywhere
        c, me = self.password_step()
        self.assertTrue(me["mfa_enrol_required"])
        manager = self.make_user("hr@firm.com", self.make_role("People", manage_users=True))
        TotpDevice.objects.create(user=manager, secret=mfa._seal(pyotp.random_base32()), confirmed=True)
        r = self.client_for(manager).post(f"/api/users/{self.admin.pk}/reset-mfa/", {"password": self.password}, format="json")
        self.assertEqual(r.status_code, 403)
