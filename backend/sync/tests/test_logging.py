"""Email and calendar logging (decision 64)."""

import datetime as dt
import json
import urllib.parse
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: F401
from django.utils import timezone

from contacts.models import Contact
from core.models import Note
from core.tests.base import APITestBase
from deals.models import Deal, DealInvestor
from investors.models import Investor
from mail import crypto, providers as mail_providers
from mail.models import MailSettings, SentEmail
from outreach.models import Company, Outreach
from sync import engine, net
from sync.models import Connection, Copy, Item

from .fakes import FakeCloud, FakeDAV, FakeIMAPServer, mime

UTC = dt.timezone.utc
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer\n%%EOF\n" + b"term sheet " * 30


class SyncBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.cloud = FakeCloud()
        p = mock.patch.object(mail_providers, "TRANSPORT", self.cloud)
        p.start()
        self.addCleanup(p.stop)
        ms = MailSettings.load()
        ms.google_client_id, ms.google_client_secret = "g-client", crypto.seal("g-secret")
        ms.microsoft_client_id, ms.microsoft_client_secret = "o-client", crypto.seal("o-secret")
        ms.public_url = "https://crm.test"
        ms.save()
        a = self.admin
        self.northwind = Investor.objects.create(firm_name="Northwind Ventures", created_by=a, updated_by=a)
        self.priya = Contact.objects.create(full_name="Priya Shah", investor=self.northwind, work_email="priya@northwind.example",
                                            created_by=a, updated_by=a)
        self.zeta = Company.objects.create(name="Zeta Pharma", created_by=a, updated_by=a)
        self.ravi = Contact.objects.create(full_name="Ravi Menon", company=self.zeta, work_email="ravi@zeta.example",
                                           created_by=a, updated_by=a)
        self.deal = Deal.objects.create(company=self.zeta, stage="data_collection", created_by=a, updated_by=a)

    def connect(self, kind="google", client=None, mail=True, calendar=True, expect="connected"):
        c = client or self.client
        c.force_login(self.admin)
        r = c.post(f"/api/sync/connect/{kind}/", {"mail": mail, "calendar": calendar, "next": "/settings/preferences"},
                   format="json")
        self.assertEqual(r.status_code, 200, r.content)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json()["url"]).query))
        provider = "gmail" if kind == "google" else "outlook"
        back = c.get(f"/api/mail/callback/{provider}/", {"state": q["state"], "code": "good-code"})
        self.assertEqual(back.status_code, 302)
        self.assertIn(f"logging={expect}", back["Location"])
        return q

    def conn(self, user=None) -> Connection:
        return Connection.objects.get(user=user or self.admin)

    def run_sync(self, user=None, now=None):
        return engine.sync_connection(self.conn(user), now)

    def notes(self, ref):
        mkey, _, rid = ref.partition(":")
        return list(Note.objects.filter(module=mkey, record_id=int(rid)).exclude(kind="stage"))


class ConnectTests(SyncBase):
    def test_google_asks_only_for_what_was_picked(self):
        q = self.connect()
        self.assertIn("gmail.readonly", q["scope"])
        self.assertIn("calendar.events", q["scope"])
        self.assertNotIn("gmail.send", q["scope"])
        c = self.conn()
        self.assertEqual((c.kind, c.email, c.mail, c.calendar), ("google", "admin@firm.com", True, True))
        self.assertEqual(c.mail_cursor, {"history": "100"})
        self.assertNotIn("g-refresh", c.secret)  # sealed
        r = self.client.post("/api/sync/connect/google/", {"mail": False, "calendar": True}, format="json")
        self.assertNotIn("gmail.readonly", r.json()["url"])

    def test_missing_permission_connects_nothing(self):
        self.cloud.google_scope = "openid email https://www.googleapis.com/auth/calendar.events"
        self.connect(expect="error")
        self.assertFalse(Connection.objects.exists())

    def test_state_must_match(self):
        self.client.force_login(self.admin)
        self.client.post("/api/sync/connect/google/", {}, format="json")
        back = self.client.get("/api/mail/callback/gmail/", {"state": "forged", "code": "good-code"})
        self.assertIn("error", back["Location"])
        self.assertFalse(Connection.objects.exists())

    def test_not_in_the_sample_or_without_app_setup(self):
        ms = MailSettings.load()
        ms.google_client_id = ""
        ms.save()
        r = self.client.post("/api/sync/connect/google/", {}, format="json")
        self.assertEqual(r.json()["code"], "mail_setup")

    def test_overview_for_admins(self):
        self.connect()
        rows = self.client.get("/api/settings/email/logging/").json()
        me = next(r for r in rows if r["email"] == "admin@firm.com")
        self.assertEqual(me["connections"][0]["label"], "Google")
        member = self.make_user("m@firm.com", self.make_role("Member+", manage_settings=True))
        self.assertEqual(self.client_for(member).get("/api/settings/email/logging/").status_code, 403)

    def test_disconnect_keeps_the_log(self):
        self.connect()
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Numbers", "Here they are"))
        self.run_sync()
        r = self.client.delete(f"/api/sync/connections/{self.conn().pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(len(self.notes(f"contacts:{self.ravi.pk}")), 1)
        notes = self.client.get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()
        self.assertFalse(notes[0]["can_open"])


class EmailLoggingTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_email_with_a_contact_is_logged_on_contact_firm_and_deal(self):
        self.cloud.add_gmail("m1", mime("Ravi Menon <ravi@zeta.example>", ["admin@firm.com"], "Q3 numbers", "Attached.",
                                        attachments=[("Model.xlsx", "application/vnd.ms-excel", b"PK..")]))
        counts = self.run_sync()
        self.assertEqual(counts["emails"], 1)
        item = Item.objects.get()
        self.assertEqual(set(item.records), {f"contacts:{self.ravi.pk}", f"companies:{self.zeta.pk}", f"deals:{self.deal.pk}"})
        self.assertEqual(item.attachments[0]["name"], "Model.xlsx")
        for ref in item.records:
            if ref.startswith("companies"):
                continue  # companies keep no log of their own
            n = self.notes(ref)
            self.assertEqual(len(n), 1, ref)
            self.assertEqual((n[0].kind, n[0].text, n[0].meta["subject"]), ("email", "", "Q3 numbers"))
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.last_contacted, timezone.localdate())
        # Nothing new the second time
        self.assertEqual(self.run_sync()["emails"], 0)
        self.assertEqual(Item.objects.count(), 1)

    def test_only_people_in_dealflow_and_never_colleagues_only(self):
        self.cloud.add_gmail("m1", mime("colleague@firm.com", ["admin@firm.com"], "Lunch?", "x"))
        self.cloud.add_gmail("m2", mime("stranger@else.com", ["admin@firm.com"], "Hello", "x"))
        self.cloud.add_gmail("m3", mime("spam@zeta.example", ["admin@firm.com"], "Junk", "x"))
        self.cloud.add_gmail("m4", mime("ravi@zeta.example", ["admin@firm.com"], "Spam folder", "x"), labels=["SPAM"])
        self.cloud.add_gmail("m5", mime("ravi@zeta.example", ["admin@firm.com"], "Draft", "x"), labels=["DRAFT"])
        self.assertEqual(self.run_sync()["emails"], 0)
        self.assertFalse(Item.objects.exists())

    def test_nothing_from_before_connecting(self):
        old = mime("ravi@zeta.example", ["admin@firm.com"], "Old news", "x")
        self.cloud.add_gmail("m0", old, at=timezone.now() - dt.timedelta(days=30))
        self.assertEqual(self.run_sync()["emails"], 0)

    def test_several_open_deals_ask_the_owner(self):
        a = self.admin
        second = Outreach.objects.create(company=self.zeta, stage="pitched", created_by=a, updated_by=a)
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Which one?", "x"))
        self.run_sync()
        item = Item.objects.get()
        self.assertEqual(set(item.choices), {f"deals:{self.deal.pk}", f"outreach:{second.pk}"})
        self.assertEqual(self.notes(f"deals:{self.deal.pk}"), [])
        agenda = self.client.get("/api/sync/agenda/").json()
        self.assertEqual(agenda["to_file"][0]["id"], item.pk)
        log = self.client.get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()[0]
        self.assertEqual({c["ref"] for c in log["choices"]}, set(item.choices))
        r = self.client.post(f"/api/sync/items/{item.pk}/file/", {"ref": f"deals:{self.deal.pk}"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(self.notes(f"deals:{self.deal.pk}")), 1)
        self.assertEqual(self.client.get("/api/sync/agenda/").json()["to_file"], [])
        self.assertEqual(self.client.post(f"/api/sync/items/{item.pk}/file/", {"ref": None}, format="json").status_code, 400)

    def test_choosing_none(self):
        a = self.admin
        Outreach.objects.create(company=self.zeta, stage="pitched", created_by=a, updated_by=a)
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "General", "x"))
        self.run_sync()
        item = Item.objects.get()
        r = self.client.post(f"/api/sync/items/{item.pk}/file/", {"ref": None}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Item.objects.get().choices, [])

    def test_investor_contact_goes_on_their_tracker_entry(self):
        a = self.admin
        entry = DealInvestor.objects.create(deal=self.deal, investor=self.northwind, created_by=a, updated_by=a)
        self.cloud.add_gmail("m1", mime("admin@firm.com", ["priya@northwind.example"], "Teaser", "x"), labels=["SENT"])
        self.run_sync()
        item = Item.objects.get()
        self.assertIn(f"deal_investors:{entry.pk}", item.records)
        self.assertIn(f"investors:{self.northwind.pk}", item.records)
        self.northwind.refresh_from_db()
        self.assertEqual(self.northwind.last_contacted, timezone.localdate())

    def test_only_the_owner_reads_the_text(self):
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Private", "The valuation is 400"))
        self.run_sync()
        item = Item.objects.get()
        r = self.client.get(f"/api/sync/items/{item.pk}/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("The valuation is 400", r.json()["text"])
        self.assertFalse(Note.objects.filter(text__contains="valuation").exists())  # never stored
        member = self.make_user("vp@firm.com", self.make_role("Member+", {m: ["view", "create", "edit"] for m in
                                                                          ("contacts", "deals", "outreach", "investors")}))
        c = self.client_for(member)
        self.assertEqual(c.get(f"/api/sync/items/{item.pk}/").status_code, 403)
        log = c.get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()[0]
        self.assertEqual(log["meta"]["subject"], "Private")
        self.assertFalse(log["can_open"])
        self.assertEqual({p["email"] for p in log["meta"]["participants"]}, {"ravi@zeta.example", "admin@firm.com"})
        self.assertFalse(log["can_edit"])
        self.assertEqual(c.patch(f"/api/notes/{log['id']}/", {"text": "x"}, format="json").status_code, 403)

    def test_same_email_in_two_mailboxes_is_logged_once(self):
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com", "vp@firm.com"], "Both of us", "x",
                                        message_id="<shared@zeta.example>"))
        self.run_sync()
        vp = self.make_user("vp@firm.com", self.admin_role)
        conn2 = Connection.objects.create(user=vp, kind="google", email="vp@firm.com", secret=self.conn().secret,
                                          mail_cursor={"history": "100"}, calendar=False,
                                          connected_at=timezone.now() - dt.timedelta(minutes=1))
        engine.sync_connection(conn2)
        self.assertEqual(Item.objects.count(), 1)
        self.assertEqual(Copy.objects.count(), 2)
        self.assertEqual(len(self.notes(f"contacts:{self.ravi.pk}")), 1)
        self.assertEqual(self.client_for(vp).get(f"/api/sync/items/{Item.objects.get().pk}/").status_code, 200)

    def test_restricted_deal_subject_is_withheld(self):
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Project Falcon terms", "x"))
        self.run_sync()
        Deal.objects.filter(pk=self.deal.pk).update(restricted=True)
        member = self.make_user("an@firm.com", self.make_role("Analyst+", {m: ["view"] for m in
                                                                           ("contacts", "deals", "outreach", "investors")}))
        log = self.client_for(member).get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()[0]
        self.assertEqual(log["meta"]["subject"], "About a restricted deal")
        mine = self.client.get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()[0]
        self.assertEqual(mine["meta"]["subject"], "Project Falcon terms")

    def test_hidden_email_field_hides_addresses(self):
        from accounts.models import RoleFieldAccess

        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Hi", "x"))
        self.run_sync()
        role = self.make_role("NoEmails", {m: ["view"] for m in ("contacts", "deals", "outreach", "investors")})
        RoleFieldAccess.objects.create(role=role, module="contacts", field="work_email", level="hidden")
        log = self.client_for(self.make_user("x@firm.com", role)).get(f"/api/m/contacts/{self.ravi.pk}/notes/").json()[0]
        ravi = next(p for p in log["meta"]["participants"] if p["contact"])
        self.assertEqual(ravi["email"], "")

    def test_sent_from_dealflow_isnt_logged_twice(self):
        SentEmail.objects.create(user=self.admin, provider="gmail", from_email="admin@firm.com", to=["ravi@zeta.example"],
                                 subject="From the composer", message_id="m1", status="confirmed")
        self.cloud.add_gmail("m1", mime("admin@firm.com", ["ravi@zeta.example"], "From the composer", "x"), labels=["SENT"])
        self.run_sync()
        self.assertEqual(self.notes(f"contacts:{self.ravi.pk}"), [])
        self.assertEqual(Item.objects.get().records, [])

    def test_save_attachment_to_vault(self):
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "IM", "see attached",
                                        attachments=[("IM.pdf", "application/pdf", PDF)]))
        self.run_sync()
        item = Item.objects.get()
        r = self.client.post(f"/api/sync/items/{item.pk}/save/", {"index": 0}, format="json")
        self.assertEqual(r.status_code, 403)  # Vault closed
        self.client.post("/api/vault/pin/", {"pin": "4826", "password": self.password}, format="json")
        r = self.client.post(f"/api/sync/items/{item.pk}/save/", {"index": 0}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        doc = r.json()
        self.assertEqual(doc["title"], "IM")
        tags = {f"{t['module']}:{t['id']}" for t in doc["tagged"]}
        self.assertEqual(tags, {f"companies:{self.zeta.pk}", f"deals:{self.deal.pk}"})
        dl = self.client.get(f"/api/vault/{doc['id']}/download/")
        self.assertEqual(b"".join(dl.streaming_content), PDF)
        self.assertEqual(self.client.post(f"/api/sync/items/{item.pk}/save/", {"index": 5}, format="json").status_code, 400)

    def test_lost_permission_needs_reconnect(self):
        self.cloud.fail.add("gmail")
        c = self.conn()
        c.secret = crypto.seal({"access": "dead", "refresh": "", "expires": 0})
        c.save()
        counts = self.run_sync()
        self.assertIn("error", counts)
        self.assertTrue(self.conn().needs_reconnect)
        self.assertEqual(engine.tick(), 0)  # not tried again until reconnected


class MeetingTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.now = timezone.now()

    def test_upcoming_meeting_then_logged_when_it_ends(self):
        start = self.now + dt.timedelta(minutes=10)
        self.cloud.add_gcal("e1", start, 30, "Zeta kickoff", ["ravi@zeta.example", "vp@firm.com"], description="Agenda: data")
        self.assertEqual(self.run_sync()["meetings"], 1)
        item = Item.objects.get(kind="meeting")
        self.assertFalse(item.logged)
        self.assertEqual(self.notes(f"contacts:{self.ravi.pk}"), [])
        on_deal = self.client.get(f"/api/sync/meetings/?about=deals:{self.deal.pk}").json()
        self.assertEqual(on_deal[0]["subject"], "Zeta kickoff")
        on_contact = self.client.get(f"/api/sync/meetings/?about=contacts:{self.ravi.pk}").json()
        self.assertEqual(len(on_contact), 1)
        agenda = self.client.get("/api/sync/agenda/").json()
        self.assertEqual(agenda["meetings"][0]["id"], item.pk)
        self.assertEqual(agenda["soon"], [item.pk])  # within 15 minutes
        self.client.put("/api/sync/", {"reminder_minutes": 5}, format="json")
        self.assertEqual(self.client.get("/api/sync/agenda/").json()["soon"], [])
        self.client.put("/api/sync/", {"reminder_minutes": None}, format="json")
        self.assertEqual(self.client.put("/api/sync/", {"reminder_minutes": 7}, format="json").status_code, 400)
        detail = self.client.get(f"/api/sync/items/{item.pk}/").json()
        self.assertEqual(detail["description"], "Agenda: data")
        # It ends
        engine.finish_meetings(start + dt.timedelta(minutes=31))
        n = self.notes(f"deals:{self.deal.pk}")
        self.assertEqual((len(n), n[0].kind), (1, "meeting"))
        self.assertEqual(self.notes(f"contacts:{self.ravi.pk}")[0].created_by, self.admin)

    def test_cancelled_or_removed_meetings_drop_out(self):
        self.cloud.add_gcal("e1", self.now + dt.timedelta(days=1), 30, "Call", ["ravi@zeta.example"])
        self.cloud.add_gcal("e2", self.now + dt.timedelta(days=2), 30, "Call 2", ["ravi@zeta.example"])
        self.run_sync()
        self.cloud.gcal["e1"]["status"] = "cancelled"
        del self.cloud.gcal["e2"]
        self.conn().__class__.objects.update(synced_at=None)
        self.run_sync()
        self.assertEqual(Item.objects.filter(cancelled=True).count(), 2)
        engine.finish_meetings(self.now + dt.timedelta(days=3))
        self.assertEqual(self.notes(f"contacts:{self.ravi.pk}"), [])

    def test_meetings_without_contacts_are_ignored(self):
        self.cloud.add_gcal("e1", self.now + dt.timedelta(hours=1), 30, "Team sync", ["vp@firm.com"])
        self.cloud.add_gcal("e2", self.now + dt.timedelta(hours=1), 30, "Dentist")
        self.assertEqual(self.run_sync()["meetings"], 0)

    def test_schedule_meeting_from_a_deal(self):
        start = (self.now + dt.timedelta(days=2)).replace(microsecond=0)
        r = self.client.post("/api/sync/schedule/", {
            "about": f"deals:{self.deal.pk}", "title": "Data review", "start": start.isoformat(), "minutes": 45,
            "attendees": [{"email": "ravi@zeta.example", "name": "Ravi Menon"}], "location": "Zoom", "notes": "Bring the model",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        made = self.cloud.created[0]
        self.assertEqual(made["sendUpdates"], "all")
        self.assertEqual(made["attendees"][0]["email"], "ravi@zeta.example")
        item = Item.objects.get(kind="meeting")
        self.assertIn(f"deals:{self.deal.pk}", item.records)
        self.assertEqual(self.client.get(f"/api/sync/meetings/?about=deals:{self.deal.pk}").json()[0]["subject"],
                         "Data review")
        bad = self.client.post("/api/sync/schedule/", {"about": f"deals:{self.deal.pk}", "title": "x",
                                                       "start": start.isoformat(), "attendees": ["not-an-email"]},
                               format="json")
        self.assertEqual(bad.status_code, 400)
        past = self.client.post("/api/sync/schedule/", {"about": f"deals:{self.deal.pk}", "title": "x",
                                                        "start": (self.now - dt.timedelta(days=1)).isoformat(),
                                                        "attendees": ["ravi@zeta.example"]}, format="json")
        self.assertEqual(past.status_code, 400)

    def test_schedule_needs_a_calendar(self):
        Connection.objects.all().delete()
        r = self.client.post("/api/sync/schedule/", {"about": f"deals:{self.deal.pk}", "title": "x",
                                                     "start": (self.now + dt.timedelta(days=1)).isoformat(),
                                                     "attendees": ["ravi@zeta.example"]}, format="json")
        self.assertEqual(r.json()["code"], "no_calendar")


class MicrosoftTests(SyncBase):
    def test_outlook_mail_attachments_and_calendar(self):
        q = self.connect("microsoft")
        self.assertIn("Mail.Read", q["scope"])
        self.assertNotIn("Mail.Send", q["scope"])
        self.cloud.add_graph("g1", "ravi@zeta.example", ["admin@firm.com"], "Deck", text="Body here",
                             attachments=[("Deck.pdf", "application/pdf", PDF)])
        self.cloud.add_graph("g2", "ravi@zeta.example", ["admin@firm.com"], "Junk mail", folder="junkemail")
        self.cloud.add_graph_event("ev1", timezone.now() + dt.timedelta(hours=2), 30, "Site visit", ["ravi@zeta.example"])
        counts = self.run_sync()
        self.assertEqual((counts["emails"], counts["meetings"]), (1, 1))
        item = Item.objects.get(kind="email")
        self.assertEqual(item.attachments[0]["name"], "Deck.pdf")
        self.assertIn("Body here", self.client.get(f"/api/sync/items/{item.pk}/").json()["text"])
        meeting = Item.objects.get(kind="meeting")
        self.assertEqual(self.client.get(f"/api/sync/items/{meeting.pk}/").json()["location"], "Their office")


class PasswordSourceTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.imap = FakeIMAPServer()
        self.dav = FakeDAV()
        for target, fake in ((net, "IMAP"), (net, "HTTP")):
            p = mock.patch.object(target, fake, self.imap if fake == "IMAP" else self.dav)
            p.start()
            self.addCleanup(p.stop)

    def connect_other(self, **kw):
        data = {"email": "banker@icloud.com", "password": "abcd-efgh-ijkl-mnop", "imap_host": "imap.example.com",
                "imap_port": 993, "caldav_url": FakeDAV.BASE + "/", **kw}
        return self.client.post("/api/sync/connect/other/", data, format="json")

    def test_wrong_password_is_refused(self):
        r = self.connect_other(password="wrong")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Connection.objects.exists())

    def test_imap_and_caldav(self):
        self.imap.add("INBOX", mime("someone@else.com", ["banker@icloud.com"], "before", "x"))
        r = self.connect_other()
        self.assertEqual(r.status_code, 201, r.content)
        conn = self.conn()
        self.assertEqual(conn.calendar_cursor["calendars"][0]["name"], "Work")  # the task list was left out
        self.assertEqual(conn.mail_cursor["folders"]["INBOX"]["last"], 1)
        self.imap.add("INBOX", mime("Ravi <ravi@zeta.example>", ["banker@icloud.com"], "=?utf-8?q?Caf=C3=A9_terms?=",
                                    "Text body", attachments=[("Terms.pdf", "application/pdf", PDF)]))
        self.imap.add("Sent Messages", mime("banker@icloud.com", ["priya@northwind.example"], "Teaser", "x"))
        start = (timezone.now() + dt.timedelta(days=1)).astimezone(UTC)
        self.dav.events["/home/work/a.ics"] = "\r\n".join([
            "BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:abc-1", f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{start + dt.timedelta(hours=1):%Y%m%dT%H%M%SZ}", "SUMMARY:Lunch with Ravi",
            "ORGANIZER;CN=Banker:mailto:banker@icloud.com", 'ATTENDEE;CN="Ravi Menon";PARTSTAT=ACCEPTED:mailto:ravi@zeta.example',
            "DESCRIPTION:Discuss\\, the plan", "END:VEVENT", "END:VCALENDAR", ""])
        counts = self.run_sync()
        self.assertEqual((counts["emails"], counts["meetings"]), (2, 1), counts)
        cafe = Item.objects.get(subject="Café terms")
        self.assertEqual(cafe.attachments[0]["name"], "Terms.pdf")
        self.assertIn("Text body", self.client.get(f"/api/sync/items/{cafe.pk}/").json()["text"])
        meeting = Item.objects.get(kind="meeting")
        self.assertEqual(self.client.get(f"/api/sync/items/{meeting.pk}/").json()["description"], "Discuss, the plan")
        # schedule on the CalDAV calendar
        r = self.client.post("/api/sync/schedule/", {"about": f"contacts:{self.ravi.pk}", "title": "Follow-up",
                                                     "start": (start + dt.timedelta(days=1)).isoformat(), "minutes": 30,
                                                     "attendees": ["ravi@zeta.example"]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        path, body = self.dav.puts[0]
        body = body.replace("\r\n ", "")  # unfold
        self.assertIn("ATTENDEE", body)
        self.assertIn("mailto:ravi@zeta.example", body)

    def test_icloud_uses_fixed_servers(self):
        r = self.client.post("/api/sync/connect/icloud/", {"email": "banker@icloud.com", "password": "abcd-efgh-ijkl-mnop",
                                                           "mail": True, "calendar": False}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        from sync.providers import secret

        self.assertEqual(secret(self.conn())["imap_host"], "imap.mail.me.com")


class GuardTests(APITestBase):
    def test_private_addresses_are_refused(self):
        from mail.providers import MailError

        for host in ("localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]", "a/b"):
            with self.assertRaises(MailError, msg=host):
                net.resolve(host.strip("[]"), 993)

    def test_dtd_is_refused(self):
        from mail.providers import MailError
        from sync.providers import PasswordSource

        with self.assertRaises(MailError):
            PasswordSource._xml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')


class IcsTests(APITestBase):
    def test_parse(self):
        from sync import ics

        text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART;TZID=Asia/Kolkata:20261101T100000\r\n"
                "DURATION:PT90M\r\nSUMMARY:Long \r\n title\r\nATTENDEE;CN=\"Menon, Ravi\";PARTSTAT=ACCEPTED:MAILTO:Ravi@Zeta.example\r\n"
                "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nDESCRIPTION:alarm\r\nEND:VALARM\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nUID:2\r\nDTSTART;VALUE=DATE:20261102\r\nSUMMARY:Holiday\r\nEND:VEVENT\r\nEND:VCALENDAR")
        a, b = ics.parse(text)
        self.assertEqual(a.start, dt.datetime(2026, 11, 1, 4, 30, tzinfo=UTC))
        self.assertEqual(a.end - a.start, dt.timedelta(minutes=90))
        self.assertEqual(a.summary, "Long title")
        self.assertEqual(a.attendees, [("Menon, Ravi", "ravi@zeta.example")])
        self.assertEqual(a.description, "")  # the alarm's description isn't the event's
        self.assertTrue(b.all_day)
        uid, inv = ics.invitation(organizer=("Asha", "a@firm.com"), attendees=[("Ravi", "ravi@zeta.example")],
                                  start=a.start, end=a.end, summary="Ünïcode, title; " + "long " * 30)
        back = ics.parse(inv)[0]
        self.assertEqual(back.summary, "Ünïcode, title; " + "long " * 30)
        self.assertTrue(all(len(line.encode()) <= 75 for line in inv.split("\r\n")))


class SchedulerTests(SyncBase):
    def test_tick_reads_due_connections(self):
        self.connect()
        self.cloud.add_gmail("m1", mime("ravi@zeta.example", ["admin@firm.com"], "Hello", "x"))
        self.assertEqual(engine.tick(), 1)
        self.assertEqual(engine.tick(), 0)  # read a moment ago
        self.assertEqual(Item.objects.count(), 1)
        self.assertEqual(json.loads(json.dumps(self.client.get("/api/sync/").json()))["connections"][0]["email"],
                         "admin@firm.com")
