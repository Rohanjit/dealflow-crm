"""Security baseline (decision 63, phase S1): headers, passwords, sign-in lock-out, sessions,
invite links, Host header."""

import datetime as dt
import time

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import lockout
from accounts.models import Invite, PasswordHistory, Role, SignInFailure, User
from core.models import FirmSettings

from .base import APITestBase as _Base


class APITestBase(_Base):
    def setUp(self):
        super().setUp()
        cache.clear()  # rate limits kept by other tests in this process


def sign_in(email, password, client=None, ip="10.0.0.1"):
    c = client or APIClient(REMOTE_ADDR=ip)
    return c, c.post("/api/auth/login/", {"email": email, "password": password}, format="json", REMOTE_ADDR=ip)


class HeaderTests(APITestBase):
    def test_every_response_carries_the_headers(self):
        for path in ("/api/app/", "/"):
            r = APIClient().get(path)
            csp = r["Content-Security-Policy"]
            for part in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'", "object-src 'none'",
                         "base-uri 'self'", "form-action 'self'"):
                self.assertIn(part, csp)
            self.assertNotIn("unsafe-eval", csp)
            self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
            self.assertEqual(r["X-Frame-Options"], "DENY")
            self.assertEqual(r["X-Content-Type-Options"], "nosniff")
            self.assertEqual(r["Referrer-Policy"], "same-origin")
            self.assertEqual(r["Cross-Origin-Opener-Policy"], "same-origin")
            self.assertIn("camera=()", r["Permissions-Policy"])
        self.assertEqual(APIClient().get("/api/app/")["Cache-Control"], "no-store")

    @override_settings(ALLOWED_HOSTS=["crm.firm.com"])
    def test_unknown_host_is_refused(self):
        self.assertEqual(APIClient().get("/api/app/", HTTP_HOST="evil.example").status_code, 400)
        self.assertEqual(APIClient().get("/api/app/", HTTP_HOST="crm.firm.com").status_code, 200)


class PasswordTests(APITestBase):
    def change(self, current, new):
        return self.client.post("/api/auth/change-password/", {"current_password": current, "new_password": new},
                                format="json")

    def test_argon2_and_length(self):
        self.assertTrue(self.admin.password.startswith("argon2"))
        r = self.change(self.password, "Short-pw-1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("12 characters", str(r.json()))
        r = self.change(self.password, "x" * 300)
        self.assertEqual(r.status_code, 400)

    def test_no_reuse_of_the_last_twelve(self):
        pw = [f"Deal-room-key-{n:02d}" for n in range(14)]
        r = self.change(self.password, self.password)
        self.assertEqual(r.status_code, 400)
        self.assertIn("haven't used", str(r.json()))
        current = self.password
        for p in pw[:13]:
            r = self.change(current, p)
            self.assertEqual(r.status_code, 200, r.content)
            self.admin.refresh_from_db()
            self.client.force_authenticate(self.admin)
            current = p
        self.assertLessEqual(PasswordHistory.objects.filter(user=self.admin).count(), 12)
        self.assertEqual(self.change(current, pw[5]).status_code, 400)  # among the last 12
        self.assertEqual(self.change(current, pw[0]).status_code, 200)  # 13 changes ago: allowed again
        self.assertFalse(PasswordHistory.objects.filter(hash__contains="Deal-room").exists())

    def test_similar_to_email_refused(self):
        u = self.make_user("rajeshwari.krishnan@firm.com", self.admin_role, full_name="Rajeshwari Krishnan")
        c = self.client_for(u)
        r = c.post("/api/auth/change-password/", {"current_password": self.password,
                                                   "new_password": "rajeshwari.krishnan"}, format="json")
        self.assertEqual(r.status_code, 400)


class LockoutTests(APITestBase):
    def test_five_failures_lock_the_account_even_for_the_right_password(self):
        for _ in range(5):
            _, r = sign_in("admin@firm.com", "wrong-password-x")
            self.assertEqual(r.status_code, 400)
        _, r = sign_in("admin@firm.com", self.password, ip="10.9.9.9")  # another address: still locked
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "signin_locked")
        # After 30 minutes it opens again.
        SignInFailure.objects.update(locked_until=timezone.now() - dt.timedelta(seconds=1))
        _, r = sign_in("admin@firm.com", self.password)
        self.assertEqual(r.status_code, 200)

    def test_unknown_accounts_behave_the_same(self):
        _, a = sign_in("nobody@firm.com", "wrong-password-x")
        _, b = sign_in("admin@firm.com", "wrong-password-x")
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        for _ in range(4):
            sign_in("nobody@firm.com", "wrong-password-x")
        _, r = sign_in("nobody@firm.com", "wrong-password-x")
        self.assertEqual(r.json()["code"], "signin_locked")  # same lock-out, so no way to tell it doesn't exist
        self.assertFalse(SignInFailure.objects.filter(key__contains="nobody").exists())  # emails stored hashed

    def test_success_resets_and_address_limit(self):
        for _ in range(4):
            sign_in("admin@firm.com", "wrong-password-x")
        sign_in("admin@firm.com", self.password)
        _, r = sign_in("admin@firm.com", "wrong-password-x")
        self.assertEqual(r.status_code, 400)  # count started again
        for n in range(lockout.ADDRESS_LIMIT):
            sign_in(f"spray{n}@firm.com", "wrong-password-x", ip="10.6.6.6")
        _, r = sign_in("admin@firm.com", self.password, ip="10.6.6.6")
        self.assertEqual(r.status_code, 403)

    def test_admin_reset_lifts_the_lock(self):
        u = self.make_user("anu@firm.com", Role.objects.get(name="Member"))
        for _ in range(5):
            sign_in(u.email, "wrong-password-x")
        r = self.client.post(f"/api/users/{u.pk}/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        _, r = sign_in(u.email, r.json()["temp_password"])
        self.assertEqual(r.status_code, 200)


class SessionTests(APITestBase):
    def signed_in(self):
        c, r = sign_in("admin@firm.com", self.password)
        self.assertEqual(r.status_code, 200)
        return c

    def age(self, c, seen=None, started=None):
        s = c.session
        now = int(time.time())
        if seen is not None:
            s["df_seen"] = now - seen
        if started is not None:
            s["df_started"] = now - started
        s.save()

    def test_new_session_id_on_sign_in(self):
        c = APIClient()
        c.get("/api/app/")
        c.session.save()
        before = c.session.session_key
        sign_in("admin@firm.com", self.password, client=c)
        self.assertNotEqual(c.session.session_key, before)

    def test_idle_time_out(self):
        c = self.signed_in()
        self.age(c, seen=29 * 60)
        self.assertEqual(c.get("/api/auth/me/").status_code, 200)  # activity: the clock restarts
        self.age(c, seen=31 * 60)
        r = c.get("/api/auth/me/")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "session_expired")
        self.assertIsNone(c.get("/api/app/").json()["user"])

    def test_background_polling_is_not_activity(self):
        c = self.signed_in()
        self.age(c, seen=20 * 60)
        self.assertEqual(c.get("/api/auth/me/", HTTP_X_DEALFLOW_BACKGROUND="1").status_code, 200)
        self.age(c)  # unchanged
        self.assertLess(c.session["df_seen"], int(time.time()) - 19 * 60)

    def test_firm_idle_setting_and_absolute_limit(self):
        fs = FirmSettings.load()
        fs.idle_minutes = 120
        fs.save()
        c = self.signed_in()
        self.age(c, seen=60 * 60)
        self.assertEqual(c.get("/api/auth/me/").status_code, 200)
        self.age(c, started=12 * 3600 + 5)
        self.assertEqual(c.get("/api/auth/me/").status_code, 401)

    def test_security_settings(self):
        r = self.client.put("/api/settings/security/", {"idle_minutes": 60}, format="json")
        self.assertEqual(r.status_code, 400)  # password needed
        r = self.client.put("/api/settings/security/", {"idle_minutes": 5, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/settings/security/", {"idle_minutes": 60, "password": self.password}, format="json")
        self.assertEqual(r.json()["idle_minutes"], 60)
        member = self.make_user("m@firm.com", self.make_role("Settings", manage_settings=True))
        self.assertEqual(self.client_for(member).get("/api/settings/security/").status_code, 403)


class InviteTests(APITestBase):
    def test_link_secret_is_shown_once_and_stored_hashed(self):
        r = self.client.post("/api/invites/", {"role": Role.objects.get(name="Member").pk}, format="json")
        raw = r.json()["token"]
        i = Invite.objects.get()
        self.assertNotEqual(i.token, raw)
        self.assertEqual(len(i.token), 64)
        self.assertNotIn("token", self.client.get("/api/invites/").json()[0])
        self.assertLessEqual(i.expires_at - timezone.now(), dt.timedelta(hours=72))
        self.assertEqual(APIClient().get(f"/api/invites/accept/{i.token}/").status_code, 400)  # the hash is no key
        self.assertEqual(APIClient().get(f"/api/invites/accept/{raw}/").status_code, 200)
        body = {"full_name": "Neha", "email": "neha@firm.com", "password": "Long-enough-pass-1"}
        self.assertEqual(APIClient().post(f"/api/invites/accept/{raw}/", body, format="json").status_code, 201)
        body["email"] = "other@firm.com"
        self.assertEqual(APIClient().post(f"/api/invites/accept/{raw}/", body, format="json").status_code, 400)
        self.assertTrue(User.objects.get(email="neha@firm.com").password.startswith("argon2"))

    def test_expired(self):
        r = self.client.post("/api/invites/", {"role": Role.objects.get(name="Member").pk}, format="json")
        Invite.objects.update(expires_at=timezone.now() - dt.timedelta(minutes=1))
        self.assertEqual(APIClient().get(f"/api/invites/accept/{r.json()['token']}/").status_code, 400)
