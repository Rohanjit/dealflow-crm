"""Guest mode: sample data before set-up, "Set up for real", and the demo site."""

from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role, User
from core import guest, sample
from core.models import ChoiceList, FirmSettings
from deals.models import Deal, DealInvestor
from investors.models import Investor
from tasks.models import Task
from vault.models import Document

from .base import DataDirMixin


class GuestModeTests(DataDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def start(self, client=None):
        client = client or APIClient()
        return client, client.post("/api/guest/start/", {}, format="json")

    def test_fresh_install_offers_guest_mode(self):
        info = APIClient().get("/api/app/").json()
        self.assertTrue(info["needs_setup"])
        self.assertTrue(info["guest_available"])
        self.assertFalse(info["sample"])

    def test_look_around_loads_sample_and_signs_in(self):
        c, r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["email"], sample.GUEST_EMAIL)
        self.assertTrue(r.json()["role"]["is_admin"])
        info = c.get("/api/app/").json()
        self.assertTrue(info["sample"] and info["is_guest"])
        self.assertEqual(info["firm_name"], sample.FIRM_NAME)
        # Every module has something to look at.
        self.assertGreaterEqual(Investor.objects.count(), 8)
        self.assertGreaterEqual(Deal.objects.count(), 4)
        self.assertTrue(DealInvestor.objects.exists())
        self.assertTrue(Task.objects.filter(assigned_to__email=sample.GUEST_EMAIL).exists())
        self.assertEqual(Document.objects.count(), 5)
        doc = Document.objects.first()
        self.assertTrue(Path(doc.file.path).read_bytes().startswith(b"%PDF"))
        self.assertEqual(c.get("/api/m/deals/").status_code, 200)
        self.assertGreater(c.get("/api/tasks/alerts/").json()["count"], 0)
        # The Vault opens with the sample PIN.
        self.assertEqual(c.get("/api/vault/status/").json()["sample_pin"], sample.GUEST_PIN)
        self.assertEqual(c.post("/api/vault/unlock/", {"pin": sample.GUEST_PIN}, format="json").status_code, 200)
        self.assertEqual(c.get("/api/m/vault/").status_code, 200)

    def test_second_visitor_reuses_the_sample(self):
        self.start()
        count = Investor.objects.count()
        _, r = self.start()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Investor.objects.count(), count)
        self.assertEqual(User.objects.filter(email=sample.GUEST_EMAIL).count(), 1)

    def test_sample_users_cannot_sign_in_with_a_password(self):
        self.start()
        for email in (sample.GUEST_EMAIL, "asha@example.com"):
            r = APIClient().post("/api/auth/login/", {"email": email, "password": ""}, format="json")
            self.assertEqual(r.status_code, 400)

    def test_people_and_safety_actions_are_refused_in_the_sample(self):
        c, _ = self.start()
        member = Role.objects.get(name="Member")
        asha = User.objects.get(email="asha@example.com")
        refused = [
            c.post("/api/users/", {"full_name": "X", "email": "x@firm.com", "role": member.pk}, format="json"),
            c.patch(f"/api/users/{asha.pk}/", {"is_active": False}, format="json"),
            c.post(f"/api/users/{asha.pk}/reset-password/", {}, format="json"),
            c.post("/api/invites/", {"role": member.pk}, format="json"),
            c.post("/api/roles/", {"name": "Analyst"}, format="json"),
            c.patch(f"/api/roles/{member.pk}/", {"name": "Team"}, format="json"),
            c.post("/api/backups/", {}, format="json"),
            c.post("/api/backups/restore/", {}, format="multipart"),
            c.patch("/api/settings/firm/", {"backups_enabled": True}, format="json"),
            c.post(f"/api/users/{asha.pk}/reset-vault-pin/", {}, format="json"),
        ]
        for r in refused:
            self.assertEqual(r.status_code, 403, r.content)
        # Looking is fine, and so is working with the sample records.
        self.assertEqual(c.get("/api/users/").status_code, 200)
        self.assertEqual(c.post("/api/m/investors/", {"firm_name": "My test fund"}, format="json").status_code, 201)

    def test_setup_is_refused_while_the_sample_is_loaded(self):
        self.start()
        r = APIClient().post("/api/setup/", {"firm_name": "Real", "full_name": "S", "email": "s@real.com",
                                             "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_set_up_for_real_restores_the_empty_install(self):
        lists_before = ChoiceList.objects.count()
        roles_before = set(Role.objects.values_list("name", flat=True))
        c, _ = self.start()
        c.post("/api/m/investors/", {"firm_name": "Guest added this"}, format="json")
        files = [Path(d.file.path) for d in Document.objects.all()]
        r = c.post("/api/guest/leave/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Investor.objects.exists())
        self.assertFalse(Document.objects.exists())
        self.assertTrue(all(not p.exists() for p in files))
        self.assertEqual(ChoiceList.objects.count(), lists_before)
        self.assertEqual(set(Role.objects.values_list("name", flat=True)), roles_before)
        fs = FirmSettings.load()
        self.assertIsNone(fs.sample_loaded_at)
        self.assertEqual(fs.firm_name, "")
        # Signed out, back at set-up, which now works.
        self.assertIn(c.get("/api/auth/me/").status_code, (401, 403))
        self.assertTrue(APIClient().get("/api/app/").json()["needs_setup"])
        r = APIClient().post("/api/setup/", {"firm_name": "Real Advisors", "full_name": "Shobhit", "email": "s@real.com",
                                             "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_guest_mode_is_gone_after_real_setup(self):
        APIClient().post("/api/setup/", {"firm_name": "Real", "full_name": "S", "email": "s@real.com",
                                         "password": "Str0ng-pass-123"}, format="json")
        self.assertFalse(APIClient().get("/api/app/").json()["guest_available"])
        _, r = self.start()
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Investor.objects.exists())

    def test_only_the_guest_can_leave_the_sample(self):
        self.start()
        asha = User.objects.get(email="asha@example.com")
        c = APIClient()
        c.force_authenticate(asha)
        self.assertEqual(c.post("/api/guest/leave/", {}, format="json").status_code, 403)
        self.assertEqual(APIClient().post("/api/guest/leave/", {}, format="json").status_code, 403)
        self.assertTrue(Investor.objects.exists())

    def test_just_me_setup(self):
        r = APIClient().post("/api/setup/", {"workspace_kind": "individual", "firm_name": "", "full_name": "Shobhit",
                                             "email": "s@me.com", "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        fs = FirmSettings.load()
        self.assertEqual((fs.workspace_kind, fs.firm_name), ("individual", ""))
        # A firm still needs its name.
        FirmSettings.objects.all().delete()
        User.objects.all().delete()
        r = APIClient().post("/api/setup/", {"workspace_kind": "firm", "firm_name": "", "full_name": "S",
                                             "email": "s@firm.com", "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 400)


@override_settings(DEMO=True)
class DemoSiteTests(DataDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_demo_site(self):
        info = APIClient().get("/api/app/").json()
        self.assertTrue(info["demo"] and info["guest_available"])
        self.assertFalse(info["needs_setup"])
        r = APIClient().post("/api/setup/", {"firm_name": "X", "full_name": "Y", "email": "y@x.com",
                                             "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 403)
        c = APIClient()
        self.assertEqual(c.post("/api/guest/start/", {}, format="json").status_code, 200)
        self.assertEqual(c.post("/api/guest/leave/", {}, format="json").status_code, 403)
        self.assertEqual(c.patch("/api/settings/firm/", {"firm_name": "Vandalised"}, format="json").status_code, 403)

    def test_daily_reload(self):
        guest.reload_demo()
        Investor.objects.create(firm_name="Added by a guest")
        self.assertFalse(guest.demo_reload_due())
        FirmSettings.objects.update(sample_loaded_at="2020-01-01T00:00:00Z")
        self.assertTrue(guest.demo_reload_due())
        guest.reload_demo()
        self.assertFalse(Investor.objects.filter(firm_name="Added by a guest").exists())
        self.assertTrue(Investor.objects.filter(firm_name="Northwind Ventures").exists())
        self.assertEqual(User.objects.filter(email=sample.GUEST_EMAIL).count(), 1)

    def test_demo_never_loads_over_a_real_install(self):
        User.objects.create_user("real@firm.com", "Str0ng-pass-123", full_name="Real", role=Role.objects.get(is_admin=True))
        r = APIClient().post("/api/guest/start/", {}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Investor.objects.exists())


class GuestVaultTests(DataDirMixin, TestCase):
    def test_guest_cannot_change_or_lock_the_sample_pin(self):
        cache.clear()
        c = APIClient()
        c.post("/api/guest/start/", {}, format="json")
        r = c.post("/api/vault/pin/", {"pin": "1470", "current_pin": sample.GUEST_PIN}, format="json")
        self.assertEqual(r.status_code, 403)
        for _ in range(6):
            c.post("/api/vault/unlock/", {"pin": "9999"}, format="json")
        self.assertEqual(c.post("/api/vault/unlock/", {"pin": sample.GUEST_PIN}, format="json").status_code, 200)
