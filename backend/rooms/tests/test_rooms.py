"""Investor data rooms (decision 64): sharing, approval, codes, NDA, watermarked reading and downloads,
questions, activity on the tracker, stopping links, keeping investors on their own address."""

import io
import json
import re
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Note
from deals.models import AccessRequest, DealInvestor
from mail.tests.test_mail import EVERYTHING, MailBase
from rooms import crypto, marks
from rooms.models import Room, RoomFile, Share, Visit


def make_pdf(pages=2) -> bytes:
    from reportlab.pdfgen import canvas

    b = io.BytesIO()
    c = canvas.Canvas(b)
    for i in range(pages):
        c.drawString(100, 700, f"Zeta Pharma information memorandum, page {i + 1}")
        c.showPage()
    c.save()
    return b.getvalue()


def make_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active["A1"] = "Revenue"
    wb.create_sheet("Cash flow")
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class RoomBase(MailBase):
    def setUp(self):
        super().setUp()
        self.entry = DealInvestor.objects.create(deal=self.deal, investor=self.northwind, created_by=self.admin,
                                                 updated_by=self.admin)
        self.deal.team_md.add(self.admin)

    def ready(self, client=None, user=None):
        """Mailbox connected and Vault open for this person."""
        client = client or self.client
        self.connect(client=client, user=user or self.admin)
        r = client.post("/api/vault/pin/", {"pin": "4826", "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def vault(self, client, content, name):
        r = client.post("/api/vault/upload/", {"files": [SimpleUploadedFile(name, content)],
                                               "data": json.dumps({"tagged": [f"deals:{self.deal.pk}"]})},
                        format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()[0]["id"]

    def add(self, client, doc, mode="view", status=201):
        r = client.post(f"/api/deals/{self.deal.pk}/room/files/", {"document": doc, "mode": mode}, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def share(self, client=None, status=201, **kw):
        data = {"entry": self.entry.pk, "email": "priya@northwind.example", "days": 30, **kw}
        with self.captureOnCommitCallbacks(execute=True):
            r = (client or self.client).post(f"/api/deals/{self.deal.pk}/room/shares/", data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def sent_to(self, addr):
        return [m for m in self.fake.gmail_sent if addr in m["To"]]

    def body(self, msg) -> str:
        part = msg.get_body(preferencelist=("plain",))
        return part.get_content()

    def link(self) -> str:
        msgs = [m for m in self.sent_to("priya@northwind.example") if "data room link" in m["Subject"]]
        self.assertTrue(msgs, "no invitation sent")
        return re.search(r"https://crm\.test(/r/[^\s/]+/)", self.body(msgs[-1])).group(1)

    def code(self) -> str:
        msgs = [m for m in self.sent_to("priya@northwind.example") if "is your code" in m["Subject"]]
        return re.search(r"\b(\d{6})\b", self.body(msgs[-1])).group(1)

    def investor(self):
        c = APIClient(enforce_csrf_checks=False)
        return c

    def open_room(self, inv, path):
        r = inv.post(path + "code/")
        self.assertEqual(r.status_code, 302, r.content)
        r = inv.post(path + "verify/", {"code": self.code()})
        self.assertEqual(r.status_code, 302, r.content)
        return r


class SharingTests(RoomBase):
    def test_the_whole_way_through(self):
        self.ready()
        pdf = make_pdf(3)
        doc = self.vault(self.client, pdf, "IM.pdf")
        out = self.add(self.client, doc)
        rf = RoomFile.objects.get()
        self.assertEqual((rf.pages, rf.mode, out["files"][0]["title"]), (3, "view", "IM"))
        stored = Path(settings.MEDIA_ROOT, rf.stored.name).read_bytes()
        self.assertTrue(stored.startswith(b"DFR1"))
        self.assertNotIn(b"information memorandum", stored)
        # An admin who is the deal's MD shares: approved there and then, the link goes out.
        out = self.share()
        s = out["shares"][0]
        self.assertEqual((s["status"], s["name"]), ("active", "Ms. Priya Shah"))
        self.assertTrue(out["room"]["approved"])
        path = self.link()
        self.assertTrue(Note.objects.filter(module="deal_investors", record_id=self.entry.pk, kind="email").exists())

        inv = self.investor()
        r = inv.get(path)
        self.assertContains(r, "Email me a code")
        self.assertContains(r, "p•••")
        self.assertEqual(r["Referrer-Policy"], "same-origin")
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", r["Content-Security-Policy"].split("style-src")[1].split(";")[0])
        inv.post(path + "code/")
        r = inv.post(path + "verify/", {"code": "000000" if self.code() != "000000" else "111111"})
        self.assertContains(r, "That code isn", status_code=400)
        r = inv.post(path + "verify/", {"code": self.code()})
        self.assertEqual(r.status_code, 302)
        cookie = [k for k in r.cookies if k.startswith("dealflow_room_")][0]
        self.assertTrue(r.cookies[cookie]["httponly"])
        self.assertEqual(r.cookies[cookie]["samesite"], "Strict")
        # Codes are single use.
        r = inv.post(path + "verify/", {"code": self.code()})
        self.assertEqual(r.status_code, 400)

        # NDA on screen first.
        r = inv.get(path)
        self.assertContains(r, "Before you open the room")
        r = inv.post(path + "nda/", {"name": "Priya Shah"})
        self.assertEqual(r.status_code, 400)
        inv.post(path + "nda/", {"name": "Priya Shah", "agree": "yes"})
        r = inv.get(path)
        self.assertContains(r, "IM")
        self.assertContains(r, "View only")
        self.assertNotContains(r, "/download/")

        r = inv.get(f"{path}f/{rf.pk}/")
        self.assertContains(r, f"/f/{rf.pk}/p/3/")
        r = inv.get(f"{path}f/{rf.pk}/p/1/")
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"))
        self.assertEqual(inv.get(f"{path}f/{rf.pk}/p/9/").status_code, 404)
        self.assertEqual(inv.get(f"{path}f/{rf.pk}/download/").status_code, 404)  # view only
        visit = Visit.objects.get(kind="viewed")
        Visit.objects.filter(pk=visit.pk).update(last_at=timezone.now() - timezone.timedelta(seconds=15))
        r = inv.post(f"{path}f/{rf.pk}/ping/", {"page": 2}, format="json")
        self.assertEqual(r.status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.pages, [2])
        self.assertGreaterEqual(visit.seconds, 14)

        # The banker sees it, with the stage to suggest.
        out = self.client.get(f"/api/deals/{self.deal.pk}/room/").json()
        row = out["by_entry"][str(self.entry.pk)]
        self.assertEqual(row["suggestion"]["stage"], "collaterals_shared")
        self.assertGreaterEqual(row["seconds"], 14)
        self.assertEqual(out["shares"][0]["nda"]["state"], "done")
        kinds = [a["kind"] for a in out["activity"]]
        self.assertIn("viewed", kinds)
        self.assertIn("nda", kinds)

        # Downloads carry a trace tag the banker can look up.
        self.client.patch(f"/api/rooms/files/{rf.pk}/", {"mode": "download"}, format="json")
        r = inv.get(f"{path}f/{rf.pk}/download/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        tags = marks.find_trace(r.content)
        self.assertEqual(len(tags), 1)
        r2 = self.client.post(f"/api/deals/{self.deal.pk}/room/trace/",
                              {"file": SimpleUploadedFile("leak.pdf", r.content)}, format="multipart")
        self.assertEqual(r2.json()["found"][0]["who"], "Ms. Priya Shah (Northwind Ventures)")

        # Questions go to the deal team, the answer back to the investor only.
        inv.post(path + "ask/", {"file": rf.pk, "text": "What's the FY26 EBITDA margin?", "page": "2"})
        q = self.client.get("/api/rooms/alerts/").json()["questions"]
        self.assertEqual(len(q), 1)
        logged = Note.objects.filter(module="deal_investors", record_id=self.entry.pk, meta__room_question__isnull=False)
        self.assertEqual(logged.count(), 1)
        before = len(self.sent_to("priya@northwind.example"))
        self.client.post(f"/api/rooms/questions/{q[0]['id']}/answer/", {"answer": "About 18%."}, format="json")
        self.assertEqual(len(self.sent_to("priya@northwind.example")), before + 1)
        self.assertNotIn("18%", self.body(self.sent_to("priya@northwind.example")[-1]))  # the answer stays in the room
        self.assertContains(inv.get(path), "About 18%.")
        self.assertEqual(self.client.get("/api/rooms/alerts/").json()["questions"], [])

        # The banker confirms the stage move through the normal edit.
        r = self.client.patch(f"/api/m/deal_investors/{self.entry.pk}/", {"stage": "collaterals_shared"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        out = self.client.get(f"/api/deals/{self.deal.pk}/room/").json()
        self.assertIsNone(out["by_entry"][str(self.entry.pk)]["suggestion"])

        # Leaving ends this browser's room.
        inv.post(path + "leave/")
        self.assertContains(inv.get(path), "Email me a code")

    def test_excel_downloads_get_header_footer_and_hidden_tag(self):
        self.ready()
        doc = self.vault(self.client, make_xlsx(), "Model.xlsx")
        r = self.client.post(f"/api/deals/{self.deal.pk}/room/files/", {"document": doc, "mode": "view"}, format="json")
        self.assertEqual(r.status_code, 400)  # Excel can't be view only
        self.add(self.client, doc, mode="download")
        Room.objects.update(nda_mode="none")
        self.share()
        path = self.link()
        inv = self.investor()
        self.open_room(inv, path)
        rf = RoomFile.objects.get()
        r = inv.get(f"{path}f/{rf.pk}/download/")
        self.assertEqual(r.status_code, 200)
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertIn("Ms. Priya Shah", wb["Cash flow"].oddHeader.left.text)
        self.assertIn("priya@northwind.example", wb.active.oddFooter.left.text)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            self.assertIn("DealflowTrace", z.read("docProps/custom.xml").decode())
        self.assertEqual(Visit.objects.get(kind="downloaded").trace, marks.find_trace(r.content)[0])

    def test_files_that_cant_be_watermarked_are_refused(self):
        self.ready()
        doc = self.vault(self.client, b"name,amount\nA,1\n", "List.csv")
        r = self.client.post(f"/api/deals/{self.deal.pk}/room/files/", {"document": doc, "mode": "download"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("watermarked", json.dumps(r.json()))

    def test_adding_files_needs_an_open_vault(self):
        self.ready()
        doc = self.vault(self.client, make_pdf(), "IM.pdf")
        self.client.post("/api/vault/lock/")
        r = self.client.post(f"/api/deals/{self.deal.pk}/room/files/", {"document": doc, "mode": "view"}, format="json")
        self.assertEqual(r.status_code, 403)


class ApprovalTests(RoomBase):
    def test_first_share_waits_for_the_md(self):
        role = self.make_role("Banker", EVERYTHING, share_with_investors=True)
        vp = self.make_user("vp@firm.com", role, full_name="Vikram VP")
        md = self.make_user("md@firm.com", role, full_name="Meena MD")
        self.deal.team_md.set([md])
        self.deal.team_vp.add(vp)
        vc = self.client_for(vp)
        self.ready(client=vc, user=vp)
        out = self.share(client=vc)
        self.assertEqual(out["shares"][0]["status"], "waiting")
        self.assertEqual(out["room"]["pending"]["requested_by"], "Vikram VP")
        self.assertEqual(self.sent_to("priya@northwind.example"), [])
        req = AccessRequest.objects.get(kind="room", status="pending")
        # The VP can't approve; the MD can.
        self.assertEqual(vc.post(f"/api/access-requests/{req.pk}/", {"decision": "approve"}, format="json").status_code, 400)
        mc = self.client_for(md)
        todo = mc.get("/api/access-requests/").json()["to_decide"]
        self.assertEqual(todo[0]["what"], "Share the data room with investors")
        with self.captureOnCommitCallbacks(execute=True):
            r = mc.post(f"/api/access-requests/{req.pk}/", {"decision": "approve"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Share.objects.get().status, "active")
        self.link()  # sent from the VP's mailbox
        self.assertEqual(self.sent_to("priya@northwind.example")[0]["From"].addresses[0].display_name, "Vikram VP")
        # Later shares go straight out.
        DealInvestor.objects.create(deal=self.deal, investor=self.northwind.__class__.objects.create(
            firm_name="Larkspur", created_by=self.admin, updated_by=self.admin), created_by=self.admin, updated_by=self.admin)
        e2 = DealInvestor.objects.exclude(pk=self.entry.pk).get()
        out = self.share(client=vc, entry=e2.pk, email="meera@larkspur.example", name="Meera Rao")
        self.assertTrue(all(s["status"] == "active" for s in out["shares"]))

    def test_who_can_share(self):
        plain = self.make_role("No sharing", EVERYTHING)
        flagged = self.make_role("Sharer", EVERYTHING, share_with_investors=True)
        viewer = self.make_role("Viewer", {"deals": ["view"]})
        a = self.make_user("a@firm.com", plain)
        b = self.make_user("b@firm.com", flagged)
        v = self.make_user("v@firm.com", viewer)
        self.deal.team_vp.add(a)
        self.assertEqual(self.share(client=self.client_for(a), status=403)["detail"][:9], "Your role")
        self.assertIn("deal team", self.share(client=self.client_for(b), status=403)["detail"])  # not on the deal
        out = self.client_for(v).get(f"/api/deals/{self.deal.pk}/room/").json()
        self.assertFalse(out["can"]["share"])
        self.assertEqual(self.client_for(v).patch(f"/api/deals/{self.deal.pk}/room/", {"name": "x"},
                                                  format="json").status_code, 403)

    def test_restricted_deal_room_is_hidden_with_it(self):
        role = self.make_role("Banker", EVERYTHING, share_with_investors=True)
        other = self.make_user("o@firm.com", role)
        self.deal.restricted = True
        self.deal.save(update_fields=["restricted"])
        self.assertEqual(self.client_for(other).get(f"/api/deals/{self.deal.pk}/room/").status_code, 403)

    def test_sharing_needs_a_mailbox(self):
        self.client.post("/api/vault/pin/", {"pin": "4826", "password": self.password}, format="json")
        r = self.share(status=400)
        self.assertIn("Connect your Gmail or Outlook", json.dumps(r))


class LinkTests(RoomBase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.add(self.client, self.vault(self.client, make_pdf(), "IM.pdf"))
        self.share()
        self.path = self.link()
        self.s = Share.objects.get()

    def test_stopped_expired_closed_and_withdrawn_links_dont_work(self):
        inv = self.investor()
        self.open_room(inv, self.path)
        self.client.patch(f"/api/rooms/shares/{self.s.pk}/", {"action": "stop"}, format="json")
        self.assertContains(inv.get(self.path), "This link doesn't work", status_code=404)
        Share.objects.update(status="active")
        Share.objects.update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
        self.assertEqual(inv.get(self.path).status_code, 404)
        Share.objects.update(expires_at=timezone.now() + timezone.timedelta(days=1))
        self.assertEqual(inv.get(self.path).status_code, 200)
        Room.objects.update(is_open=False)
        self.assertEqual(inv.get(self.path).status_code, 404)
        Room.objects.update(is_open=True)
        self.deal.status = "withdrawn"
        self.deal.save()
        self.assertEqual(inv.get(self.path).status_code, 404)
        self.assertEqual(inv.get("/r/not-a-real-token/").status_code, 404)

    def test_wrong_codes_pause_the_link(self):
        inv = self.investor()
        for _ in range(2):
            inv.post(self.path + "code/")
            for _ in range(5):
                inv.post(self.path + "verify/", {"code": "999999" if self.code() != "999999" else "888888"})
        self.s.refresh_from_db()
        self.assertTrue(self.s.locked_until and self.s.locked_until > timezone.now())
        r = inv.post(self.path + "code/")
        self.assertContains(r, "paused", status_code=429)

    def test_code_limits(self):
        inv = self.investor()
        for _ in range(5):
            self.assertEqual(inv.post(self.path + "code/").status_code, 302)
        self.assertContains(inv.post(self.path + "code/"), "most codes", status_code=429)

    def test_cookie_belongs_to_one_link_and_runs_out(self):
        inv = self.investor()
        self.open_room(inv, self.path)
        inv.post(self.path + "nda/", {"name": "Priya", "agree": "yes"})
        self.assertContains(inv.get(self.path), "Documents")
        from rooms.models import Visitor

        Visitor.objects.update(last_seen=timezone.now() - timezone.timedelta(minutes=31))
        self.assertContains(inv.get(self.path), "Email me a code")

    def test_signed_nda_waits_for_the_banker(self):
        self.client.patch(f"/api/deals/{self.deal.pk}/room/", {"nda_mode": "signed"}, format="json")
        inv = self.investor()
        self.open_room(inv, self.path)
        r = inv.get(self.path)
        self.assertContains(r, "opens once we've received your signed NDA")
        self.assertEqual(inv.post(self.path + "nda/", {"name": "Priya", "agree": "yes"}).status_code, 302)
        self.assertContains(inv.get(self.path), "signed NDA")  # still waiting
        self.client.patch(f"/api/rooms/shares/{self.s.pk}/", {"action": "nda_received"}, format="json")
        self.assertContains(inv.get(self.path), "Documents")

    def test_copy_link_and_audit_hides_the_token(self):
        r = self.client.get(f"/api/rooms/shares/{self.s.pk}/link/").json()
        self.assertTrue(r["link"].endswith(self.path))
        token = self.path.split("/")[2]
        self.assertEqual(crypto.token_hash(token), self.s.token_hash)
        entries = []
        with mock.patch("core.audit.append", side_effect=entries.append):
            self.investor().get(self.path)
            self.investor().post(self.path + "code/")
        self.assertTrue(entries)
        self.assertNotIn(token, json.dumps(entries))

    def test_removed_files_lose_their_copy(self):
        rf = RoomFile.objects.get()
        stored = Path(settings.MEDIA_ROOT, rf.stored.name)
        self.assertTrue(stored.exists())
        self.client.delete(f"/api/rooms/files/{rf.pk}/")
        self.assertFalse(stored.exists())


class HostTests(RoomBase):
    def test_investor_address_serves_only_rooms(self):
        with override_settings(ROOMS_DOMAIN="rooms.test", ALLOWED_HOSTS=["rooms.test", "crm.test", "testserver"]):
            c = APIClient()
            self.assertEqual(c.get("/api/auth/me/", HTTP_HOST="rooms.test").status_code, 404)
            self.assertEqual(c.get("/", HTTP_HOST="rooms.test").status_code, 404)
            self.assertEqual(c.get("/r/abc/", HTTP_HOST="rooms.test").status_code, 404)  # a room page (unknown link)
            self.assertContains(c.get("/r/abc/", HTTP_HOST="rooms.test"), "This link doesn't work", status_code=404)
            self.assertEqual(c.get("/r/abc/", HTTP_HOST="crm.test").content, b"Not found")
            self.assertEqual(c.get("/api/health/", HTTP_HOST="rooms.test").status_code, 200)

    def test_staff_networks(self):
        with override_settings(STAFF_NETWORKS="10.0.0.0/8"):
            c = APIClient()
            blocked = b"only open from the firm's network"
            self.assertIn(blocked, c.get("/api/auth/me/", REMOTE_ADDR="203.0.113.9").content)
            self.assertNotIn(blocked, c.get("/api/auth/me/", REMOTE_ADDR="10.1.2.3").content)
            self.assertNotIn(blocked, c.get("/r/abc/", REMOTE_ADDR="203.0.113.9").content)  # no rooms domain: rooms stay open


class LogTests(RoomBase):
    def test_links_never_reach_the_logs(self):
        import datetime
        import logging
        from types import SimpleNamespace

        from gunicorn.config import Config

        from dealflow.gunicorn_log import RedactingLogger
        from dealflow.logredact import RedactRoomLinks

        env = {"REMOTE_ADDR": "1.2.3.4", "RAW_URI": "/r/abcDEF_-12/f/3/", "QUERY_STRING": "", "REQUEST_METHOD": "GET",
               "SERVER_PROTOCOL": "HTTP/1.1", "PATH_INFO": "/r/abcDEF_-12/f/3/", "HTTP_REFERER": "https://x/r/abcDEF_-12/"}
        req = SimpleNamespace(method="GET", path=env["PATH_INFO"], query="", uri=env["RAW_URI"], version=(1, 1), headers=[])
        resp = SimpleNamespace(status="200 OK", headers=[], sent=1, response_length=1)
        atoms = RedactingLogger(Config()).atoms(resp, req, env, datetime.timedelta(seconds=0.1))
        self.assertNotIn("abcDEF", json.dumps({k: v for k, v in atoms.items() if isinstance(v, str)}))
        rec = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: %s",
                                ("/r/abcDEF_-12/f/3/",), None)
        RedactRoomLinks().filter(rec)
        self.assertEqual(rec.getMessage(), "Internal Server Error: /r/-/f/3/")
