"""Email from Dealflow: set-up, connecting mailboxes, one-to-one sends confirmed in the Sent
folder and logged, campaigns sent in the background within limits."""

import datetime as dt
import json
import urllib.parse
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from contacts.models import Contact
from core.models import FirmSettings, Note
from core.tests.base import APITestBase
from deals.models import Deal
from investors.models import Investor
from mail import campaigns, crypto, providers, service
from mail.models import Campaign, MailAccount, MailSettings, SentEmail
from outreach.models import Company

from .fakes import FakeMail

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer\n%%EOF\n"
EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


class MailBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMail()
        p1 = mock.patch.object(providers, "TRANSPORT", self.fake)
        p2 = mock.patch.object(providers, "CONFIRM_WAITS", (0, 0))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        ms = MailSettings.load()
        ms.google_client_id, ms.google_client_secret = "g-client", crypto.seal("g-secret")
        ms.microsoft_client_id, ms.microsoft_client_secret = "o-client", crypto.seal("o-secret")
        ms.public_url = "https://crm.test"
        ms.save()
        a = self.admin
        self.blume = Investor.objects.create(firm_name="Blume Ventures", created_by=a, updated_by=a)
        self.priya = Contact.objects.create(full_name="Ms. Priya Shah", designation="Partner", investor=self.blume,
                                            work_email="priya@blume.vc", primary_contact=True, created_by=a, updated_by=a)
        self.zeta = Company.objects.create(name="Zeta Pharma", created_by=a, updated_by=a)
        self.ravi = Contact.objects.create(full_name="Ravi Menon", company=self.zeta, work_email="ravi@zeta.in",
                                           created_by=a, updated_by=a)
        self.deal = Deal.objects.create(company=self.zeta, stage="data_collection", created_by=a, updated_by=a)

    def connect(self, provider="gmail", client=None, user=None):
        user = user or self.admin
        c = client or self.client
        c.force_login(user)
        r = c.post(f"/api/mail/connect/{provider}/", {"next": "/settings/preferences"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json()["url"]).query))
        self.assertEqual(q["redirect_uri"], f"https://crm.test/api/mail/callback/{provider}/")
        self.assertEqual(q["code_challenge_method"], "S256")
        r = c.get(f"/api/mail/callback/{provider}/", {"code": "good-code", "state": q["state"]})
        self.assertEqual(r.status_code, 302)
        self.assertIn("mailbox=connected", r["Location"])
        return MailAccount.objects.get(user=user)

    def send(self, client=None, status=201, **body):
        data = {"about": f"deals:{self.deal.pk}", "to": ["ravi@zeta.in"], "subject": "Data request",
                "body": "Hi Ravi,\n\nPlease share the FY25 numbers.", **body}
        r = (client or self.client).post("/api/mail/send/", data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()


class SetupTests(MailBase):
    def test_settings_need_admin_and_password_and_hide_secrets(self):
        r = self.client.get("/api/mail/settings/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["gmail"]["has_secret"])
        self.assertNotIn("g-secret", json.dumps(body))
        self.assertEqual(body["outlook"]["redirect_uri"], "https://crm.test/api/mail/callback/outlook/")
        r = self.client.put("/api/mail/settings/", {"gmail_client_id": "new-id"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/mail/settings/", {"gmail_client_id": "new-id", "gmail_client_secret": "",
                                                    "per_minute": 5, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        ms = MailSettings.load()
        self.assertEqual((ms.google_client_id, ms.per_minute), ("new-id", 5))
        self.assertEqual(crypto.unseal(ms.google_client_secret), "g-secret")  # blank leaves it
        member = self.make_user("m@firm.com", self.make_role("Member+", EVERYTHING, manage_settings=True))
        self.assertEqual(self.client_for(member).get("/api/mail/settings/").status_code, 403)
        r = self.client.put("/api/mail/settings/", {"public_url": "javascript:alert(1)", "password": self.password},
                            format="json")
        self.assertEqual(r.status_code, 400)

    def test_connect_gmail_stores_tokens_encrypted(self):
        acct = self.connect("gmail")
        self.assertEqual((acct.provider, acct.email), ("gmail", "asha@gmail.com"))
        self.assertNotIn("g-access", acct.tokens)
        self.assertEqual(crypto.unseal(acct.tokens)["refresh"], "g-refresh")
        r = self.client.get("/api/mail/account/").json()
        self.assertEqual(r["account"]["email"], "asha@gmail.com")
        self.assertNotIn("tokens", json.dumps(r))

    def test_connect_outlook(self):
        acct = self.connect("outlook")
        self.assertEqual((acct.provider, acct.email), ("outlook", "asha@firm.com"))

    def test_bad_state_or_refusal_connects_nothing(self):
        self.client.force_login(self.admin)
        self.client.post("/api/mail/connect/gmail/", {}, format="json")
        r = self.client.get("/api/mail/callback/gmail/", {"code": "good-code", "state": "wrong"})
        self.assertIn("mailbox=error", r["Location"])
        self.client.post("/api/mail/connect/gmail/", {}, format="json")
        state = self.client.session["mail_connect"]["state"]
        r = self.client.get("/api/mail/callback/gmail/", {"error": "access_denied", "state": state})
        self.assertIn("permission", urllib.parse.unquote_plus(r["Location"]))
        self.assertFalse(MailAccount.objects.exists())

    def test_gmail_without_send_permission_is_refused(self):
        self.fake.gmail_scope = "openid email"
        self.client.force_login(self.admin)
        r = self.client.post("/api/mail/connect/gmail/", {}, format="json")
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json()["url"]).query))["state"]
        r = self.client.get("/api/mail/callback/gmail/", {"code": "good-code", "state": state})
        self.assertIn("mailbox=error", r["Location"])
        self.assertFalse(MailAccount.objects.exists())

    def test_not_set_up_and_sample_mode(self):
        ms = MailSettings.load()
        ms.microsoft_client_id = ""
        ms.save()
        r = self.client.post("/api/mail/connect/outlook/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "mail_setup")
        fs = FirmSettings.load()
        fs.sample_loaded_at = timezone.now()
        fs.save()
        self.assertEqual(self.client.post("/api/mail/connect/gmail/", {}, format="json").status_code, 403)

    def test_disconnect(self):
        self.connect("gmail")
        self.assertEqual(self.client.delete("/api/mail/account/").status_code, 204)
        self.assertFalse(MailAccount.objects.exists())
        self.assertTrue(any("revoke" in c[1] for c in self.fake.calls))


class SendTests(MailBase):
    def test_gmail_send_is_confirmed_and_logged(self):
        self.connect("gmail")
        up = self.client.post("/api/mail/attachments/",
                              {"files": [SimpleUploadedFile("Teaser.pdf", PDF, content_type="application/pdf")]},
                              format="multipart")
        self.assertEqual(up.status_code, 201, up.content)
        token = up.json()[0]["token"]
        r = self.send(cc=["someone@else.com"], attachments=[{"kind": "upload", "token": token}])
        self.assertEqual(r["status"], "confirmed")
        self.assertIn("mail.google.com", r["web_link"])
        msg = self.fake.gmail_sent[0]
        self.assertEqual(msg["Subject"], "Data request")
        self.assertIn("ravi@zeta.in", msg["To"])
        self.assertIn("Ravi Menon", msg["To"])
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["Teaser.pdf"])
        # Logged on the deal and on Ravi's contact page, confirmed.
        notes = Note.objects.filter(kind="email").order_by("module")
        self.assertEqual(sorted((n.module, n.record_id) for n in notes),
                         [("contacts", self.ravi.pk), ("deals", self.deal.pk)])
        self.assertEqual(notes[0].meta["status"], "confirmed")
        self.assertEqual(notes[0].meta["attachments"], ["Teaser.pdf"])
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.last_contacted, timezone.localdate())
        self.assertEqual(self.ravi.updated_by, self.admin)
        # The log shows it, and email entries can't be edited.
        log = self.client.get(f"/api/m/deals/{self.deal.pk}/notes/").json()
        self.assertEqual(log[0]["kind"], "email")
        self.assertFalse(log[0]["can_edit"])
        self.assertEqual(self.client.patch(f"/api/notes/{log[0]['id']}/", {"text": "x"}, format="json").status_code, 403)

    def test_email_to_an_investor_contact_moves_the_investor_too(self):
        self.connect("gmail")
        self.send(about=f"investors:{self.blume.pk}", to=["priya@blume.vc"])
        self.blume.refresh_from_db()
        self.assertEqual(self.blume.last_contacted, timezone.localdate())
        self.assertEqual(Note.objects.filter(module="investors", record_id=self.blume.pk, kind="email").count(), 1)

    def test_last_contacted_never_moves_backwards(self):
        self.connect("gmail")
        later = timezone.localdate() + dt.timedelta(days=3)
        Contact.objects.filter(pk=self.ravi.pk).update(last_contacted=later)
        self.send()
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.last_contacted, later)

    def test_outlook_confirms_now_or_later(self):
        self.connect("outlook")
        r = self.send()
        self.assertEqual(r["status"], "confirmed")
        self.assertTrue(r["web_link"].startswith("https://outlook.office.com"))
        # Exchange slow to file it: waiting, then confirmed by the scheduler.
        self.fake.outlook_instant = False
        r = self.send(subject="Second")
        self.assertEqual(r["status"], "pending")
        Contact.objects.filter(pk=self.ravi.pk).update(last_contacted=None)
        se = SentEmail.objects.get(pk=r["id"])
        self.fake.deliver(se.message_id)
        service.recheck_pending()
        se.refresh_from_db()
        self.assertEqual(se.status, "confirmed")
        self.assertTrue(all(n.meta["status"] == "confirmed" for n in Note.objects.filter(pk__in=se.note_ids)))
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.last_contacted, timezone.localdate())

    def test_outlook_never_filed_is_marked_not_found(self):
        self.connect("outlook")
        self.fake.outlook_instant = False
        r = self.send()
        se = SentEmail.objects.get(pk=r["id"])
        SentEmail.objects.filter(pk=se.pk).update(sent_at=timezone.now() - dt.timedelta(hours=3))
        service.recheck_pending()
        se.refresh_from_db()
        self.assertEqual(se.status, "unconfirmed")
        self.ravi.refresh_from_db()
        self.assertIsNone(self.ravi.last_contacted)

    def test_outlook_large_attachment_goes_up_in_pieces(self):
        self.connect("outlook")
        big = b"%PDF-1.7\n" + b"0" * (4 * 1024 * 1024)
        up = self.client.post("/api/mail/attachments/",
                              {"files": [SimpleUploadedFile("IM.pdf", big, content_type="application/pdf")]},
                              format="multipart").json()
        self.send(attachments=[{"kind": "upload", "token": up[0]["token"]}])
        m = next(iter(self.fake.graph.values()))
        self.assertEqual(m["attachments"], [])
        (name, url), = m["uploaded"]
        self.assertEqual((name, bytes(self.fake.uploads[url])), ("IM.pdf", big))
        # The upload address never gets the bearer token.
        self.assertTrue(all("Authorization" not in h for meth, u, h, b in self.fake.calls if "upload.fake" in u))

    def test_expired_token_is_refreshed(self):
        acct = self.connect("gmail")
        tokens = crypto.unseal(acct.tokens)
        self.fake.valid.discard(tokens["access"])  # revoked early: refused once, then refreshed
        self.assertEqual(self.send()["status"], "confirmed")
        tokens["expires"] = 0
        MailAccount.objects.filter(pk=acct.pk).update(tokens=crypto.seal({**crypto.unseal(
            MailAccount.objects.get(pk=acct.pk).tokens), "expires": 0}))
        self.assertEqual(self.send()["status"], "confirmed")

    def test_lost_permission_asks_to_reconnect(self):
        acct = self.connect("gmail")
        self.fake.valid.clear()
        self.fake.refresh_ok = False
        r = self.send(status=400)
        self.assertEqual(r["code"], "mail_reconnect")
        acct.refresh_from_db()
        self.assertTrue(acct.needs_reconnect)
        self.assertFalse(Note.objects.filter(kind="email").exists())

    def test_provider_limit_and_rejection(self):
        self.connect("gmail")
        self.fake.fail_next = [(429, {}, b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}')]
        self.assertEqual(self.send(status=429)["code"], "mail_limit")
        self.fake.fail_next = [(400, {}, b'{"error":{"message":"Invalid To header"}}')]
        self.assertIn("Invalid To header", self.send(status=400)["detail"])

    def test_daily_limit(self):
        acct = self.connect("gmail")
        MailAccount.objects.filter(pk=acct.pk).update(sent_day=timezone.localdate(), sent_today=400)
        self.assertEqual(self.send(status=429)["code"], "mail_limit")

    def test_validation_and_permissions(self):
        self.connect("gmail")
        self.assertIn("isn't an email", self.send(status=400, to=["not an email"])["detail"])
        self.send(status=400, to=[])
        self.send(status=400, subject="  ")
        self.send(status=400, about="tasks:1")
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"deals": ["view"], "contacts": ["view"]}))
        vc = self.client_for(viewer)
        self.assertEqual(vc.post("/api/mail/send/", {"about": f"deals:{self.deal.pk}", "to": ["a@b.co"],
                                                     "subject": "x", "body": "y"}, format="json").status_code, 403)
        # Not connected yet.
        r = vc.get(f"/api/mail/recipients/?about=deals:{self.deal.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["can_log"])

    def test_suggestions_list_the_record_people(self):
        r = self.client.get(f"/api/mail/recipients/?about=deals:{self.deal.pk}").json()
        self.assertEqual([p["email"] for p in r["people"]], ["ravi@zeta.in"])
        r = self.client.get(f"/api/mail/recipients/?about=investors:{self.blume.pk}").json()
        self.assertEqual(r["people"][0]["name"], "Ms. Priya Shah")

    def test_vault_documents_need_an_open_vault(self):
        from vault.models import Document

        self.connect("gmail")
        self.client.post("/api/vault/pin/", {"pin": "4826", "password": self.password}, format="json")
        r = self.client.post("/api/vault/upload/", {"files": [SimpleUploadedFile("IM.pdf", PDF)],
                                                    "data": json.dumps({})}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        doc = Document.objects.get()
        self.client.post("/api/vault/lock/")
        r = self.send(status=403, attachments=[{"kind": "vault", "id": doc.pk}])
        self.assertEqual(r["code"], "vault_locked")
        self.client.post("/api/vault/unlock/", {"pin": "4826"}, format="json")
        self.send(attachments=[{"kind": "vault", "id": doc.pk}])
        self.assertEqual([p.get_filename() for p in self.fake.gmail_sent[-1].iter_attachments()], ["IM.pdf"])

    def test_computer_files_are_checked(self):
        r = self.client.post("/api/mail/attachments/",
                             {"files": [SimpleUploadedFile("run.exe", b"MZ\x90\x00", content_type="application/octet-stream")]},
                             format="multipart")
        self.assertEqual(r.status_code, 400)
        self.send(status=400, attachments=[{"kind": "upload", "token": "../../etc/passwd"}])


class CampaignTests(MailBase):
    def setUp(self):
        super().setUp()
        a = self.admin
        self.people = [self.priya, self.ravi]
        self.people.append(Contact.objects.create(full_name="Dr. Neha Rao", investor=self.blume, work_email="neha@blume.vc",
                                                  created_by=a, updated_by=a))
        self.people.append(Contact.objects.create(full_name="No Email", created_by=a, updated_by=a))
        self.people.append(Contact.objects.create(full_name="Opted Out", work_email="out@x.com", email_opt_out=True,
                                                  created_by=a, updated_by=a))
        self.people.append(Contact.objects.create(full_name="Priya Again", personal_email="PRIYA@blume.vc",
                                                  created_by=a, updated_by=a))

    def make(self, client=None, status=201, **body):
        data = {"name": "Fund update", "subject": "{first_name|Hello}, our Q3 note",
                "body": "Dear {first_name|there},\n\nSharing our Q3 update for {firm}.\n\n{my_name}",
                "recipients": {"contacts": [c.pk for c in self.people]}, **body}
        r = (client or self.client).post("/api/mail/campaigns/", data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def test_build_skips_and_merge_preview(self):
        c = self.make()
        self.assertEqual(c["result"], {"added": 3, "skipped": 3})
        people = self.client.get(f"/api/mail/campaigns/{c['id']}/people/?status=skipped").json()["results"]
        self.assertEqual(sorted(p["reason"] for p in people),
                         ["Marked “No campaign emails”", "No email address", "Same email as another person on this campaign"])
        p = self.client.get(f"/api/mail/campaigns/{c['id']}/preview/").json()
        self.assertEqual(p["subject"], "Priya, our Q3 note")
        self.assertIn("Dear Priya,", p["body"])
        self.assertIn("Blume Ventures", p["body"])
        self.assertIn("Asha Admin", p["body"])
        self.assertIn(MailSettings.load().opt_out_text, p["body"])
        # Ravi works at a company: {firm} is its name.
        self.assertEqual(self.make(name="x", body="{nope}", status=400)["body"], "Unknown merge fields: {nope}")

    def test_needs_permission(self):
        member = self.make_user("m@firm.com", self.make_role("Member+", EVERYTHING))
        self.make(client=self.client_for(member), status=403)
        member.role.send_campaigns = True
        member.role.save()
        self.make(client=self.client_for(member))
        self.assertEqual(self.client_for(member).get("/api/mail/campaigns/").json()[0]["mine"], True)
        # Admins see everyone's; others only their own.
        other = self.make_user("o@firm.com", self.make_role("Other", EVERYTHING, send_campaigns=True))
        self.assertEqual(self.client_for(other).get("/api/mail/campaigns/").json(), [])
        self.assertEqual(len(self.client.get("/api/mail/campaigns/").json()), 1)

    def test_from_investors_and_from_list_filters(self):
        c = self.make(recipients={"investors": [self.blume.pk]})
        self.assertEqual(c["counts"]["queued"], 1)  # the primary contact only
        c = self.make(recipients={"investors": [self.blume.pk], "who": "all"})
        self.assertEqual(c["counts"]["queued"], 2)
        c = self.make(recipients={"module": "contacts", "query": {"q": "blume"}})
        self.assertEqual((c["counts"]["total"], c["counts"]["queued"]), (3, 2))  # Priya twice: once skipped

    def test_start_and_send_in_batches_with_logs(self):
        self.connect("gmail")
        ms = MailSettings.load()
        ms.per_minute = 2
        ms.save()
        c = self.make()
        r = self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "sending")
        self.assertEqual(campaigns.tick(), 2)
        self.assertEqual(campaigns.tick(), 1)
        camp = Campaign.objects.get(pk=c["id"])
        self.assertEqual(camp.status, "done")
        sent = self.fake.gmail_sent
        self.assertEqual(sorted(m["To"] for m in sent),
                         ['"Dr. Neha Rao" <neha@blume.vc>', '"Ms. Priya Shah" <priya@blume.vc>', "Ravi Menon <ravi@zeta.in>"])
        neha = next(m for m in sent if "neha" in m["To"])
        self.assertEqual(neha["Subject"], "Neha, our Q3 note")
        self.assertIn("List-Unsubscribe", neha)
        self.assertIn("unsubscribe", neha.get_body(("plain",)).get_content())
        # Logged on each contact and on Blume's page (twice: two people there).
        self.assertEqual(Note.objects.filter(module="contacts", kind="email").count(), 3)
        self.assertEqual(Note.objects.filter(module="investors", kind="email").count(), 2)
        self.assertEqual(Note.objects.filter(kind="email").first().meta["campaign_name"], "Fund update")
        self.blume.refresh_from_db()
        self.assertEqual(self.blume.last_contacted, timezone.localdate())
        d = self.client.get(f"/api/mail/campaigns/{c['id']}/").json()
        self.assertEqual((d["counts"]["sent"], d["confirmed"]), (3, 3))

    def test_opt_out_after_building_is_respected(self):
        self.connect("gmail")
        c = self.make()
        self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        Contact.objects.filter(pk=self.ravi.pk).update(email_opt_out=True)
        campaigns.tick()
        self.assertEqual(len(self.fake.gmail_sent), 2)

    def test_limits_pause_and_carry_on(self):
        acct = self.connect("gmail")
        c = self.make()
        self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        self.fake.fail_next = [(429, {"retry-after": "120"}, b"{}")]
        self.assertEqual(campaigns.tick(), 0)
        camp = Campaign.objects.get(pk=c["id"])
        self.assertEqual(camp.status, "paused")
        self.assertIn("sending limit", camp.paused_reason)
        self.assertEqual(camp.recipients.filter(status="queued").count(), 3)
        campaigns.tick(now=timezone.now() + dt.timedelta(minutes=3))
        camp.refresh_from_db()
        self.assertEqual(camp.status, "done", camp.paused_reason)
        # Daily limit of this install.
        MailAccount.objects.filter(pk=acct.pk).update(sent_day=timezone.localdate(), sent_today=400)
        c2 = self.make(name="Second")
        self.client.post(f"/api/mail/campaigns/{c2['id']}/start/")
        campaigns.tick()
        camp2 = Campaign.objects.get(pk=c2["id"])
        self.assertEqual(camp2.status, "paused")
        self.assertIn("after midnight", camp2.paused_reason)
        self.assertIsNotNone(camp2.resume_at)

    def test_reconnect_pause_resumes_on_reconnect(self):
        self.connect("gmail")
        c = self.make()
        self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        self.fake.valid.clear()
        self.fake.refresh_ok = False
        campaigns.tick()
        camp = Campaign.objects.get(pk=c["id"])
        self.assertTrue(camp.paused_reason.startswith("Reconnect"))
        self.fake.refresh_ok = True
        self.connect("gmail")
        campaigns.tick()
        camp.refresh_from_db()
        self.assertEqual(camp.status, "done")

    def test_schedule_pause_cancel_and_test_send(self):
        self.connect("gmail")
        later = (timezone.now() + dt.timedelta(hours=2)).isoformat()
        c = self.make(send_at=later)
        r = self.client.post(f"/api/mail/campaigns/{c['id']}/test/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.fake.gmail_sent[-1]["Subject"], "[Test] Priya, our Q3 note")
        self.assertFalse(Note.objects.filter(kind="email").exists())
        self.assertEqual(self.client.post(f"/api/mail/campaigns/{c['id']}/start/").json()["status"], "scheduled")
        self.assertEqual(campaigns.tick(), 0)
        self.assertEqual(campaigns.tick(now=timezone.now() + dt.timedelta(hours=3)), 3)
        c2 = self.make(name="Two")
        self.client.post(f"/api/mail/campaigns/{c2['id']}/start/")
        self.assertEqual(self.client.post(f"/api/mail/campaigns/{c2['id']}/pause/").json()["status"], "paused")
        self.assertEqual(campaigns.tick(), 0)
        r = self.client.post(f"/api/mail/campaigns/{c2['id']}/cancel/").json()
        self.assertEqual((r["status"], r["counts"]["queued"]), ("cancelled", 0))
        self.assertEqual(self.client.delete(f"/api/mail/campaigns/{c2['id']}/").status_code, 204)

    def test_start_problems(self):
        c = self.make(subject="", recipients={"contacts": []})
        r = self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Write a subject.", r.json()["detail"])
        c = self.make()
        r = self.client.post(f"/api/mail/campaigns/{c['id']}/start/")
        self.assertEqual(r.json()["code"], "mail_setup")  # no mailbox yet


class TemplateTests(MailBase):
    def test_private_and_shared(self):
        r = self.client.post("/api/mail/templates/", {"name": "Intro", "subject": "Hi {first_name}", "body": "..."},
                             format="json")
        self.assertEqual(r.status_code, 201, r.content)
        other = self.make_user("o@firm.com", self.make_role("Other", EVERYTHING))
        oc = self.client_for(other)
        self.assertEqual(oc.get("/api/mail/templates/").json(), [])
        self.client.patch(f"/api/mail/templates/{r.json()['id']}/", {"shared": True}, format="json")
        self.assertEqual(len(oc.get("/api/mail/templates/").json()), 1)
        self.assertEqual(oc.patch(f"/api/mail/templates/{r.json()['id']}/", {"name": "x"}, format="json").status_code, 403)
        bad = self.client.post("/api/mail/templates/", {"name": "B", "body": "{bogus}"}, format="json")
        self.assertEqual(bad.status_code, 400)


class HelperTests(APITestBase):
    def test_render_and_names(self):
        self.assertEqual(campaigns.render("Hi {first_name|there}, {firm}", {"first_name": "", "firm": "Blume"}),
                         "Hi there, Blume")
        self.assertEqual(campaigns._split("Dr. Neha  Rao"), ("Neha", "Rao"))
        self.assertEqual(campaigns.unknown_fields("{first_name} {Bad} { full_name }"), ["Bad"])

    def test_html_is_escaped(self):
        html = providers.text_to_html("<b>x</b>\nsee https://a.com/x?y=1.")
        self.assertIn("&lt;b&gt;", html)
        self.assertIn('<a href="https://a.com/x?y=1">', html)

    def test_header_injection(self):
        out = providers.Outgoing("A\r\nBcc: evil@x.com", "a@b.com", [("", "c@d.com")], "Hi\r\nBcc: evil@x.com", "t")
        raw = providers.build_mime(out).decode()
        self.assertNotIn("\nBcc:", raw)


class ProviderAddressTests(TestCase):
    def test_only_https_to_the_mail_services(self):
        from mail.providers import MailError, _check_url

        for ok in ("https://gmail.googleapis.com/x", "https://oauth2.googleapis.com/token",
                   "https://graph.microsoft.com/v1.0/me", "https://outlook.office.com/api/v2.0/x"):
            _check_url(ok)
        for bad in ("http://graph.microsoft.com/", "file:///etc/passwd", "https://evil.com/?graph.microsoft.com",
                    "https://graph.microsoft.com.evil.com/", "https://169.254.169.254/latest"):
            with self.assertRaises(MailError):
                _check_url(bad)
