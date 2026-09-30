import datetime as dt
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from contacts.models import Contact
from core.models import FieldSwitch, Note
from core.tests.base import APITestBase
from outreach.models import Company, Outreach

C_URL = "/api/m/companies/"
O_URL = "/api/m/outreach/"


def xlsx(rows, name="pipeline.xlsx"):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


class OutreachBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.acme = self.client.post(C_URL, {"name": "Acme Foods Pvt Ltd", "city": "Pune",
                                              "sector": self.opt("sectors", "Consumer / D2C").pk}, format="json").json()

    def company_link(self, c=None):
        return f"companies:{(c or self.acme)['id']}"

    def outreach(self, **kw):
        d = {"company": self.company_link(), "transaction_type": self.opt("transaction_types", "Equity raise").pk}
        d.update(kw)
        r = self.client.post(O_URL, d, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def notes(self, o):
        return self.client.get(f"{O_URL}{o['id']}/notes/").json()


class CompanyLevelTests(OutreachBase):
    def test_company_fields_are_shared_by_attempts(self):
        first = self.outreach(city="Mumbai", revenue={"amount": "120", "currency": "INR", "unit": "Cr"},
                              ebitda={"amount": "-4.5", "currency": "INR", "unit": "Cr"})
        self.assertEqual(first["_title"], "Acme Foods Pvt Ltd")
        self.assertEqual(first["company"]["title"], "Acme Foods Pvt Ltd")
        self.assertEqual(first["city"], "Mumbai")
        company = Company.objects.get()
        self.assertEqual(company.city, "Mumbai")
        self.assertLess(company.ebitda_amount, 0)  # losses are allowed
        # a second attempt on the same company sees (and edits) the same details
        second = self.outreach(transaction_type=self.opt("transaction_types", "Debt raise").pk)
        self.assertEqual(second["city"], "Mumbai")
        self.client.patch(f"{O_URL}{second['id']}/", {"sub_sector": "Snacks"}, format="json")
        self.assertEqual(self.client.get(f"{O_URL}{first['id']}/").json()["sub_sector"], "Snacks")
        self.assertEqual(self.client.get(f"{C_URL}{self.acme['id']}/").json()["sub_sector"], "Snacks")

    def test_company_required_and_new_company_details(self):
        r = self.client.post(O_URL, {"stage": "contacted"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("company", r.json())
        new = self.client.post(C_URL, {"name": "Zeta Pharma"}, format="json").json()
        o = self.outreach(company=self.company_link(new), sector=self.opt("sectors", "Pharma & life sciences").pk,
                          year_founded=2011)
        self.assertEqual(o["sector"], self.opt("sectors", "Pharma & life sciences").pk)
        self.assertEqual(Company.objects.get(name="Zeta Pharma").year_founded, 2011)

    def test_removed_sector_kept_when_saving_attempt(self):
        old = self.opt("sectors", "Consumer / D2C")
        self.client.patch(f"/api/choice-options/{old.pk}/", {"is_active": False}, format="json")
        # a new attempt for a company that already has the removed sector can be saved with it
        o = self.outreach(sector=old.pk)
        self.assertEqual(o["sector"], old.pk)

    def test_company_switches_apply_to_outreach(self):
        self.client.patch("/api/settings/fields/", {"module": "companies", "field_key": "website", "enabled": False}, format="json")
        schema = self.client.get("/api/schema/").json()
        outreach = next(m for m in schema["modules"] if m["key"] == "outreach")
        self.assertFalse(next(f for f in outreach["fields"] if f["key"] == "website")["enabled"])
        # and can't be switched from the outreach side
        r = self.client.patch("/api/settings/fields/", {"module": "outreach", "field_key": "city", "enabled": False}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Companies", r.json()["field_key"])
        fields = self.client.get("/api/settings/fields/").json()["modules"]
        outreach_keys = {f["key"] for m in fields if m["module"] == "outreach" for f in m["fields"]}
        self.assertNotIn("city", outreach_keys)
        self.assertFalse(FieldSwitch.objects.filter(module="outreach").exists())

    def test_company_with_outreach_cant_be_purged(self):
        self.outreach()
        self.client.delete(f"{C_URL}{self.acme['id']}/")
        r = self.client.delete(f"/api/archive/companies/{self.acme['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("outreach", r.json()["detail"])


class PipelineTests(OutreachBase):
    def test_new_outreach_starts_at_identified(self):
        o = self.outreach()
        self.assertEqual((o["stage"], o["status"], o["stage_entered"]), ("identified", "open", timezone.localdate().isoformat()))
        self.assertEqual([n["text"] for n in self.notes(o)], ["Added at Identified"])

    def test_moving_stages(self):
        o = self.outreach()
        Outreach.objects.filter(pk=o["id"]).update(stage_entered=dt.date(2026, 1, 1))
        r = self.client.patch(f"{O_URL}{o['id']}/", {"stage": "meeting"}, format="json").json()
        self.assertEqual((r["stage"], r["stage_entered"]), ("meeting", timezone.localdate().isoformat()))
        self.assertEqual(self.notes(o)[0]["text"], "Moved from Identified to Meeting held")
        self.assertEqual(self.notes(o)[0]["kind"], "stage")
        # editing other fields doesn't touch the stage date or the log
        self.client.patch(f"{O_URL}{o['id']}/", {"expected_timeline": "Q3 FY27"}, format="json")
        self.assertEqual(len(self.notes(o)), 2)

    def test_lost_needs_reason_and_keeps_exit_stage(self):
        o = self.outreach(stage="proposal")
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "lost"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("lost_reason", r.json())
        reason = self.opt("lost_reasons", "Fees not agreed")
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "lost", "lost_reason": reason.pk}, format="json").json()
        self.assertEqual((r["stage"], r["status"], r["lost_reason"]), ("proposal", "lost", reason.pk))
        self.assertEqual(self.notes(o)[0]["text"], "Marked lost at Proposal sent · Fees not agreed")

    def test_on_hold_needs_reason_and_date_then_resumes_at_same_stage(self):
        o = self.outreach(stage="negotiating")
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "on_hold", "hold_reason": self.opt("hold_reasons", "Awaiting financials").pk}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("revisit_date", r.json())
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "on_hold", "revisit_date": "2027-01-12",
                                                    "hold_reason": self.opt("hold_reasons", "Awaiting financials").pk}, format="json").json()
        self.assertEqual((r["stage"], r["status"], r["revisit_date"]), ("negotiating", "on_hold", "2027-01-12"))
        note = self.notes(o)[0]
        self.assertEqual(note["text"], "Put on hold at Negotiating · Awaiting financials")
        self.assertEqual(note["meta"]["date"], "2027-01-12")
        # resume: back at the paused stage, hold details cleared (the log keeps them)
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "open"}, format="json").json()
        self.assertEqual((r["stage"], r["status"], r["hold_reason"], r["revisit_date"]), ("negotiating", "open", None, None))
        self.assertEqual(self.notes(o)[0]["text"], "Resumed at Negotiating")

    def test_moving_a_lost_outreach_reopens_it(self):
        o = self.outreach(stage="contacted")
        self.client.patch(f"{O_URL}{o['id']}/", {"status": "lost", "lost_reason": self.opt("lost_reasons", "No response").pk}, format="json")
        r = self.client.patch(f"{O_URL}{o['id']}/", {"stage": "meeting"}, format="json").json()
        self.assertEqual((r["stage"], r["status"], r["lost_reason"]), ("meeting", "open", None))
        self.assertEqual(self.notes(o)[0]["text"], "Reopened and moved to Meeting held")

    def test_won(self):
        o = self.outreach(stage="negotiating")
        r = self.client.patch(f"{O_URL}{o['id']}/", {"stage": "won"}, format="json").json()
        self.assertEqual(r["stage"], "won")
        self.assertEqual([n["text"] for n in self.notes(o)[:2]], ["Moved to Active Deals", "Mandate won (from Negotiating)"])
        r = self.client.patch(f"{O_URL}{o['id']}/", {"status": "lost", "lost_reason": self.opt("lost_reasons", "Other").pk}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.json())

    def test_views_and_stage_order(self):
        a = self.outreach(stage="proposal")
        zeta = self.client.post(C_URL, {"name": "Zeta"}, format="json").json()
        b = self.outreach(company=self.company_link(zeta), stage="contacted")
        beta = self.client.post(C_URL, {"name": "Beta"}, format="json").json()
        c = self.outreach(company=self.company_link(beta), stage="won")
        d = self.outreach(company=self.company_link(zeta), stage="meeting")
        self.client.patch(f"{O_URL}{d['id']}/", {"status": "lost", "lost_reason": self.opt("lost_reasons", "Other").pk}, format="json")

        def ids(qs):
            return [r["id"] for r in self.client.get(O_URL + "?" + qs).json()["results"]]

        self.assertEqual(sorted(ids("view=active")), sorted([a["id"], b["id"]]))
        self.assertEqual(ids("view=won"), [c["id"]])
        self.assertEqual(ids("view=lost"), [d["id"]])
        self.assertEqual(len(ids("")), 4)  # no view = everything (search, related lists)
        self.assertEqual(ids("view=all&ordering=stage"), [b["id"], d["id"], a["id"], c["id"]])
        # Acme, Beta, then Zeta's two attempts in stage order
        self.assertEqual(ids("view=all&ordering=company,stage"), [a["id"], c["id"], b["id"], d["id"]])

    def test_filters_on_company_fields_and_search(self):
        self.outreach()
        zeta = self.client.post(C_URL, {"name": "Zeta Pharma", "city": "Hyderabad"}, format="json").json()
        self.outreach(company=self.company_link(zeta))
        names = lambda qs: [r["_title"] for r in self.client.get(O_URL + "?" + qs).json()["results"]]  # noqa: E731
        self.assertEqual(names(f"sector={self.opt('sectors', 'Consumer / D2C').pk}"), ["Acme Foods Pvt Ltd"])
        self.assertEqual(names("city=hyder"), ["Zeta Pharma"])
        self.assertEqual(names("q=zeta"), ["Zeta Pharma"])
        self.assertEqual(names("q=pune"), ["Acme Foods Pvt Ltd"])
        hits = self.client.get("/api/search/?q=acme").json()["results"]
        self.assertEqual({(h["module"], h.get("title")) for h in hits},
                         {("companies", "Acme Foods Pvt Ltd"), ("outreach", "Acme Foods Pvt Ltd")})
        self.assertEqual(next(h for h in hits if h["module"] == "outreach")["subtitle"], "Equity raise · Identified")


class NotesTests(OutreachBase):
    def test_log_interactions(self):
        o = self.outreach()
        r = self.client.post(f"{O_URL}{o['id']}/notes/", {"kind": "call", "date": "2026-09-20", "text": "Spoke to the CFO"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        note = r.json()
        self.assertTrue(note["can_edit"])
        self.assertEqual([n["kind"] for n in self.notes(o)], ["stage", "call"])  # newest date first
        r = self.client.patch(f"/api/notes/{note['id']}/", {"text": "Spoke to the CFO and CEO"}, format="json")
        self.assertEqual(r.json()["text"], "Spoke to the CFO and CEO")
        self.assertEqual(self.client.post(f"{O_URL}{o['id']}/notes/", {"text": " "}, format="json").status_code, 400)
        # stage entries can't be edited
        stage = next(n for n in self.notes(o) if n["kind"] == "stage")
        self.assertFalse(stage["can_edit"])
        self.assertEqual(self.client.delete(f"/api/notes/{stage['id']}/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/notes/{note['id']}/").status_code, 204)

    def test_note_permissions(self):
        o = self.outreach()
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"outreach": ["view"]}))
        editor = self.make_user("e@firm.com", self.make_role("Editor", {"outreach": ["view", "edit"]}))
        self.assertEqual(self.client_for(viewer).get(f"{O_URL}{o['id']}/notes/").status_code, 200)
        self.assertEqual(self.client_for(viewer).post(f"{O_URL}{o['id']}/notes/", {"text": "x"}, format="json").status_code, 403)
        mine = self.client_for(editor).post(f"{O_URL}{o['id']}/notes/", {"text": "x"}, format="json").json()
        admins = self.client.post(f"{O_URL}{o['id']}/notes/", {"text": "y"}, format="json").json()
        self.assertEqual(self.client_for(editor).patch(f"/api/notes/{admins['id']}/", {"text": "z"}, format="json").status_code, 403)
        self.assertEqual(self.client_for(editor).patch(f"/api/notes/{mine['id']}/", {"text": "z"}, format="json").status_code, 200)
        # contacts have no interaction log
        self.assertEqual(self.client.get("/api/m/contacts/1/notes/").status_code, 404)

    def test_purging_removes_the_log(self):
        o = self.outreach()
        self.client.post(f"{O_URL}{o['id']}/notes/", {"text": "x"}, format="json")
        self.client.delete(f"{O_URL}{o['id']}/")
        self.client.delete(f"/api/archive/outreach/{o['id']}/")
        self.assertFalse(Note.objects.filter(module="outreach", record_id=o["id"]).exists())


class PermissionTests(OutreachBase):
    def test_companies_follow_outreach_permissions(self):
        banker = self.make_user("b@firm.com", self.make_role("Banker", {"outreach": ["view", "create", "edit"]}))
        c = self.client_for(banker)
        self.assertEqual(c.get(C_URL).status_code, 200)
        self.assertEqual(c.post(C_URL, {"name": "New Co"}, format="json").status_code, 201)
        self.assertEqual(c.delete(f"{C_URL}{self.acme['id']}/").status_code, 403)
        me = c.get("/api/auth/me/").json()
        self.assertEqual(me["perms"]["companies"], {"view": True, "create": True, "edit": True, "delete": False})
        nobody = self.make_user("n@firm.com", self.make_role("Investors only", {"investors": ["view"]}))
        self.assertEqual(self.client_for(nobody).get(C_URL).status_code, 403)
        modules = [m["key"] for m in self.client_for(nobody).get("/api/schema/").json()["modules"]]
        self.assertEqual(modules, ["investors"])


class ContactsCompanySideTests(OutreachBase):
    def test_company_contacts(self):
        o = self.outreach()
        r = self.client.post("/api/m/contacts/", {"full_name": "Ravi CFO", "firm": self.company_link()}, format="json")
        self.assertEqual(r.json()["firm"]["module"], "companies")
        self.client.post("/api/m/investors/", {"firm_name": "Blume"}, format="json")
        names = lambda qs: [x["full_name"] for x in self.client.get("/api/m/contacts/?" + qs).json()["results"]]  # noqa: E731
        self.assertEqual(names("firm_type=companies"), ["Ravi CFO"])
        self.assertEqual(names(f"firm={self.company_link()}"), ["Ravi CFO"])
        self.assertEqual(self.client.get("/api/search/?q=ravi").json()["results"][0]["subtitle"], "Acme Foods Pvt Ltd")
        schema = self.client.get("/api/schema/").json()
        outreach = next(m for m in schema["modules"] if m["key"] == "outreach")
        self.assertEqual({s["key"]: (s["status"], s.get("via")) for s in outreach["sections"]},
                         {"tasks": ("ready", None), "deal": ("ready", None), "contacts": ("ready", "company"),
                          "history": ("ready", "company")})
        self.assertEqual(outreach["pipeline"]["stages"][0], {"value": "identified", "label": "Identified"})
        self.assertTrue(outreach["timeline"])
        self.assertTrue(o["id"])


class OutreachImportTests(OutreachBase):
    BASE = "/api/m/outreach/import/"

    def upload(self, f):
        r = self.client.post(self.BASE + "upload/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_pipeline_sheet(self):
        existing = self.outreach(stage="contacted")
        banker = self.make_user("bob@firm.com", self.admin_role, full_name="Bob Banker")
        f = xlsx([
            ["Company", "Sector", "City", "Deal type", "Deal size (INR Cr)", "Stage", "Status", "Banker", "Follow up", "Lost reason"],
            ["Acme Foods", "", "", "Debt raise", 50, "Meeting held", "", "Bob Banker", "15/10/2026", ""],
            ["Nova Retail", "Retail", "Delhi", "Equity raise", 200, "Proposal sent", "", "", "", ""],
            ["Old Lost Co", "Retail", "", "M&A sell-side", "", "Negotiating", "Lost", "", "", ""],
            ["Nova Retail Pvt Ltd", "", "", "Pre-IPO", "", "Identified", "", "", "", ""],
        ])
        up = self.upload(f)
        self.assertEqual([m["field"] for m in up["mapping"]],
                         ["company", "sector", "city", "transaction_type", "expected_size", "stage", "status",
                          "lead_banker", "next_follow_up", "lost_reason"])
        opts = {"token": up["token"], "mapping": up["mapping"], "create_links": {"company": "companies"}}
        r = self.client.post(self.BASE + "check/", opts, format="json").json()
        rows = {x["row"]: x for x in r["rows"]}
        self.assertEqual(rows[2]["title"], "Acme Foods")
        self.assertEqual(rows[2]["duplicate_of"][0]["id"], existing["id"])  # Acme already being worked
        self.assertEqual(rows[4]["errors"], {})  # lost without a reason is fine on import
        self.assertEqual(r["summary"]["new_records"], {"Companies": ["Nova Retail", "Old Lost Co"]})
        r = self.client.post(self.BASE + "commit/", {**opts, "include_rows": [2, 3, 4, 5]}, format="json").json()
        self.assertEqual(r["created"], 4, r)
        # "Nova Retail Pvt Ltd" was matched to the Nova Retail created a row earlier
        self.assertEqual(r["new_records"], {"Companies": 2})
        nova = Company.objects.get(name="Nova Retail")
        self.assertEqual((nova.city, nova.sector.label), ("Delhi", "Retail"))
        self.assertEqual(nova.outreach.count(), 2)
        acme = Company.objects.get(pk=self.acme["id"])
        self.assertEqual(acme.city, "Pune")  # blank cells don't wipe what's known
        debt = Outreach.objects.get(company=acme, transaction_type__label="Debt raise")
        self.assertEqual((debt.stage, debt.lead_banker, debt.next_follow_up), ("meeting", banker, dt.date(2026, 10, 15)))
        self.assertEqual((debt.expected_size_amount, debt.expected_size_unit), (50, "Cr"))
        lost = Outreach.objects.get(company__name="Old Lost Co")
        self.assertEqual((lost.stage, lost.status), ("negotiating", "lost"))
        self.assertTrue(Note.objects.filter(module="outreach", record_id=lost.pk, text__contains="imported").exists())

    def test_export(self):
        self.outreach(stage="proposal", city="Mumbai", expected_size={"amount": "75", "currency": "INR", "unit": "Cr"})
        data = self.client.get("/api/m/outreach/export/?view=all").content
        ws = load_workbook(io.BytesIO(data)).active
        headers = [c.value for c in ws[1]]
        row = {h: c.value for h, c in zip(headers, ws[2])}
        self.assertEqual((row["Company"], row["City"], row["Stage"], row["Status"], row["Transaction type"]),
                         ("Acme Foods Pvt Ltd", "Mumbai", "Proposal sent", "Active", "Equity raise"))
        self.assertEqual((row["Expected size"], row["Expected size – currency & unit"]), (75, "INR Cr"))
