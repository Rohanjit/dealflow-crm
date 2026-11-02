"""Settings > Security overview (decision 63, S7): the checklist, the audit chain check and
"Sign out everyone"."""

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import TotpDevice
from core import audit, backups

from .base import APITestBase


class OverviewTests(APITestBase):
    def checks(self):
        r = self.client.get("/api/settings/security/overview/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json(), {c["key"]: c for c in r.json()["checks"]}

    @override_settings(MFA_REQUIRED=True)
    def test_checklist_reflects_the_install(self):
        TotpDevice.objects.create(user=self.admin, secret="x", confirmed=True)
        late = self.make_user("late@firm.com", self.make_role("M"))
        data, c = self.checks()
        self.assertFalse(c["two_step"]["ok"])  # someone hasn't set it up yet
        self.assertIn("1 of 2", c["two_step"]["label"])
        self.assertEqual(data["two_step"]["without"], [late.full_name or late.email])
        self.assertTrue(c["two_step"]["fix"])
        self.assertFalse(c["backups"]["ok"])
        self.assertFalse(c["scheduler"]["ok"])
        self.assertTrue(c["idle"]["ok"])
        TotpDevice.objects.create(user=late, secret="x", confirmed=True)
        backups.create_backup("manual")
        (settings.DATA_DIR / "scheduler.alive").write_text(timezone.now().isoformat())
        data, c = self.checks()
        self.assertTrue(c["two_step"]["ok"])
        self.assertTrue(c["backups"]["ok"])
        self.assertTrue(c["backups"]["label"].startswith("Last backup"))
        self.assertTrue(c["scheduler"]["ok"])
        self.assertEqual(data["score"]["total"], sum(1 for x in data["checks"] if x["ok"] is not None))

    def test_audit_chain_check_finds_tampering(self):
        self.client.get("/api/m/investors/")  # writes audit entries
        r = self.client.post("/api/settings/security/audit-check/").json()
        self.assertTrue(r["ok"], r)
        self.assertGreater(r["entries"], 0)
        log = sorted(audit.folder().glob("????-??.log"))[-1]
        lines = log.read_text().splitlines()
        log.write_text("\n".join(lines[1:]) + "\n")  # drop the first line
        r = self.client.post("/api/settings/security/audit-check/").json()
        self.assertFalse(r["ok"])
        _, c = self.checks()
        self.assertFalse(c["audit_chain"]["ok"])

    def test_sign_out_everyone_keeps_the_admin(self):
        # Real sessions (force_login), not force_authenticate.
        other, me = APIClient(), APIClient()
        other.force_login(self.make_user("m@firm.com", self.make_role("M")))
        me.force_login(self.admin)
        self.client = me
        self.assertEqual(other.get("/api/auth/me/").status_code, 200)
        r = self.client.post("/api/settings/security/sign-out-everyone/", {"password": "nope"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/settings/security/sign-out-everyone/", {"password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn(other.get("/api/auth/me/").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_admins_only(self):
        c = self.client_for(self.make_user("s@firm.com", self.make_role("S", manage_settings=True, manage_users=True)))
        for url in ("/api/settings/security/overview/",):
            self.assertEqual(c.get(url).status_code, 403)
        self.assertEqual(c.post("/api/settings/security/audit-check/").status_code, 403)
        self.assertEqual(c.post("/api/settings/security/sign-out-everyone/", {"password": "x"}, format="json").status_code, 403)


class RestoreTestTests(APITestBase):
    def test_latest_backup_restores_into_a_separate_database(self):
        from investors.models import Investor

        self.client.post("/api/m/investors/", {"firm_name": "Northwind", "tags": ["a"]}, format="json")
        backups.create_backup("manual")
        import subprocess
        from pathlib import Path
        from unittest import mock

        real, seen = subprocess.Popen, {}

        def popen(*a, **kw):  # look at the checking database before it's removed
            proc = real(*a, **kw)
            seen.update(kw["env"])
            return proc

        with mock.patch("core.restore_test.subprocess.Popen", popen):
            r = self.client.post("/api/settings/security/restore-test/").json()
        self.assertTrue(r["ok"], r)
        # The checking process gets no backup key, and its database's key isn't in the data folder.
        self.assertEqual(seen["DEALFLOW_DB_ENCRYPTION"], "1")
        self.assertFalse(Path(seen["DEALFLOW_KEYS_DIR"]).is_relative_to(settings.DATA_DIR))
        self.assertFalse(Path(seen["DEALFLOW_KEYS_DIR"]).exists())  # thrown away
        self.assertNotIn(".dealflow-restore-test-", " ".join(p.name for p in settings.DATA_DIR.iterdir()))
        self.assertGreater(r["rows"], 0)
        self.assertEqual(Investor.objects.count(), 1)  # live data untouched
        c = {x["key"]: x for x in self.client.get("/api/settings/security/overview/").json()["checks"]}
        self.assertTrue(c["restore_test"]["ok"])

    def test_damaged_backup_fails_the_test(self):
        p = backups.create_backup("manual")
        blob = bytearray(p.read_bytes())
        blob[-40] ^= 0xFF
        p.write_bytes(bytes(blob))
        r = self.client.post("/api/settings/security/restore-test/").json()
        self.assertFalse(r["ok"])
        self.assertIn("couldn't be opened", r["problems"][0])

    def test_restore_check_only_touches_its_own_database(self):
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("restore_check", "-")


class AdminAlertTests(APITestBase):
    def test_backup_failure_and_lockouts_reach_admins(self):
        from accounts.models import SignInFailure
        from core.models import FirmSettings

        self.assertEqual(self.client.get("/api/security/admin-alerts/").json(), [])
        FirmSettings.objects.filter(pk=1).update(last_backup_error="Disk full")
        SignInFailure.objects.create(key="a:x", count=5, first_at=timezone.now(),
                                     locked_until=timezone.now() + timezone.timedelta(minutes=30))
        keys = [a["key"].split(":")[1] for a in self.client.get("/api/security/admin-alerts/").json()]
        self.assertIn("backupfail", keys)
        self.assertIn("lockout", keys)
        other = self.client_for(self.make_user("m@firm.com", self.make_role("M")))
        self.assertEqual(other.get("/api/security/admin-alerts/").json(), [])
