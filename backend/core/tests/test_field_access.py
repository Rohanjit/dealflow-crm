"""Field access per role (decision 63, S3): hidden fields never leave the server and read-only
fields can't be changed, on every path."""

import io

from openpyxl import load_workbook

from accounts.models import RoleFieldAccess
from contacts.models import Contact
from core.models import ChoiceOption
from deals.models import Deal
from investors.models import Investor
from outreach.models import Company

from .base import APITestBase

EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


class FieldAccessBase(APITestBase):
    def setUp(self):
        super().setUp()
        a = self.admin
        self.role = self.make_role("Analyst", EVERYTHING)
        for module, field, level in (("deals", "success_fee_pct", "hidden"), ("deals", "agreed_retainer", "read"),
                                     ("deals", "target_close_date", "hidden"), ("companies", "sector", "hidden"),
                                     ("contacts", "work_email", "hidden"), ("contacts", "firm", "hidden")):
            RoleFieldAccess.objects.create(role=self.role, module=module, field=field, level=level)
        self.analyst = self.make_user("ana@firm.com", self.role, full_name="Ana Lyst")
        self.c = self.client_for(self.analyst)
        pharma = ChoiceOption.objects.filter(choice_list__key="sectors").first()
        self.co = Company.objects.create(name="Zeta Pharma", sector=pharma, city="Pune", created_by=a, updated_by=a)
        self.deal = Deal.objects.create(company=self.co, stage="data_collection", success_fee_pct=2.5,
                                        agreed_retainer_amount=10, agreed_retainer_currency="INR", agreed_retainer_unit="Lakh",
                                        target_close_date="2026-11-01", created_by=a, updated_by=a)
        self.northwind = Investor.objects.create(firm_name="Northwind Ventures", created_by=a, updated_by=a)
        self.priya = Contact.objects.create(full_name="Priya Shah", investor=self.northwind, work_email="priya@northwind.example",
                                            designation="Partner", created_by=a, updated_by=a)


class ReadTests(FieldAccessBase):
    def test_hidden_fields_are_not_sent(self):
        r = self.c.get(f"/api/m/deals/{self.deal.pk}/").json()
        self.assertNotIn("success_fee_pct", r)
        self.assertNotIn("target_close_date", r)
        self.assertNotIn("sector", r)  # a company detail, ruled by Companies
        self.assertIn("agreed_retainer", r)  # read-only is still seen
        rows = self.c.get("/api/m/deals/").json()["results"]
        self.assertNotIn("success_fee_pct", rows[0])
        self.assertNotIn("sector", self.c.get(f"/api/m/companies/{self.co.pk}/").json())
        # The admin sees everything.
        self.assertIn("success_fee_pct", self.client.get(f"/api/m/deals/{self.deal.pk}/").json())

    def test_schema(self):
        mods = {m["key"]: m for m in self.c.get("/api/schema/").json()["modules"]}
        keys = {f["key"]: f for f in mods["deals"]["fields"]}
        self.assertNotIn("success_fee_pct", keys)
        self.assertTrue(keys["agreed_retainer"]["readonly"])
        self.assertEqual(mods["contacts"]["segments"], None)  # the firm switch would show the hidden link
        inv_sections = {s["key"]: s for s in mods["investors"]["sections"]}
        self.assertEqual(next(s for s in inv_sections.values() if s.get("module") == "contacts")["status"], "hidden")

    def test_search_filters_and_sorting_ignore_hidden_fields(self):
        self.assertEqual(self.c.get("/api/m/contacts/?q=priya@northwind").json()["count"], 0)
        self.assertEqual(self.client.get("/api/m/contacts/?q=priya@northwind").json()["count"], 1)
        self.assertEqual(self.c.get(f"/api/m/contacts/?firm=investors:{self.northwind.pk}").json()["count"], 0)
        self.assertEqual(self.c.get("/api/m/deals/?success_fee_pct_min=99").json()["count"], 1)  # filter ignored
        self.assertEqual(self.c.get("/api/m/deals/?ordering=-success_fee_pct").status_code, 200)

    def test_subtitles_lookups_and_search(self):
        hit = self.c.get("/api/lookup/companies/?q=zeta").json()[0]
        self.assertEqual(hit["subtitle"], "")
        self.assertIn("Pune", self.client.get("/api/lookup/companies/?q=zeta").json()[0]["subtitle"])
        found = self.c.get("/api/search/?q=priya").json()
        self.assertNotIn("Northwind", str(found))

    def test_export_and_report(self):
        r = self.c.get("/api/m/deals/export/?columns=success_fee_pct,agreed_retainer,stage")
        headers = [c.value for c in next(load_workbook(io.BytesIO(r.content)).active.iter_rows(max_row=1))]
        self.assertNotIn("Success fee %", headers)
        self.assertTrue(any(h and h.startswith("Agreed retainer") for h in headers))
        r = self.c.post("/api/reports/run/", {"config": {"module": "deals", "format": "csv",
                                                          "columns": ["success_fee_pct", "stage"]}}, format="json")
        self.assertNotIn("Success fee", r.content.decode("utf-8-sig"))

    def test_dashboard_insights_matching_mail(self):
        d = self.c.get("/api/dashboard/").json()
        self.assertEqual(d["near_close"], [])
        self.assertNotIn("target_close_date", str(d["expiring"]))
        self.assertIsNotNone(self.client.get("/api/dashboard/").json()["near_close"])
        a = self.c.get("/api/insights/analytics/?period=all").json()
        self.assertIsNone(a["deals"]["by"]["sector"])
        self.assertIsNotNone(self.client.get("/api/insights/analytics/?period=all").json()["deals"]["by"]["sector"])
        m = self.c.get(f"/api/deals/{self.deal.pk}/matches/").json()
        self.assertIsNone(m["facts"]["sector"])
        sector = next(x for x in m["results"][0]["reasons"] if x["factor"] == "sector")
        self.assertEqual(sector["text"], "Not shown to your role")
        people = self.c.get(f"/api/mail/recipients/?about=investors:{self.northwind.pk}").json()["people"]
        self.assertEqual(people, [])  # only a work email, which this role can't see


class WriteTests(FieldAccessBase):
    def test_hidden_and_read_only_fields_are_refused(self):
        r = self.c.patch(f"/api/m/deals/{self.deal.pk}/", {"success_fee_pct": 9}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(f"/api/m/deals/{self.deal.pk}/", {"agreed_retainer": {"amount": 1, "currency": "INR", "unit": "Cr"}},
                         format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("can't change", str(r.json()))
        self.deal.refresh_from_db()
        self.assertEqual(float(self.deal.success_fee_pct), 2.5)
        self.assertEqual(self.c.patch(f"/api/m/deals/{self.deal.pk}/", {"instrument": "equity"}, format="json").status_code, 200)

    def test_bulk_edit_and_import_leave_them_alone(self):
        r = self.c.post("/api/m/deals/bulk/", {"ids": [self.deal.pk], "action": "edit",
                                               "values": {"agreed_retainer": {"amount": 1, "currency": "INR", "unit": "Cr"}}},
                        format="json")
        self.assertEqual(r.status_code, 400)
        from core import importer
        from core.current import _request

        class Req:
            pass

        req = Req()
        req.user = self.analyst
        token = _request.set(req)
        try:
            from core.registry import enabled_map, get_module

            fields = [f["key"] for f in importer.importable_fields(get_module("contacts"), enabled_map("contacts"))]
        finally:
            _request.reset(token)
        self.assertNotIn("work_email", fields)
        self.assertIn("designation", fields)


class RoleScreenTests(APITestBase):
    def test_saving_rules(self):
        body = {"name": "Junior", "password": self.password, "perms": {"deals": {"view": True}},
                "field_access": {"deals": {"success_fee_pct": "hidden", "agreed_retainer": "read"}}}
        r = self.client.post("/api/roles/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["field_access"], {"deals": {"success_fee_pct": "hidden", "agreed_retainer": "read"}})
        rid = r.json()["id"]
        for bad in ({"deals": {"company": "hidden"}}, {"deals": {"stage": "hidden"}}, {"deals": {"sector": "hidden"}},
                    {"deals": {"nope": "hidden"}}, {"deals": {"success_fee_pct": "secret"}}, {"zzz": {}}):
            r = self.client.patch(f"/api/roles/{rid}/", {"field_access": bad, "password": self.password}, format="json")
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.patch(f"/api/roles/{rid}/", {"field_access": {"companies": {"sector": "hidden"}}}, format="json")
        self.assertEqual(r.status_code, 400)  # no password
        r = self.client.patch(f"/api/roles/{rid}/", {"field_access": {"companies": {"sector": "hidden"}},
                                                     "password": self.password}, format="json")
        self.assertEqual(r.json()["field_access"], {"companies": {"sector": "hidden"}})
