"""Contact capture (decision 64): suggestions from email, signatures, checking before saving."""

from capture import signature
from capture.models import Stranger
from contacts.models import Contact
from core.tests.base import APITestBase
from investors.models import Investor
from outreach.models import Company
from sync.tests.fakes import mime
from sync.tests.test_logging import SyncBase

SIG = """Hi Asha,

Happy to look at the deck. Can we talk Thursday?

Best regards,
Meera Rao
Principal | Kalaari Capital
M: +91 98450 12345 | T: +91 80 4123 4567
www.kalaari.com
linkedin.com/in/meera-rao

On Tue, 21 Oct 2026, Asha wrote:
> Hi Meera, sharing our teaser
"""


class SignatureTests(APITestBase):
    def test_reads_a_typical_signature(self):
        out = signature.parse(SIG, "Meera Rao", "meera@kalaari.com")
        self.assertEqual(out["full_name"], "Meera Rao")
        self.assertEqual(out["designation"], "Principal")
        self.assertEqual(out["company"], "Kalaari Capital")
        self.assertEqual(out["mobile"], "+919845012345")
        self.assertEqual(out["office_phone"], "+918041234567")
        self.assertEqual(out["linkedin"], "https://linkedin.com/in/meera-rao")
        self.assertEqual(out["website"], "https://www.kalaari.com")

    def test_quoted_text_is_ignored(self):
        out = signature.parse("Thanks,\nRavi\n\nOn Mon, X wrote:\n> Priya Shah\n> Partner, Blume Ventures\n> +91 99999 88888",
                              "Ravi Kulkarni", "ravi@acmefoods.in")
        self.assertEqual(out, {"full_name": "Ravi Kulkarni"})

    def test_pasted_block(self):
        out = signature.from_text("Rohan Das\nPartner, Fund B Capital\nrohan@fund-b.vc\n+1 (415) 555-0134")
        self.assertEqual((out["full_name"], out["designation"], out["company"], out["work_email"]),
                         ("Rohan Das", "Partner", "Fund B Capital", "rohan@fund-b.vc"))


class SuggestionTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_people_youve_emailed_who_arent_in_dealflow(self):
        self.cloud.add_gmail("m1", mime("Meera Rao <meera@kalaari.com>", ["admin@firm.com"], "Deck", SIG,
                                        cc=["ravi@zeta.in", "colleague@firm.com"]))
        self.cloud.add_gmail("m2", mime("News <newsletter@vccircle.com>", ["admin@firm.com"], "Weekly", "x"))
        self.cloud.add_gmail("m3", mime("noreply@bank.com", ["admin@firm.com"], "Statement", "x"))
        raw = mime("Promo <offers@shop.com>", ["admin@firm.com"], "Sale", "x").replace(
            b"Subject:", b"List-Unsubscribe: <mailto:u@shop.com>\nSubject:")
        self.cloud.add_gmail("m4", raw)
        self.run_sync()
        people = self.client.get("/api/capture/suggestions/").json()["people"]
        self.assertEqual([p["email"] for p in people], ["meera@kalaari.com"])  # not contacts, colleagues, bulk or no-reply
        # Private to the person whose mailbox it is
        other = self.make_user("vp@firm.com", self.admin_role)
        self.assertEqual(self.client_for(other).get("/api/capture/suggestions/").json()["people"], [])
        # Adding: the signature is read from the mailbox now
        pre = self.client.get(f"/api/capture/suggestions/{people[0]['id']}/prefill/").json()
        self.assertEqual((pre["designation"], pre["company"], pre["work_email"]), ("Principal", "Kalaari Capital", "meera@kalaari.com"))
        self.assertEqual(self.client_for(other).get(f"/api/capture/suggestions/{people[0]['id']}/prefill/").status_code, 404)
        # Once they're a contact they drop off
        Contact.objects.create(full_name="Meera Rao", work_email="Meera@Kalaari.com", created_by=self.admin, updated_by=self.admin)
        self.assertEqual(self.client.get("/api/capture/suggestions/").json()["people"], [])

    def test_counts_and_dismiss(self):
        for i in range(3):
            self.cloud.add_gmail(f"m{i}", mime("Meera Rao <meera@kalaari.com>", ["admin@firm.com"], f"Note {i}", "x",
                                                message_id=f"<n{i}@k>"))
        self.cloud.add_gmail("s1", mime("admin@firm.com", ["anil@sequoia.com"], "Intro", "x"), labels=["SENT"])
        self.run_sync()
        people = {p["email"]: p for p in self.client.get("/api/capture/suggestions/").json()["people"]}
        self.assertEqual(people["meera@kalaari.com"]["count"], 3)
        self.assertTrue(people["anil@sequoia.com"]["wrote_to_them"])
        self.client.post(f"/api/capture/suggestions/{people['anil@sequoia.com']['id']}/dismiss/")
        left = [p["email"] for p in self.client.get("/api/capture/suggestions/").json()["people"]]
        self.assertEqual(left, ["meera@kalaari.com"])
        self.assertEqual(Stranger.objects.filter(dismissed=True).count(), 1)


class CheckTests(APITestBase):
    def setUp(self):
        super().setUp()
        a = self.admin
        self.blume = Investor.objects.create(firm_name="Blume Ventures", website="https://blume.vc", created_by=a, updated_by=a)
        self.zeta = Company.objects.create(name="Zeta Pharma Pvt Ltd", website="www.zetapharma.in", created_by=a, updated_by=a)
        self.priya = Contact.objects.create(full_name="Priya Shah", investor=self.blume, work_email="priya@blume.vc",
                                            linkedin="https://www.linkedin.com/in/priyashah", created_by=a, updated_by=a)

    def test_new_person_with_firm_found_by_email_domain(self):
        r = self.client.post("/api/capture/check/", {"full_name": "Arjun Rao", "work_email": "arjun@blume.vc",
                                                     "designation": "Principal"}, format="json").json()
        self.assertIsNone(r["existing"])
        self.assertEqual(r["firms"][0]["title"], "Blume Ventures")
        r = self.client.post("/api/capture/check/", {"full_name": "Leela", "company": "Zeta Pharma"}, format="json").json()
        self.assertEqual(r["firms"][0]["module"], "companies")

    def test_existing_person_shows_what_changed(self):
        r = self.client.post("/api/capture/check/", {"full_name": "Priya Shah", "designation": "Managing Partner",
                                                     "linkedin": "https://linkedin.com/in/PriyaShah/"}, format="json").json()
        self.assertEqual(r["existing"]["id"], self.priya.pk)
        self.assertEqual(r["existing"]["why"], "linkedin")
        self.assertEqual([c["field"] for c in r["existing"]["changes"]], ["designation", "linkedin"])

    def test_add_firm_in_the_same_step(self):
        r = self.client.post("/api/capture/firm/", {"kind": "investors", "name": "Kalaari Capital",
                                                    "website": "https://kalaari.com"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(Investor.objects.filter(firm_name="Kalaari Capital").exists())
        role = self.make_role("Viewer", {m: ["view"] for m in ("contacts", "investors", "outreach")})
        c = self.client_for(self.make_user("v@firm.com", role))
        self.assertEqual(c.post("/api/capture/firm/", {"kind": "investors", "name": "X"}, format="json").status_code, 403)

    def test_parse_pasted_text(self):
        r = self.client.post("/api/capture/parse/", {"text": "Rohan Das\nPartner, Fund B Capital\nrohan@fund-b.vc"},
                             format="json").json()
        self.assertEqual(r["company"], "Fund B Capital")
        self.assertEqual(self.client.post("/api/capture/parse/", {"text": "x" * 20001}, format="json").status_code, 400)
