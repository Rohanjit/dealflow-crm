import io
import zipfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import Role
from core import backups
from core.models import FirmSettings
from investors.models import Investor

from .base import APITestBase


class BackupTests(APITestBase):
    def test_backup_and_restore(self):
        self.client.post("/api/m/investors/", {"firm_name": "Blume", "tags": ["a"]}, format="json")
        media = settings.MEDIA_ROOT
        media.mkdir(parents=True, exist_ok=True)
        (media / "doc.txt").write_text("secret deal memo")

        r = self.client.post("/api/backups/")
        self.assertEqual(r.status_code, 201, r.content)
        name = r.json()["name"]
        listing = self.client.get("/api/backups/").json()
        self.assertEqual(listing["backups"][0]["name"], name)
        dl = self.client.get(f"/api/backups/{name}/download/")
        blob = b"".join(dl.streaming_content)
        z = zipfile.ZipFile(io.BytesIO(blob))
        self.assertIn("files/doc.txt", z.namelist())

        # change things, then restore
        Investor.objects.all().delete()
        self.client.post("/api/m/investors/", {"firm_name": "Other"}, format="json")
        (media / "doc.txt").unlink()
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", blob), "confirm": "RESTORE"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(list(Investor.objects.values_list("firm_name", flat=True)), ["Blume"])
        self.assertEqual([t.name for t in Investor.objects.get().tags.all()], ["a"])
        self.assertEqual((media / "doc.txt").read_text(), "secret deal memo")
        self.assertTrue(r.json()["safety_backup"].startswith("dealflow-pre-restore-"))

    def test_restore_with_records_linked_across_modules(self):
        """Records pointing at ones from modules loaded later (a contact at an Outreach company) restore
        on Postgres as well as SQLite."""
        from contacts.models import Contact
        from deals.models import Deal, DealInvestor
        from tasks.models import Task

        c = self.client
        inv = c.post("/api/m/investors/", {"firm_name": "Blume"}, format="json").json()
        co = c.post("/api/m/companies/", {"name": "Acme Foods"}, format="json").json()
        c.post("/api/m/contacts/", {"full_name": "Ravi", "firm": f"companies:{co['id']}"}, format="json")
        o = c.post("/api/m/outreach/", {"company": f"companies:{co['id']}", "stage": "won"}, format="json").json()
        deal = Deal.objects.get(outreach_id=o["id"])
        c.post("/api/m/deal_investors/", {"deal": f"deals:{deal.pk}", "investor": f"investors:{inv['id']}"}, format="json")
        c.post("/api/m/tasks/", {"title": "Call Ravi", "related": f"deals:{deal.pk}"}, format="json")
        before = (Contact.objects.get().company_id, Deal.objects.count(), DealInvestor.objects.count(), Task.objects.count())
        blob = open(backups.create_backup("manual"), "rb").read()
        Task.objects.all().delete()
        r = c.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", blob), "confirm": "RESTORE"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        after = (Contact.objects.get().company_id, Deal.objects.count(), DealInvestor.objects.count(), Task.objects.count())
        self.assertEqual(after, before)

    def test_restore_rejects_garbage_and_needs_confirm(self):
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", b"nope"), "confirm": "RESTORE"}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", b"nope")}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_prune_keeps_n_per_kind(self):
        fs = FirmSettings.load()
        fs.backup_keep = 2
        fs.save()
        d = backups.backup_dir()
        for old in d.glob("*.zip"):
            old.unlink()
        for i in range(4):
            (d / f"dealflow-daily-2026010{i}-020000.zip").write_bytes(b"x")
        (d / "dealflow-manual-20260101-100000.zip").write_bytes(b"x")
        backups.prune(2)
        names = sorted(p.name for p in d.glob("*.zip"))
        self.assertEqual(names, ["dealflow-daily-20260102-020000.zip", "dealflow-daily-20260103-020000.zip",
                                 "dealflow-manual-20260101-100000.zip"])

    def test_due_for_daily(self):
        fs = FirmSettings.load()
        fs.setup_completed_at = timezone.now()
        fs.backup_hour = 0
        fs.save()
        self.assertTrue(backups.due_for_daily_backup())
        backups.create_backup("daily")
        self.assertFalse(backups.due_for_daily_backup())

    def test_backups_admin_only(self):
        role = self.make_role("Settings", manage_settings=True)
        c = self.client_for(self.make_user("s@firm.com", role))
        self.assertEqual(c.get("/api/backups/").status_code, 403)
        self.assertEqual(c.post("/api/backups/").status_code, 403)

    def test_download_path_traversal(self):
        self.assertEqual(self.client.get("/api/backups/..%2Fsecret_key/download/").status_code, 404)
