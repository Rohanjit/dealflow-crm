import io
import json
import zipfile
from pathlib import Path
from unittest import mock

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
        # Encrypted (decision 63, S4): nothing readable without the backup key.
        self.assertTrue(blob.startswith(b"DFBK"))
        self.assertNotIn(b"Blume", blob)
        from core import backup_crypto

        src, plain = settings.DATA_DIR / "t.dfb", settings.DATA_DIR / "t.zip"
        src.write_bytes(blob)
        backup_crypto.decrypt(src, plain)
        z = zipfile.ZipFile(plain)
        self.assertIn("files/doc.txt", z.namelist())

        # change things, then restore
        Investor.objects.all().delete()
        self.client.post("/api/m/investors/", {"firm_name": "Other"}, format="json")
        (media / "doc.txt").unlink()
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.dfb", blob), "confirm": "RESTORE", "password": self.password}, format="multipart")
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
        r = c.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", blob), "confirm": "RESTORE", "password": self.password}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        after = (Contact.objects.get().company_id, Deal.objects.count(), DealInvestor.objects.count(), Task.objects.count())
        self.assertEqual(after, before)

    def test_restore_rejects_garbage_and_needs_confirm(self):
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", b"nope"), "confirm": "RESTORE", "password": self.password}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/backups/restore/", {"file": SimpleUploadedFile("b.zip", b"nope")}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_prune_keeps_n_per_kind(self):
        fs = FirmSettings.load()
        fs.backup_keep = 2
        fs.save()
        d = backups.backup_dir()
        for old in d.glob("dealflow-*"):
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


class HourlyBackupTests(APITestBase):
    def test_hourly_backups_when_switched_on_and_keep_24(self):
        fs = FirmSettings.load()
        fs.setup_completed_at = timezone.now()
        fs.save()
        self.assertFalse(backups.due_for_hourly_backup())
        r = self.client.patch("/api/settings/firm/", {"hourly_backups": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(backups.due_for_hourly_backup())
        backups.create_backup("hourly")
        self.assertFalse(backups.due_for_hourly_backup())
        self.assertTrue(backups.due_for_hourly_backup(timezone.now() + timezone.timedelta(hours=1)))
        d = backups.backup_dir()
        for i in range(30):
            (d / f"dealflow-hourly-20260101-{i:02d}0000.dfb").write_bytes(b"x")
        backups.prune(2)
        self.assertEqual(len(list(d.glob("dealflow-hourly-*"))), 24)

    def test_only_admins_switch_it(self):
        c = self.client_for(self.make_user("s@firm.com", self.make_role("S", manage_settings=True)))
        self.assertEqual(c.patch("/api/settings/firm/", {"hourly_backups": True}, format="json").status_code, 403)


class StreamingBackupTests(APITestBase):
    """Backups are written and read as streams (load test, 22 Oct 2026): encrypted as they're made,
    restored straight from the encrypted file, nothing readable on disk, memory flat."""

    def _roundtrip(self, data: bytes):
        from core import backup_crypto

        path = settings.DATA_DIR / "s.dfb"
        with open(path, "wb") as out:
            w = backup_crypto.EncryptingWriter(out, {"kind": "test"})
            for i in range(0, len(data), 300_001):  # writes of an awkward size
                w.write(data[i:i + 300_001])
            w.close()
        return path

    def test_encrypted_stream_reads_back_from_anywhere(self):
        import random

        from core import backup_crypto

        chunk = backup_crypto.CHUNK
        rng = random.Random(7)
        for size in (0, 1, chunk - 1, chunk, chunk + 1, 3 * chunk, 3 * chunk + 12345):
            data = rng.randbytes(size)
            path = self._roundtrip(data)
            plain = settings.DATA_DIR / "s.bin"
            backup_crypto.decrypt(path, plain)  # also the same format the old encrypt() wrote
            self.assertEqual(plain.read_bytes(), data, size)
            with backup_crypto.DecryptingReader(path) as r:
                self.assertEqual(r.size, size)
                self.assertEqual(r.read(), data)
                for _ in range(20):
                    at, n = rng.randrange(size + 1), rng.randrange(1, 3 * chunk)
                    r.seek(at)
                    self.assertEqual(r.read(n), data[at:at + n])
                r.seek(-min(5, size), 2)
                self.assertEqual(r.read(), data[-5:])

    def test_changed_reordered_or_cut_streams_are_refused(self):
        import struct

        from core import backup_crypto

        chunk = backup_crypto.CHUNK
        path = self._roundtrip(bytes(3 * chunk + 100))
        blob = path.read_bytes()
        n = struct.unpack(">I", blob[4:8])[0]
        start = 8 + n + 7
        step = 4 + chunk + 16
        pieces = [blob[start + i * step:start + (i + 1) * step] for i in range(4)]
        bad = {
            "cut at a piece boundary": blob[:start + 3 * step],
            "cut mid-piece": blob[:-10],
            "extra data": blob + b"x",
            "pieces swapped": blob[:start] + pieces[1] + pieces[0] + pieces[2] + pieces[3],
            "byte changed": blob[:start + step + 100] + bytes([blob[start + step + 100] ^ 1]) + blob[start + step + 101:],
            "header changed": blob.replace(b'"test"', b'"tess"'),
        }
        for what, b in bad.items():
            path.write_bytes(b)
            with self.assertRaises(backup_crypto.BackupKeyError, msg=what):
                with backup_crypto.DecryptingReader(path) as r:
                    while r.read(chunk):
                        pass

    def test_backup_is_streamed_and_restores_from_the_encrypted_file(self):
        from core import backup_crypto

        self.client.post("/api/m/investors/", {"firm_name": "Blume", "tags": ["a", "b"]}, format="json")
        settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
        (settings.MEDIA_ROOT / "doc.bin").write_bytes(b"DFV1 already encrypted")
        made = []
        real_open = open

        def watch_open(file, mode="r", *a, **kw):  # every file the backup writes
            if "w" in str(mode):
                made.append(str(file))
            return real_open(file, mode, *a, **kw)

        with mock.patch("builtins.open", watch_open):
            path = backups.create_backup("manual")
        self.assertEqual([Path(p).name for p in made], [path.name + ".part"])  # only the encrypted file
        self.assertEqual(sorted(p.name for p in backups.backup_dir().iterdir() if not p.name.startswith(".lock")),
                         [path.name])
        z, manifest, (member, fmt) = backups.open_backup(path)
        try:
            self.assertEqual((manifest["format"], member, fmt), (2, "data.jsonl", "jsonl"))
            lines = z.read("data.jsonl").decode().splitlines()
            inv = next(json.loads(x) for x in lines if '"investors.investor"' in x)
            self.assertEqual(len(inv["fields"]["tags"]), 2)  # many-to-many values carried
            self.assertEqual(z.getinfo("files/doc.bin").compress_type, zipfile.ZIP_STORED)
        finally:
            backups.close_backup(z)

        Investor.objects.all().delete()
        (settings.MEDIA_ROOT / "doc.bin").unlink()
        with open(path, "rb") as fh:
            backups.restore_backup(fh)
        self.assertEqual([t.name for t in Investor.objects.get().tags.order_by("name")], ["a", "b"])
        self.assertEqual((settings.MEDIA_ROOT / "doc.bin").read_bytes(), b"DFV1 already encrypted")
        self.assertEqual(list(settings.DATA_DIR.glob(".restore-*")), [])
        self.assertTrue(backup_crypto.is_encrypted(path))

    def test_backups_from_before_streaming_still_restore(self):
        """Format 1: one data.json, made with dumpdata, encrypted the old way."""
        from django.core.management import call_command

        from core import backup_crypto

        self.client.post("/api/m/investors/", {"firm_name": "Old Capital"}, format="json")
        data = io.StringIO()
        call_command("dumpdata", exclude=backups.EXCLUDE, stdout=data)
        plain = settings.DATA_DIR / "old.zip"
        with zipfile.ZipFile(plain, "w") as z:
            z.writestr("manifest.json", json.dumps({"format": 1, "created_at": "2026-10-01T00:00:00+00:00"}))
            z.writestr("data.json", data.getvalue())
        enc = settings.DATA_DIR / "old.dfb"
        backup_crypto.encrypt(plain, enc, {"kind": "manual"})
        Investor.objects.all().delete()
        for f in (enc, plain):  # encrypted, and from before backups were encrypted
            Investor.objects.all().delete()
            with open(f, "rb") as fh:
                backups.restore_backup(fh)
            self.assertEqual(list(Investor.objects.values_list("firm_name", flat=True)), ["Old Capital"])

    def test_a_backup_that_was_killed_doesnt_block_the_next(self):
        (backups.backup_dir() / ".lock").write_text("")  # left behind
        backups.create_backup("manual")
        with backups._Lock():
            with self.assertRaises(backups.BackupError):
                backups.create_backup("manual")
        backups.create_backup("manual")

    def test_uploaded_backups_go_to_the_data_folder(self):
        from django.test import RequestFactory

        h = backups.DiskUploadHandler(RequestFactory().post("/"))
        h.new_file("file", "b.dfb", "application/octet-stream", 10)
        h.receive_data_chunk(b"0123456789", 0)
        f = h.file_complete(10)
        self.assertEqual(Path(f.temporary_file_path()).parent, settings.DATA_DIR)
        f.close()
