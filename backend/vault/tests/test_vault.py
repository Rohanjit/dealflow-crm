import datetime as dt
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from core.tests.base import APITestBase
from vault.models import Document

PIN = "4826"
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x00" * 60


def upload_file(name, content=PDF, ctype="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=ctype)


class VaultBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.set_pin()

    def set_pin(self, client=None, pin=PIN):
        r = (client or self.client).post("/api/vault/pin/", {"pin": pin, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def upload(self, files, data=None, client=None, status=201):
        r = (client or self.client).post("/api/vault/upload/", {"files": files, "data": json.dumps(data or {})},
                                         format="multipart")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def company(self, name="Acme Foods"):
        return self.client.post("/api/m/companies/", {"name": name}, format="json").json()


class PinTests(APITestBase):
    def code(self, r):
        return r.json().get("code")

    def test_pin_needed_then_open_and_lock(self):
        s = self.client.get("/api/vault/status/").json()
        self.assertEqual((s["has_pin"], s["open"]), (False, False))
        r = self.client.get("/api/m/vault/")
        self.assertEqual((r.status_code, self.code(r)), (403, "vault_pin_required"))
        for weak in ("12", "1111", "1234", "abcd", "123456789"):
            r = self.client.post("/api/vault/pin/", {"pin": weak, "password": self.password}, format="json")
            self.assertEqual(r.status_code, 400, weak)
        r = self.client.post("/api/vault/pin/", {"pin": PIN, "password": "wrong"}, format="json")
        self.assertIn("password", r.json())
        s = self.client.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json").json()
        self.assertTrue(s["open"])
        self.assertEqual(self.client.get("/api/m/vault/").status_code, 200)
        self.client.post("/api/vault/lock/")
        r = self.client.get("/api/m/vault/")
        self.assertEqual((r.status_code, self.code(r)), (403, "vault_locked"))
        r = self.client.post("/api/vault/unlock/", {"pin": "9999"}, format="json")
        self.assertIn("pin", r.json())
        self.assertTrue(self.client.post("/api/vault/unlock/", {"pin": PIN}, format="json").json()["open"])

    def test_five_wrong_pins_lock_the_vault(self):
        self.client.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        self.client.post("/api/vault/lock/")
        for _ in range(4):
            self.client.post("/api/vault/unlock/", {"pin": "9999"}, format="json")
        r = self.client.post("/api/vault/unlock/", {"pin": "9999"}, format="json")
        self.assertIn("locked for 15 minutes", r.json()["pin"])
        r = self.client.post("/api/vault/unlock/", {"pin": PIN}, format="json")
        self.assertEqual((r.status_code, self.code(r)), (403, "vault_locked_out"))
        later = timezone.now() + dt.timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertTrue(self.client.post("/api/vault/unlock/", {"pin": PIN}, format="json").json()["open"])

    def test_closes_after_15_idle_minutes(self):
        self.client.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        soon = timezone.now() + dt.timedelta(minutes=10)
        with mock.patch("django.utils.timezone.now", return_value=soon):
            self.assertEqual(self.client.get("/api/m/vault/").status_code, 200)  # use keeps it open
        with mock.patch("django.utils.timezone.now", return_value=soon + dt.timedelta(minutes=14)):
            self.assertEqual(self.client.get("/api/m/vault/").status_code, 200)
        with mock.patch("django.utils.timezone.now", return_value=soon + dt.timedelta(minutes=30)):
            r = self.client.get("/api/m/vault/")
            self.assertEqual((r.status_code, self.code(r)), (403, "vault_locked"))

    def test_changing_needs_the_current_pin(self):
        self.client.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        r = self.client.post("/api/vault/pin/", {"pin": "5937", "password": self.password}, format="json")
        self.assertIn("current_pin", r.json())
        r = self.client.post("/api/vault/pin/", {"pin": "5937", "current_pin": PIN}, format="json")
        self.assertEqual(r.status_code, 200)
        self.client.post("/api/vault/lock/")
        self.assertTrue(self.client.post("/api/vault/unlock/", {"pin": "5937"}, format="json").json()["open"])

    def test_reset_by_user_managers(self):
        manager = self.make_user("m@firm.com", self.make_role("HR", {"vault": ["view"]}, manage_users=True))
        member = self.make_user("x@firm.com", self.make_role("Team", {"vault": ["view"]}))
        c = self.client_for(member)
        c.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        self.client.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        m = self.client_for(manager)
        self.assertEqual(m.post(f"/api/users/{member.pk}/reset-vault-pin/").status_code, 200)
        member.refresh_from_db()  # the test client reuses this object as the signed-in user
        self.assertFalse(c.get("/api/vault/status/").json()["has_pin"])
        self.assertEqual(c.get("/api/m/vault/").json()["code"], "vault_pin_required")
        self.assertEqual(m.post(f"/api/users/{self.admin.pk}/reset-vault-pin/").status_code, 403)
        users = self.client.get("/api/users/").json()
        rows = users["results"] if isinstance(users, dict) else users
        self.assertEqual({u["email"]: u["has_vault_pin"] for u in rows}["x@firm.com"], False)

    def test_role_permission_still_applies(self):
        outsider = self.make_user("o@firm.com", self.make_role("Deals", {"deals": ["view"]}))
        c = self.client_for(outsider)
        r = c.post("/api/vault/pin/", {"pin": PIN, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get("/api/m/vault/").status_code, 403)


class UploadTests(VaultBase):
    def test_upload_several_with_shared_details(self):
        acme = self.company()
        northwind = self.client.post("/api/m/investors/", {"firm_name": "Northwind Ventures"}, format="json").json()
        cat = self.opt("document_categories", "NDA")
        docs = self.upload(
            [upload_file("NDA - Northwind.pdf"), upload_file("Model v3.docx", DOCX, "application/octet-stream")],
            {"category": cat.pk, "tagged": [f"companies:{acme['id']}", f"investors:{northwind['id']}"],
             "titles": ["", "Financial model v3"], "file_size": 1},
        )
        self.assertEqual([d["title"] for d in docs], ["NDA - Northwind", "Financial model v3"])
        self.assertEqual({d["file_type"] for d in docs}, {"PDF", "Word"})
        self.assertEqual(docs[0]["file_size"], len(PDF))
        self.assertEqual(docs[0]["created_by"], self.admin.pk)
        self.assertEqual(docs[0]["created_at"], timezone.localdate().isoformat())
        self.assertEqual({t["title"] for t in docs[0]["tagged"]}, {"Acme Foods", "Northwind Ventures"})
        stored = Document.objects.get(pk=docs[0]["id"]).file.name
        self.assertRegex(stored, r"^vault/\d{4}/\d{2}/[0-9a-f]{32}\.pdf$")
        self.assertTrue((Path(settings.MEDIA_ROOT) / stored).exists())

    def test_refused_files_store_nothing(self):
        stored = lambda: sorted(p for p in (Path(settings.MEDIA_ROOT) / "vault").rglob("*") if p.is_file())  # noqa
        before = stored() if (Path(settings.MEDIA_ROOT) / "vault").exists() else []
        r = self.upload([upload_file("ok.pdf"), upload_file("page.html", b"<html><script>x</script>", "text/html")],
                        status=400)
        self.assertIn("page.html", " ".join(r["files"]))
        r = self.upload([upload_file("fake.pdf", b"MZ\x90\x00 not a pdf")], status=400)
        self.assertIn("doesn't look like a real .pdf", r["files"][0])
        r = self.upload([upload_file("empty.pdf", b"")], status=400)
        self.assertIn("empty", r["files"][0])
        self.assertFalse(Document.objects.exists())
        self.assertEqual(stored() if (Path(settings.MEDIA_ROOT) / "vault").exists() else [], before)

    @override_settings(VAULT_MAX_FILE_MB=1)
    def test_size_limit(self):
        big = PDF + b"0" * (1024 * 1024)
        r = self.upload([upload_file("big.pdf", big)], status=400)
        self.assertIn("larger than 1 MB", r["files"][0])

    def test_folder_names_in_the_file_name_are_dropped(self):
        docs = self.upload([upload_file("../../etc/passwd.pdf")])
        self.assertEqual(docs[0]["file_name"], "passwd.pdf")
        self.assertTrue(Document.objects.get().file.name.startswith("vault/"))

    def test_needs_create_permission_and_an_open_vault(self):
        viewer = self.make_user("v@firm.com", self.make_role("Readers", {"vault": ["view"]}))
        c = self.client_for(viewer)
        self.set_pin(c)
        self.upload([upload_file("a.pdf")], client=c, status=403)
        self.client.post("/api/vault/lock/")
        self.upload([upload_file("a.pdf")], status=403)
        r = self.client.post("/api/m/vault/", {"title": "No file"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_generic_create_is_not_used(self):
        r = self.client.post("/api/m/vault/", {"title": "No file"}, format="json")
        self.assertEqual(r.status_code, 400)


class DownloadTests(VaultBase):
    def setUp(self):
        super().setUp()
        self.pdf = self.upload([upload_file("Term sheet (signed).pdf")])[0]
        self.docx = self.upload([upload_file("IM.docx", DOCX)])[0]

    def get(self, doc, inline=False, client=None):
        return (client or self.client).get(f"/api/vault/{doc['id']}/download/" + ("?inline=1" if inline else ""))

    def test_download_and_open(self):
        r = self.get(self.pdf)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("Term sheet (signed).pdf", r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), PDF)
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", r["Cache-Control"])
        r = self.get(self.pdf, inline=True)
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertEqual(r["Content-Type"], "application/pdf")
        r = self.get(self.docx, inline=True)
        self.assertIn("attachment", r["Content-Disposition"])

    def test_locked_archived_and_forbidden(self):
        self.client.post("/api/vault/lock/")
        self.assertEqual(self.get(self.pdf).status_code, 403)
        self.client.post("/api/vault/unlock/", {"pin": PIN}, format="json")
        self.client.delete(f"/api/m/vault/{self.pdf['id']}/")
        self.assertEqual(self.get(self.pdf).status_code, 404)
        outsider = self.make_user("o@firm.com", self.make_role("Deals", {"deals": ["view"]}))
        self.assertEqual(self.get(self.docx, client=self.client_for(outsider)).status_code, 403)

    def test_deleting_for_good_removes_the_file(self):
        path = Path(settings.MEDIA_ROOT) / Document.objects.get(pk=self.pdf["id"]).file.name
        self.client.delete(f"/api/m/vault/{self.pdf['id']}/")
        self.assertTrue(path.exists())  # archived documents keep their file
        self.assertEqual(self.client.delete(f"/api/archive/vault/{self.pdf['id']}/").status_code, 204)
        self.assertFalse(path.exists())

    def test_backup_carries_the_files(self):
        from core import backups

        path = Path(settings.MEDIA_ROOT) / Document.objects.get(pk=self.pdf["id"]).file.name
        backup = backups.create_backup("manual")
        from core import backup_crypto

        plain = Path(settings.DATA_DIR) / "plain.zip"
        backup_crypto.decrypt(backup, plain)
        with zipfile.ZipFile(plain) as z:
            self.assertIn("files/" + Document.objects.get(pk=self.pdf["id"]).file.name, z.namelist())
        path.unlink()
        with open(backup, "rb") as fh:
            backups.restore_backup(fh)
        self.assertTrue(path.read_bytes().startswith(b"DFV1"))  # stored encrypted (decision 63, S4)


class VaultListTests(VaultBase):
    def test_documents_about_a_record(self):
        acme = self.company()
        deal1 = self.client.post("/api/m/deals/", {"company": f"companies:{acme['id']}"}, format="json").json()
        deal2 = self.client.post("/api/m/deals/", {"company": f"companies:{acme['id']}"}, format="json").json()
        attempt = self.client.post("/api/m/outreach/", {"company": f"companies:{acme['id']}"}, format="json").json()
        company_doc = self.upload([upload_file("Audited accounts.pdf")], {"tagged": [f"companies:{acme['id']}"]})[0]
        deal_doc = self.upload([upload_file("NDA.pdf")], {"tagged": [f"deals:{deal1['id']}"]})[0]
        pitch = self.upload([upload_file("Pitch.pdf")], {"tagged": [f"outreach:{attempt['id']}"]})[0]
        ids = lambda about: {d["id"] for d in self.client.get(f"/api/m/vault/?about={about}").json()["results"]}  # noqa
        self.assertEqual(ids(f"deals:{deal1['id']}"), {company_doc["id"], deal_doc["id"]})
        self.assertEqual(ids(f"deals:{deal2['id']}"), {company_doc["id"]})
        self.assertEqual(ids(f"outreach:{attempt['id']}"), {company_doc["id"], pitch["id"]})
        self.assertEqual(ids(f"companies:{acme['id']}"), {company_doc["id"], deal_doc["id"], pitch["id"]})
        schema = self.client.get("/api/schema/").json()
        for key in ("companies", "outreach", "deals", "investors"):
            mod = next(m for m in schema["modules"] if m["key"] == key)
            self.assertEqual(next(s for s in mod["sections"] if s["key"] == "documents")["status"], "ready", key)

    def test_edit_details_and_search_only_when_open(self):
        doc = self.upload([upload_file("Teaser.pdf")])[0]
        r = self.client.patch(f"/api/m/vault/{doc['id']}/", {"title": "Teaser (final)", "file_name": "x.exe",
                                                               "category": self.opt("document_categories", "Teaser").pk},
                              format="json").json()
        self.assertEqual((r["title"], r["file_name"]), ("Teaser (final)", "Teaser.pdf"))
        found = {x.get("module") for x in self.client.get("/api/search/?q=teaser").json()["results"]}
        self.assertIn("vault", found)
        self.client.post("/api/vault/lock/")
        found = {x.get("module") for x in self.client.get("/api/search/?q=teaser").json()["results"]}
        self.assertNotIn("vault", found)
        self.assertEqual(self.client.get("/api/m/vault/export/").status_code, 403)
        self.client.post("/api/vault/unlock/", {"pin": PIN}, format="json")
        self.assertEqual(self.client.get("/api/m/vault/export/").status_code, 200)

    def test_uploaded_on_filters_by_the_firms_day(self):
        doc = self.upload([upload_file("A.pdf")])[0]
        today = timezone.localdate().isoformat()
        rows = self.client.get(f"/api/m/vault/?created_at_from={today}&created_at_to={today}").json()["results"]
        self.assertEqual([r["id"] for r in rows], [doc["id"]])
        rows = self.client.get("/api/m/vault/?created_at_to=2000-01-01").json()["results"]
        self.assertEqual(rows, [])
