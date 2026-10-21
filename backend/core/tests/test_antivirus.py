"""Virus scanning (decision 63, S5): every upload goes through clamd first, harmful files are refused and
recorded, the scanner failing or missing refuses uploads (fails closed), and Vault files are checked
again when opened. A stand-in clamd speaks the real protocol on a local port."""

import json
import socket
import socketserver
import struct
import threading

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from core import antivirus
from core.models import ScanRefusal
from vault.models import Document

from .base import APITestBase

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer\n%%EOF\n" + b"term sheet " * 50


class FakeClamd(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.scanned: list[bytes] = []
        self.answer_error = False
        self.version = "ClamAV 1.4.6/28157/Sun Oct 18 06:24:14 2026"
        self.flagged: set[bytes] = {EICAR}

    @property
    def address(self):
        return f"127.0.0.1:{self.server_address[1]}"


class _Handler(socketserver.BaseRequestHandler):
    def _cmd(self):
        buf = b""
        while not buf.endswith(b"\0"):
            c = self.request.recv(1)
            if not c:
                break
            buf += c
        return buf.rstrip(b"\0")

    def _reply(self, text: str):
        self.request.sendall(text.encode() + b"\0")

    def handle(self):
        srv = self.server
        cmd = self._cmd()
        if cmd == b"zPING":
            return self._reply("PONG")
        if cmd == b"zVERSION":
            return self._reply(srv.version)
        if cmd != b"zINSTREAM":
            return self._reply("UNKNOWN COMMAND")
        data = b""
        while True:
            (size,) = struct.unpack("!L", self.request.recv(4, socket.MSG_WAITALL))
            if not size:
                break
            data += self.request.recv(size, socket.MSG_WAITALL)
        srv.scanned.append(data)
        if srv.answer_error:
            return self._reply("INSTREAM size limit exceeded. ERROR")
        if b"TOO-DEEP" in data:
            return self._reply("stream: Heuristics.Limits.Exceeded.MaxRecursion FOUND")
        if any(sig in data for sig in srv.flagged):
            return self._reply("stream: Eicar-Test-Signature FOUND")
        return self._reply("stream: OK")


class ScanBase(APITestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clamd = FakeClamd()
        threading.Thread(target=cls.clamd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.clamd.shutdown()
        cls.clamd.server_close()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.clamd.scanned.clear()
        self.clamd.answer_error = False
        self.clamd.flagged = {EICAR}
        self._on = override_settings(ANTIVIRUS="required", CLAMD_ADDRESS=self.clamd.address)
        self._on.enable()
        self.addCleanup(self._on.disable)

    def open_vault(self):
        r = self.client.post("/api/vault/pin/", {"pin": "4826", "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def vault_upload(self, *files):
        return self.client.post("/api/vault/upload/", {"files": [SimpleUploadedFile(n, c) for n, c in files],
                                                        "data": json.dumps({})}, format="multipart")


class UploadScanTests(ScanBase):
    def test_clean_vault_upload_is_scanned_then_stored(self):
        self.open_vault()
        r = self.vault_upload(("IM.pdf", PDF))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.clamd.scanned[0], PDF)  # the plain file, before encryption

    def test_harmful_file_refuses_the_whole_upload_and_is_recorded(self):
        self.open_vault()
        r = self.vault_upload(("ok.pdf", PDF), ("bad.pdf", PDF + EICAR))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "infected")
        self.assertIn("bad.pdf", r.json()["detail"])
        self.assertEqual(Document.objects.count(), 0)
        ref = ScanRefusal.objects.get()
        self.assertEqual((ref.place, ref.file_name, ref.signature, ref.user_id),
                         ("vault", "bad.pdf", "Eicar-Test-Signature", self.admin.id))
        self.assertEqual(len(ref.sha256), 64)

    def test_scanner_down_refuses_uploads(self):
        """Fails closed: no answer, no upload."""
        self.open_vault()
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            dead = f"127.0.0.1:{s.getsockname()[1]}"
        with override_settings(CLAMD_ADDRESS=dead):
            r = self.vault_upload(("IM.pdf", PDF))
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["code"], "scanner_unavailable")
            self.assertFalse(self.client.get("/api/vault/status/").json()["uploads_allowed"])
        self.assertEqual(Document.objects.count(), 0)

    def test_no_scanner_configured_refuses_uploads(self):
        self.open_vault()
        with override_settings(CLAMD_ADDRESS=""):
            self.assertEqual(self.vault_upload(("IM.pdf", PDF)).status_code, 503)

    def test_scanner_error_or_unchecked_file_is_refused(self):
        self.open_vault()
        self.clamd.answer_error = True  # "INSTREAM size limit exceeded. ERROR": not checked, so refused
        r = self.vault_upload(("big.pdf", PDF))
        self.assertEqual(r.status_code, 400)
        self.assertIn("couldn't be fully checked", r.json()["detail"])
        self.clamd.answer_error = False
        r = self.vault_upload(("deep.pdf", PDF + b"TOO-DEEP"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("couldn't be fully checked", r.json()["detail"])

    def test_email_attachments_are_scanned(self):
        r = self.client.post("/api/mail/attachments/", {"files": [SimpleUploadedFile("x.pdf", PDF + EICAR)]},
                             format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ScanRefusal.objects.get().place, "email")
        r = self.client.post("/api/mail/attachments/", {"files": [SimpleUploadedFile("x.pdf", PDF)]}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)

    def test_import_files_are_scanned(self):
        f = SimpleUploadedFile("list.csv", b"Firm name\nBlume\n" + EICAR + b"\n", content_type="text/csv")
        r = self.client.post("/api/m/investors/import/upload/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ScanRefusal.objects.get().place, "import")

    def test_large_file_goes_in_pieces(self):
        self.open_vault()
        big = PDF + b"x" * (3 * 1024 * 1024)
        self.assertEqual(self.vault_upload(("big.pdf", big)).status_code, 201)
        self.assertEqual(self.clamd.scanned[0], big)


class OpenScanTests(ScanBase):
    def test_stored_file_matching_new_signatures_is_refused_on_opening(self):
        self.open_vault()
        doc = self.vault_upload(("IM.pdf", PDF)).json()[0]
        self.assertEqual(self.client.get(f"/api/vault/{doc['id']}/download/").status_code, 200)
        self.clamd.flagged = {b"term sheet"}  # tomorrow's signatures catch it
        r = self.client.get(f"/api/vault/{doc['id']}/download/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "infected")
        ref = ScanRefusal.objects.get()
        self.assertEqual((ref.place, ref.document_id), ("vault_open", doc["id"]))

    def test_opening_still_works_when_scanner_is_down(self):
        self.open_vault()
        doc = self.vault_upload(("IM.pdf", PDF)).json()[0]
        with override_settings(CLAMD_ADDRESS="127.0.0.1:1"):
            self.assertEqual(self.client.get(f"/api/vault/{doc['id']}/download/").status_code, 200)


class ScannerStatusTests(ScanBase):
    def test_admins_see_scanner_status_and_refusals(self):
        self.client.post("/api/mail/attachments/", {"files": [SimpleUploadedFile("x.pdf", PDF + EICAR)]},
                         format="multipart")
        st = self.client.get("/api/settings/security/scanner/").json()
        self.assertTrue(st["available"])
        self.assertEqual((st["engine"], st["signatures"], st["signatures_date"]), ("ClamAV 1.4.6", "28157", "2026-10-18"))
        self.assertEqual(st["refused_30_days"], 1)
        self.assertEqual(st["recent"][0]["file_name"], "x.pdf")
        bell = self.client.get("/api/security/refusals/").json()
        self.assertEqual(bell[0]["signature"], "Eicar-Test-Signature")

    def test_old_signatures_are_flagged(self):
        self.clamd.version = "ClamAV 1.4.6/27000/Mon Jan  5 06:24:14 2026"
        self.assertTrue(self.client.get("/api/settings/security/scanner/").json()["signatures_stale"])

    def test_not_for_other_people(self):
        role = self.make_role("Member+", manage_settings=True, manage_users=True)
        c = self.client_for(self.make_user("m@firm.com", role))
        self.assertEqual(c.get("/api/settings/security/scanner/").status_code, 403)
        self.assertEqual(c.get("/api/security/refusals/").json(), [])

    def test_off_only_on_development_servers(self):
        from dealflow.settings import antivirus_mode

        self.assertEqual(antivirus_mode(False, "off"), "required")
        self.assertEqual(antivirus_mode(True, "off"), "off")
        self.assertEqual(antivirus_mode(True, ""), "required")

    def test_version_parsing(self):
        v = antivirus._parse_version("ClamAV 1.4.6")
        self.assertEqual(v["signatures"], None)
