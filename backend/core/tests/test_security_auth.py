"""Security controls around accounts: password hashing and rules, sign-in lock-out, sessions, invites."""

import datetime as dt
import hashlib
import importlib
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import lockout
from accounts.models import Invite, PasswordHistory, Role, SignInLock, User, hash_token
from vault import pin as vault_pin

from .base import APITestBase


def sign_in(client, email, password):
    return client.post("/api/auth/login/", {"email": email, "password": password}, format="json")


class SecurityTestBase(APITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()  # the per-IP sign-in throttle lives in the cache


class PasswordStorageTests(SecurityTestBase):
    def test_new_passwords_use_argon2id(self):
        self.assertTrue(self.admin.password.startswith("argon2$argon2id$"))

    def test_old_pbkdf2_hash_is_upgraded_at_sign_in(self):
        User.objects.filter(pk=self.admin.pk).update(
            password=make_password(self.password, hasher="pbkdf2_sha256")
        )
        self.assertEqual(sign_in(APIClient(), "admin@firm.com", self.password).status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.password.startswith("argon2$argon2id$"))
        # An upgrade isn't a password change, so it doesn't take a history slot.
        self.assertEqual(PasswordHistory.objects.filter(user=self.admin).count(), 1)

    def test_vault_pin_hash_is_upgraded(self):
        User.objects.filter(pk=self.admin.pk).update(vault_pin=make_password("2580", hasher="pbkdf2_sha256"))
        self.admin.refresh_from_db()
        self.assertTrue(vault_pin.pin_matches(self.admin, "2580"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.vault_pin.startswith("argon2$argon2id$"))
        self.assertFalse(vault_pin.pin_matches(self.admin, "1470"))


class PasswordRuleTests(SecurityTestBase):
    def change(self, client, current, new):
        return client.post("/api/auth/change-password/", {"current_password": current, "new_password": new}, format="json")

    def test_minimum_length_is_twelve(self):
        r = self.change(self.client, self.password, "Short-pw-1!")  # 11 characters
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least 12", r.json()["new_password"])
        self.assertEqual(self.change(self.client, self.password, "Long-enough-pw-1").status_code, 200)

    def test_password_resembling_email_or_name_is_refused(self):
        member = Role.objects.get(name="Member")
        r = self.client.post("/api/invites/", {"role": member.pk}, format="json")
        token = r.json()["token"]
        r = APIClient().post(f"/api/invites/accept/{token}/", {
            "full_name": "Priyanka Raghunathan", "email": "priyanka.raghunathan@firm.com",
            "password": "priyanka.raghunathan"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("too similar", r.json()["password"])

    def test_last_twelve_passwords_cannot_be_reused(self):
        passwords = [f"Rotating-pass-{i:02d}" for i in range(13)]
        current = self.password
        for p in passwords[:12]:
            self.assertEqual(self.change(self.client, current, p).status_code, 200, p)
            current = p
        # History holds the current password and the 11 before it.
        for old in [passwords[0], passwords[3], passwords[10]]:
            r = self.change(self.client, current, old)
            self.assertEqual(r.status_code, 400)
            self.assertIn("used before", r.json()["new_password"])
        self.assertEqual(PasswordHistory.objects.filter(user=self.admin).count(), 12)
        # The password from 13 changes ago is allowed again.
        self.assertEqual(self.change(self.client, current, self.password).status_code, 200)
        # Only hashes are stored.
        for h in PasswordHistory.objects.values_list("password", flat=True):
            self.assertTrue(h.startswith("argon2$"))
            self.assertNotIn("Rotating", h)

    def test_admin_reset_cannot_reuse_current_password(self):
        u = self.make_user("ravi@firm.com", Role.objects.get(name="Member"))
        r = self.client.post(f"/api/users/{u.pk}/reset-password/", {"temp_password": self.password}, format="json")
        self.assertEqual(r.status_code, 400)


class LockoutTests(SecurityTestBase):
    wrong = "not-the-password"

    def fail_times(self, email, times, client=None):
        client = client or APIClient()
        return [sign_in(client, email, self.wrong) for _ in range(times)]

    def test_five_failures_lock_the_account(self):
        responses = self.fail_times("admin@firm.com", 5)
        self.assertEqual([r.status_code for r in responses], [400, 400, 400, 400, 429])
        self.assertEqual(responses[-1].json()["code"], "signin_locked")
        # The right password is refused while locked, from any device.
        r = sign_in(APIClient(), "ADMIN@firm.com ", self.password)
        self.assertEqual(r.status_code, 429)

    def test_unknown_email_gets_the_same_answers(self):
        known = self.fail_times("admin@firm.com", 6)
        cache.clear()  # separate the two runs from the per-IP rate limit
        unknown = self.fail_times("nobody@firm.com", 6)
        self.assertEqual([r.status_code for r in known], [r.status_code for r in unknown])
        for k, u in zip(known, unknown):
            self.assertEqual(k.json(), u.json())

    def test_guessing_across_many_emails_is_rate_limited_per_ip(self):
        client = APIClient()
        codes = [sign_in(client, f"user{i}@firm.com", self.wrong).status_code for i in range(11)]
        self.assertEqual(codes[:10], [400] * 10)
        self.assertEqual(codes[10], 429)

    def test_inactive_account_answers_like_a_wrong_password(self):
        u = self.make_user("gone@firm.com", self.admin_role, is_active=False)
        r = sign_in(APIClient(), u.email, self.password)
        self.assertEqual(r.json(), {"detail": "That email and password don't match."})

    def test_success_resets_the_count(self):
        self.fail_times("admin@firm.com", 4)
        self.assertEqual(sign_in(APIClient(), "admin@firm.com", self.password).status_code, 200)
        self.assertEqual([r.status_code for r in self.fail_times("admin@firm.com", 4)], [400] * 4)

    def test_lock_expires_after_thirty_minutes(self):
        self.fail_times("admin@firm.com", 5)
        later = timezone.now() + dt.timedelta(minutes=31)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(sign_in(APIClient(), "admin@firm.com", self.password).status_code, 200)

    def test_failures_far_apart_start_the_count_again(self):
        self.fail_times("admin@firm.com", 4)
        later = timezone.now() + dt.timedelta(minutes=45)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual([r.status_code for r in self.fail_times("admin@firm.com", 4)], [400] * 4)

    def test_admin_can_unlock(self):
        member = self.make_user("ravi@firm.com", Role.objects.get(name="Member"))
        self.fail_times(member.email, 5)
        users = {u["email"]: u for u in self.client.get("/api/users/").json()}
        self.assertIsNotNone(users["ravi@firm.com"]["signin_locked_until"])
        self.assertIsNone(users["admin@firm.com"]["signin_locked_until"])
        r = self.client.post(f"/api/users/{member.pk}/unlock/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["signin_locked_until"])
        self.assertEqual(sign_in(APIClient(), member.email, self.password).status_code, 200)

    def test_unlock_needs_manage_users_and_cannot_touch_admins(self):
        member_role = Role.objects.get(name="Member")
        member = self.make_user("ravi@firm.com", member_role)
        self.assertEqual(self.client_for(member).post(f"/api/users/{self.admin.pk}/unlock/").status_code, 403)
        ops = self.make_user("ops@firm.com", self.make_role("Ops", manage_users=True))
        self.assertEqual(self.client_for(ops).post(f"/api/users/{self.admin.pk}/unlock/").status_code, 403)
        self.assertEqual(self.client_for(ops).post(f"/api/users/{member.pk}/unlock/").status_code, 200)

    def test_password_reset_lifts_the_lock(self):
        member = self.make_user("ravi@firm.com", Role.objects.get(name="Member"))
        self.fail_times(member.email, 5)
        temp = self.client.post(f"/api/users/{member.pk}/reset-password/", {}, format="json").json()["temp_password"]
        self.assertEqual(sign_in(APIClient(), member.email, temp).status_code, 200)

    def test_wrong_current_password_counts_and_ends_the_session(self):
        c = APIClient()
        sign_in(c, "admin@firm.com", self.password)
        codes = [
            c.post("/api/auth/change-password/", {"current_password": "guess", "new_password": "Whatever-pass-99"},
                   format="json").status_code
            for _ in range(5)
        ]
        self.assertEqual(codes, [400, 400, 400, 400, 429])
        self.assertIn(c.get("/api/auth/me/").status_code, (401, 403))
        self.assertEqual(sign_in(APIClient(), "admin@firm.com", self.password).status_code, 429)

    def test_prune_keeps_live_locks(self):
        self.fail_times("admin@firm.com", 5)
        self.fail_times("someone@firm.com", 2)
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() + dt.timedelta(days=2)):
            lockout.prune()
        self.assertFalse(SignInLock.objects.exists())
        self.fail_times("someone@firm.com", 2)
        self.fail_times("admin@firm.com", 5)
        lockout.prune()
        self.assertEqual(SignInLock.objects.count(), 2)

    def test_lock_rows_are_left_out_of_backups(self):
        from core import backups
        self.assertIn("accounts.signinlock", backups.EXCLUDE)


class SessionTests(SecurityTestBase):
    def test_session_id_changes_at_sign_in(self):
        c = APIClient()
        c.get("/api/app/")
        c.cookies["sessionid"] = "planted-session-id"
        sign_in(c, "admin@firm.com", self.password)
        self.assertNotEqual(c.cookies["sessionid"].value, "planted-session-id")
        self.assertEqual(c.get("/api/auth/me/").status_code, 200)

    def test_sign_out_deletes_the_session_on_the_server(self):
        c = APIClient()
        sign_in(c, "admin@firm.com", self.password)
        key = c.cookies["sessionid"].value
        self.assertTrue(Session.objects.filter(session_key=key).exists())
        c.post("/api/auth/logout/")
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        # Replaying the old cookie gets nowhere.
        replay = APIClient()
        replay.cookies["sessionid"] = key
        self.assertIn(replay.get("/api/auth/me/").status_code, (401, 403))


class InviteTokenTests(SecurityTestBase):
    def test_only_a_hash_is_stored_and_the_link_is_shown_once(self):
        r = self.client.post("/api/invites/", {"role": Role.objects.get(name="Member").pk}, format="json")
        token = r.json()["token"]
        self.assertGreaterEqual(len(token), 43)
        invite = Invite.objects.get()
        self.assertEqual(invite.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, str(Invite.objects.values().get()))
        listed = self.client.get("/api/invites/").json()[0]
        self.assertNotIn("token", listed)
        self.assertNotIn("token_hash", listed)
        # The stored hash itself doesn't work as a link.
        self.assertEqual(APIClient().get(f"/api/invites/accept/{invite.token_hash}/").status_code, 400)
        self.assertEqual(APIClient().get(f"/api/invites/accept/{token}/").status_code, 200)

    def test_expired_invite_is_refused(self):
        r = self.client.post("/api/invites/", {"role": Role.objects.get(name="Member").pk}, format="json")
        Invite.objects.update(expires_at=timezone.now() - dt.timedelta(minutes=1))
        r = APIClient().post(f"/api/invites/accept/{r.json()['token']}/", {
            "full_name": "Late", "email": "late@firm.com", "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(email="late@firm.com").exists())

    def test_migration_hashes_existing_tokens(self):
        migration = importlib.import_module("accounts.migrations.0004_security_hardening")
        raw = "x" * 43
        invite, _ = Invite.issue(role=Role.objects.get(name="Member"))
        Invite.objects.filter(pk=invite.pk).update(token_hash=raw)  # as stored before the migration
        migration.hash_existing_tokens(django_apps, None)
        self.assertEqual(Invite.objects.get(pk=invite.pk).token_hash, hash_token(raw))
        migration.hash_existing_tokens(django_apps, None)  # running again changes nothing
        self.assertEqual(Invite.objects.get(pk=invite.pk).token_hash, hash_token(raw))
        self.assertEqual(Invite.by_token(raw), Invite.objects.get(pk=invite.pk))


@override_settings(ROOT_URLCONF="dealflow.urls")
class EndpointAccessTests(SecurityTestBase):
    """Every API endpoint refuses anonymous callers, except the few that must work before sign-in."""

    PUBLIC = {"app/", "setup/", "auth/login/", "auth/logout/", "invites/accept/<str:token>/"}

    def api_routes(self):
        from dealflow.urls import api
        return [str(p.pattern) for p in api]

    def fill(self, route):
        import re
        return "/api/" + re.sub(r"<(?:int|str|slug):(\w+)>", lambda m: "1" if "pk" in m.group(1) else "investors", route)

    def test_anonymous_callers_are_refused(self):
        anon = APIClient()
        for route in self.api_routes():
            if route in self.PUBLIC:
                continue
            url = self.fill(route)
            for method in ("get", "post", "patch", "delete"):
                r = getattr(anon, method)(url, {}, format="json")
                self.assertIn(r.status_code, (401, 403, 405), f"{method.upper()} {url} -> {r.status_code}")

    def test_public_list_is_exact(self):
        routes = set(self.api_routes())
        self.assertTrue(self.PUBLIC <= routes, self.PUBLIC - routes)

    def test_no_view_uses_allow_any(self):
        from django.urls import get_resolver
        from rest_framework.permissions import AllowAny
        from accounts.permissions import PublicEndpoint

        def views(resolver):
            for p in resolver.url_patterns:
                if hasattr(p, "url_patterns"):
                    yield from views(p)
                elif hasattr(p.callback, "cls"):
                    yield str(p.pattern), p.callback.cls

        for route, cls in views(get_resolver()):
            perms = cls.permission_classes
            self.assertNotIn(AllowAny, perms, route)
            if PublicEndpoint in perms:
                self.assertIn(route, self.PUBLIC, f"{route} is public but not on the list")
