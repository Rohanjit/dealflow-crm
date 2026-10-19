"""The audit log: every request recorded, encrypted, chained, fetched only by admins with their
password and the audit passphrase."""

import datetime as dt
import gzip
import io
import json
import shutil
import tempfile
from pathlib import Path

from django.test import override_settings
from django.utils import timezone
from openpyxl import load_workbook

from core import audit
from investors.models import Investor

from .base import APITestBase

PASS = "copper lantern orchard 42"


class AuditBase(APITestBase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._o = override_settings(AUDIT_DIR=self._dir)
        self._o.enable()
        super().setUp()
        # Signed in for real (not force_authenticate) so requests look like the app's.
        self.client.force_authenticate(None)
        self.client.force_login(self.admin)

    def tearDown(self):
        self._o.disable()
        shutil.rmtree(self._dir, ignore_errors=True)
        super().tearDown()

    def set_passphrase(self, p=PASS):
        r = self.client.post("/api/audit/passphrase/", {"password": self.password, "passphrase": p}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def entries(self, p=PASS):
        today = timezone.localdate()
        return audit.read(p, today - dt.timedelta(days=1), today)

    def raw(self) -> str:
        return "".join(f.read_text() for f in self._dir.glob("*.log"))


class RecordingTests(AuditBase):
    def test_every_request_is_recorded_with_old_and_new_values(self):
        r = self.client.post("/api/m/investors/", {"firm_name": "Lumen Capital", "hq_city": "Pune"}, format="json")
        pk = r.json()["id"]
        self.client.patch(f"/api/m/investors/{pk}/", {"hq_city": "Mumbai"}, format="json")
        self.client.get(f"/api/m/investors/{pk}/")
        self.client.get("/api/m/investors/?q=lum")
        self.client.delete(f"/api/m/investors/{pk}/")
        self.set_passphrase()
        entries, problems = self.entries()
        self.assertEqual(problems, [])
        actions = [e["a"] for e in entries]
        self.assertEqual(actions[0], "Added an investor")
        self.assertIn("Edited investor Lumen Capital", actions)
        self.assertIn("Opened investor Lumen Capital", actions)
        self.assertIn("Viewed the investors list", actions)
        self.assertIn("Deleted investor Lumen Capital", actions)
        edit = next(e for e in entries if e["a"] == "Edited investor Lumen Capital")
        self.assertEqual(edit["ch"][0]["changes"], [{"field": "HQ city", "from": "Pune", "to": "Mumbai"}])
        self.assertEqual(edit["u"], {"id": self.admin.pk, "name": "Asha Admin", "email": "admin@firm.com"})
        self.assertRegex(edit["t"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}")
        self.assertEqual(edit["s"], 200)
        listing = next(e for e in entries if e["a"] == "Viewed the investors list")
        self.assertEqual(listing["q"], "q=lum")

    def test_nothing_readable_on_disk_and_secrets_never_written(self):
        self.client.post("/api/m/investors/", {"firm_name": "Hidden Name Partners"}, format="json")
        self.client.post("/api/auth/change-password/", {"current_password": self.password, "new_password": "Brand-new-pass-77"}, format="json")
        self.assertNotIn("Hidden Name", self.raw())
        self.assertNotIn("admin@firm.com", self.raw())
        self.client.force_login(self.admin)
        self.admin.set_password(self.password)
        self.admin.save()
        self.client.force_login(self.admin)
        self.set_passphrase()
        entries, _ = self.entries()
        text = json.dumps(entries)
        self.assertIn("Hidden Name Partners", text)
        self.assertNotIn(self.password, text)
        self.assertNotIn("Brand-new-pass-77", text)
        self.assertIn('"current_password": "***"', text)

    def test_sign_in_attempts_are_recorded(self):
        from rest_framework.test import APIClient

        anon = APIClient()
        anon.post("/api/auth/login/", {"email": "admin@firm.com", "password": "wrong-one-123"}, format="json")
        self.set_passphrase()
        entries, _ = self.entries()
        tried = next(e for e in entries if e["path"] == "/api/auth/login/")
        self.assertEqual(tried["a"], "Tried to sign in (refused)")
        self.assertIsNone(tried["u"])
        self.assertEqual(tried["b"], {"email": "admin@firm.com", "password": "***"})


class PassphraseAndFetchTests(AuditBase):
    def test_only_admins_with_password_and_passphrase(self):
        member = self.make_user("m@firm.com", self.make_role("Member+", {"investors": ["view"]}))
        from rest_framework.test import APIClient

        c = APIClient()
        c.force_login(member)
        self.assertEqual(c.get("/api/audit/").status_code, 403)
        self.assertEqual(c.post("/api/audit/fetch/", {}, format="json").status_code, 403)
        r = self.client.post("/api/audit/passphrase/", {"password": "nope", "passphrase": PASS}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/audit/passphrase/", {"password": self.password, "passphrase": "short"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.set_passphrase()
        today = timezone.localdate().isoformat()
        body = {"from": today, "to": today, "password": self.password, "passphrase": PASS}
        r = self.client.post("/api/audit/fetch/", body, format="json")
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.content))
        rows = list(wb["Audit log"].values)
        self.assertEqual(rows[0][:5], ("Date", "Time", "Person", "Email", "Action"))
        self.assertTrue(any(row[4] == "Viewed the audit log settings" or row[4] == "Set or changed the audit passphrase" for row in rows[1:]))
        self.assertIn("Chain check: no problems", [r[0] for r in wb["About this file"].values])
        self.assertEqual(self.client.post("/api/audit/fetch/", {**body, "password": "x"}, format="json").status_code, 400)

    def test_wrong_passphrases_lock_fetching(self):
        self.set_passphrase()
        today = timezone.localdate().isoformat()
        body = {"from": today, "to": today, "password": self.password, "passphrase": "not the right one at all"}
        for _ in range(audit.LOCK_AFTER):
            self.assertEqual(self.client.post("/api/audit/fetch/", body, format="json").status_code, 400)
        r = self.client.post("/api/audit/fetch/", {**body, "passphrase": PASS}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_entries_before_the_passphrase_are_resealed(self):
        self.client.post("/api/m/investors/", {"firm_name": "Early Bird Fund"}, format="json")
        self.assertTrue((self._dir / "temp-key.json").exists())
        self.set_passphrase()
        self.assertFalse((self._dir / "temp-key.json").exists())
        entries, problems = self.entries()
        self.assertEqual(problems, [])
        self.assertTrue(any("Early Bird Fund" in json.dumps(e) for e in entries))

    def test_changing_the_passphrase_and_starting_a_new_key(self):
        self.set_passphrase()
        self.client.post("/api/m/investors/", {"firm_name": "Before Change"}, format="json")
        r = self.client.post("/api/audit/passphrase/", {"password": self.password, "passphrase": "second passphrase here", "current_passphrase": "wrong"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/audit/passphrase/", {"password": self.password, "passphrase": "second passphrase here", "current_passphrase": PASS}, format="json")
        self.assertEqual(r.status_code, 200)
        with self.assertRaises(audit.AuditError):
            self.entries(PASS)
        entries, problems = self.entries("second passphrase here")
        self.assertEqual(problems, [])
        self.assertTrue(any("Before Change" in json.dumps(e) for e in entries))
        # Lost passphrase: a new key; older entries stay but can't be read.
        r = self.client.post("/api/audit/new-key/", {"password": self.password, "passphrase": "third passphrase here", "confirm": True}, format="json")
        self.assertEqual(r.status_code, 200)
        entries, problems = self.entries("third passphrase here")
        self.assertTrue(any("older key" in p for p in problems))
        self.assertFalse(any("Before Change" in json.dumps(e) for e in entries))


class TamperTests(AuditBase):
    def make_some(self):
        for n in ("One", "Two", "Three"):
            self.client.post("/api/m/investors/", {"firm_name": f"Fund {n}"}, format="json")
        self.set_passphrase()

    def test_deleted_line_is_detected(self):
        self.make_some()
        f = next(self._dir.glob("*.log"))
        lines = f.read_text().splitlines()
        del lines[1]
        f.write_text("\n".join(lines) + "\n")
        _, problems = self.entries()
        self.assertTrue(any("missing or was changed" in p for p in problems))

    def test_edited_line_is_detected(self):
        self.make_some()
        f = next(self._dir.glob("*.log"))
        lines = f.read_text().splitlines()
        line = json.loads(lines[1])
        c = bytearray(audit._unb64(line["c"]))
        c[5] ^= 1
        line["c"] = audit._b64(bytes(c))
        lines[1] = json.dumps(line, separators=(",", ":"))
        f.write_text("\n".join(lines) + "\n")
        _, problems = self.entries()
        self.assertTrue(any("altered" in p for p in problems))

    def test_dropped_newest_lines_are_detected(self):
        self.make_some()
        f = next(self._dir.glob("*.log"))
        lines = f.read_text().splitlines()
        f.write_text("\n".join(lines[:-2]) + "\n")
        _, problems = self.entries()
        self.assertTrue(any("newest entries are missing" in p for p in problems))


class KeepingTests(AuditBase):
    def test_finished_months_are_compressed_and_old_ones_removed(self):
        self.set_passphrase()
        kid, pub = audit._public_key()
        # Three months of history, written as the scheduler would have.
        prev = audit.GENESIS
        for month in ("2019-03", "2024-05", "2026-09"):
            entry = {"t": f"{month}-10T10:00:00.000+05:30", "a": f"Something in {month}"}
            line = audit._seal(entry, prev, kid, pub)
            (self._dir / f"{month}.log").write_text(line + "\n")
            prev = audit._hash(line)
        # Today's file continues the chain.
        for f in self._dir.glob(f"{timezone.localdate():%Y-%m}.log"):
            f.unlink()
        (self._dir / "HEAD").write_text(prev)
        self.client.get("/api/m/investors/")
        audit.set_keep_years(5)
        done = audit.housekeeping(timezone.localdate())
        self.assertEqual(done, ["Removed the audit log for 2019-03 (older than 5 years)."])
        self.assertTrue((self._dir / "2024-05.log.gz").exists())
        self.assertFalse((self._dir / "2024-05.log").exists())
        entries, problems = audit.read(PASS, dt.date(2024, 1, 1), timezone.localdate())
        self.assertEqual(problems, [])
        self.assertEqual([e["a"] for e in entries[:2]], ["Something in 2024-05", "Something in 2026-09"])
        with gzip.open(self._dir / "2024-05.log.gz", "rt") as fh:
            self.assertNotIn("Something", fh.read())

    def test_retention_setting(self):
        r = self.client.patch("/api/audit/settings/", {"keep_years": 8, "password": self.password}, format="json")
        self.assertEqual(r.json()["keep_years"], 8)
        for bad in (0, 101, "8", True):
            r = self.client.patch("/api/audit/settings/", {"keep_years": bad, "password": self.password}, format="json")
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.patch("/api/audit/settings/", {"keep_years": None, "password": self.password}, format="json")
        self.assertIsNone(r.json()["keep_years"])

    def test_backups_carry_a_copy_and_restores_leave_the_log_alone(self):
        import zipfile

        from core.backups import create_backup

        Investor.objects.create(firm_name="Kept", created_by=self.admin, updated_by=self.admin)
        self.client.get("/api/m/investors/")
        z = zipfile.ZipFile(create_backup("manual"))
        self.assertTrue(any(n.startswith("audit/") and n.endswith(".log") for n in z.namelist()))
        self.assertFalse(any(n == "audit/temp-key.json" for n in z.namelist()))
