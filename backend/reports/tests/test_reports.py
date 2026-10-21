"""Settings > Export: custom reports in Excel, CSV and PDF, saved reports, and what people can see."""

import csv
import io

from openpyxl import load_workbook

from core.tests.base import APITestBase
from deals.models import Deal, DealInvestor
from investors.models import Investor
from outreach.models import Company
from reports.models import SavedReport

EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


class ReportBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.vc = self.opt("investor_types", "VC")
        self.pe = self.opt("investor_types", "PE")
        for name, t, amt in (("Blume Ventures", self.vc, 10), ("Peak XV", self.vc, 75), ("A91 Partners", self.pe, 25)):
            Investor.objects.create(firm_name=name, investor_type=t, min_ticket_amount=amt, min_ticket_currency="INR",
                                    min_ticket_unit="Cr", min_ticket_value=amt * 10**7, hq_city="Mumbai",
                                    created_by=self.admin, updated_by=self.admin)

    def run_report(self, client=None, status=200, **cfg):
        body = {"module": "investors", "format": "xlsx", **cfg}
        r = (client or self.client).post("/api/reports/run/", {"config": body}, format="json")
        self.assertEqual(r.status_code, status, r.content[:300])
        return r


class RunTests(ReportBase):
    def test_excel_with_columns_filters_and_order(self):
        r = self.run_report(columns=["investor_type", "min_ticket"], filters={"investor_type": str(self.vc.pk)},
                            ordering="-firm_name", title="VC funds")
        self.assertIn('filename="vc-funds-', r["Content-Disposition"])
        ws = load_workbook(io.BytesIO(r.content)).active
        rows = list(ws.values)
        self.assertEqual(rows[0][:4], ("Firm name", "Investor type", "Min ticket size", "Min ticket size – currency & unit"))
        self.assertEqual([row[0] for row in rows[1:]], ["Peak XV", "Blume Ventures"])

    def test_csv(self):
        r = self.run_report(format="csv", columns=["hq_city", "min_ticket"])
        text = r.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][:3], ["Firm name", "HQ city", "Min ticket size"])
        self.assertEqual(len(rows), 4)
        self.assertTrue(r.content.startswith("﻿".encode()))

    def test_csv_neutralises_formulas(self):
        Investor.objects.create(firm_name="=HYPERLINK(1)", created_by=self.admin, updated_by=self.admin)
        r = self.run_report(format="csv")
        self.assertIn("'=HYPERLINK(1)", r.content.decode("utf-8-sig"))

    def test_pdf_table_and_pages(self):
        r = self.run_report(format="pdf", layout="table", columns=["investor_type", "min_ticket", "hq_city"])
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        r = self.run_report(format="pdf", layout="pages")
        self.assertTrue(r.content.startswith(b"%PDF"))
        # Nothing matching still gives a readable file.
        r = self.run_report(format="pdf", layout="table", filters={"q": "nothing like this"})
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_pdf_pages_for_deals_with_tracker(self):
        co = Company.objects.create(name="Zeta Pharma", created_by=self.admin, updated_by=self.admin)
        d = Deal.objects.create(company=co, stage="term_sheet", created_by=self.admin, updated_by=self.admin)
        DealInvestor.objects.create(deal=d, investor=Investor.objects.first(), created_by=self.admin, updated_by=self.admin)
        r = self.run_report(module="deals", format="pdf", layout="pages")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_limits_and_bad_settings(self):
        from reports import build

        old = build.LIMITS["table"]
        build.LIMITS["table"] = 2
        try:
            r = self.run_report(status=400, format="pdf", layout="table")
            self.assertIn("covers 3 records", r.json()["detail"])
        finally:
            build.LIMITS["table"] = old
        for cfg in ({"module": "nope"}, {"format": "doc"}, {"layout": "grid", "format": "pdf"}, {"columns": "x"},
                    {"filters": []}, {"filters": {"q": ["x"]}}, {"ordering": 5}, {"title": 7}):
            self.run_report(status=400, **cfg)
        self.assertEqual(self.client.post("/api/reports/run/", {"config": "x"}, format="json").status_code, 400)
        # Unknown columns are ignored; reserved params can't page or narrow the report.
        r = self.run_report(columns=["nope"], filters={"page": "2", "page_size": "1"})
        self.assertEqual(len(list(load_workbook(io.BytesIO(r.content)).active.values)), 4)


class AccessTests(ReportBase):
    def test_only_modules_and_records_the_person_can_see(self):
        role = self.make_role("Contacts only", {"contacts": ["view"]})
        c = self.client_for(self.make_user("c@firm.com", role))
        self.run_report(client=c, status=403)
        # Restricted deals stay out.
        role2 = self.make_role("Bankers", EVERYTHING)
        outsider = self.make_user("o@firm.com", role2)
        co = Company.objects.create(name="Secret Co", created_by=self.admin, updated_by=self.admin)
        Deal.objects.create(company=co, created_by=self.admin, updated_by=self.admin)
        d2 = Deal.objects.create(company=Company.objects.create(name="Open Co", created_by=self.admin, updated_by=self.admin),
                                 created_by=self.admin, updated_by=self.admin)
        Deal.objects.filter(company=co).update(restricted=True)
        r = self.run_report(client=self.client_for(outsider), module="deals", filters={"view": "all"})
        names = [row[0] for row in list(load_workbook(io.BytesIO(r.content)).active.values)[1:]]
        self.assertEqual(names, [d2.company.name])

    def test_vault_needs_its_pin(self):
        r = self.run_report(module="vault", status=403)
        self.assertIn(r.json().get("code"), ("vault_pin_required", "vault_locked"))


class SavedTests(ReportBase):
    def test_save_share_run_and_ownership(self):
        role = self.make_role("Bankers", EVERYTHING)
        rhea = self.make_user("rhea@firm.com", role)
        c_rhea = self.client_for(rhea)
        cfg = {"module": "investors", "format": "csv", "columns": ["hq_city"]}
        r = self.client.post("/api/reports/", {"name": "  Mumbai   funds ", "config": cfg}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        self.assertEqual(r.json()["name"], "Mumbai funds")
        self.assertEqual(c_rhea.get("/api/reports/").json(), {"mine": [], "shared": []})  # private
        self.assertEqual(c_rhea.post(f"/api/reports/{rid}/run/").status_code, 404)
        self.client.patch(f"/api/reports/{rid}/", {"shared": True}, format="json")
        listed = c_rhea.get("/api/reports/").json()["shared"]
        self.assertEqual([x["name"] for x in listed], ["Mumbai funds"])
        self.assertFalse(listed[0]["mine"])
        r = c_rhea.post(f"/api/reports/{rid}/run/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c_rhea.patch(f"/api/reports/{rid}/", {"name": "Mine now"}, format="json").status_code, 403)
        self.assertEqual(c_rhea.delete(f"/api/reports/{rid}/").status_code, 403)
        # Someone who can't see investors doesn't see the shared report at all.
        other = self.client_for(self.make_user("x@firm.com", self.make_role("Tasks only", {"tasks": ["view"]})))
        self.assertEqual(other.get("/api/reports/").json()["shared"], [])
        self.assertEqual(other.post(f"/api/reports/{rid}/run/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/reports/{rid}/").status_code, 204)
        self.assertFalse(SavedReport.objects.exists())

    def test_bad_saves(self):
        cfg = {"module": "investors"}
        for body in ({"name": "", "config": cfg}, {"name": "x", "config": {"module": "nope"}},
                     {"name": "x", "shared": "yes", "config": cfg}, {"config": cfg}):
            self.assertEqual(self.client.post("/api/reports/", body, format="json").status_code, 400, body)
