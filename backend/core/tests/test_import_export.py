import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from accounts.models import Role
from core.importer import money_hint, parse_date, parse_money
from core.models import ChoiceOption
from investors.models import Investor

from .base import APITestBase

BASE = "/api/m/investors/import/"


def xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("investors.xlsx", buf.getvalue())


class ParsingTests(APITestBase):
    def test_money(self):
        self.assertEqual(parse_money("₹ 50 Cr"), {"amount": "50", "currency": "INR", "unit": "Cr"})
        self.assertEqual(parse_money("USD 12.5 million"), {"amount": "12.5", "currency": "USD", "unit": "Mn"})
        self.assertEqual(parse_money("1,50,000", "INR", ""), {"amount": "150000", "currency": "INR", "unit": ""})
        self.assertEqual(parse_money("$5mn"), {"amount": "5", "currency": "USD", "unit": "Mn"})
        self.assertEqual(money_hint("Min ticket (INR Cr)"), {"currency": "INR", "unit": "Cr"})
        self.assertEqual(money_hint("Ticket size ($ Mn)"), {"currency": "USD", "unit": "Mn"})

    def test_dates(self):
        self.assertEqual(parse_date("03/04/2026", "DD/MM/YYYY"), "2026-04-03")
        self.assertEqual(parse_date("03/04/2026", "MM/DD/YYYY"), "2026-03-04")
        self.assertEqual(parse_date("12 Mar 2025", "DD/MM/YYYY"), "2025-03-12")
        self.assertEqual(parse_date("2025-03-12", "DD/MM/YYYY"), "2025-03-12")


class ImportFlowTests(APITestBase):
    def setUp(self):
        super().setUp()
        self.client.post("/api/m/investors/", {"firm_name": "Blume Ventures", "website": "https://www.blume.vc"}, format="json")
        self.bob = self.make_user("bob@firm.com", self.admin_role, full_name="Bob Banker")

    def upload(self, f):
        r = self.client.post(BASE + "upload/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_full_flow(self):
        f = xlsx([
            ["Investor Name", "Type", "City", "Sector", "Min ticket (INR Cr)", "Max ticket", "Owner", "Last contact", "Random"],
            ["Blume", "VC", "Mumbai", "Fintech", 5, "₹ 40 Cr", "Bob Banker", "03/04/2026", "x"],   # dup: generic words ignored
            ["Blume Ventures", "VC", "Mumbai", "Fintech; Space", 5, "₹ 40 Cr", "", "", ""],        # dup of existing by name
            ["Kotak Alt", "PE", "Mumbai", "Healthcare, Fintech", 50, "₹ 300 Cr", "bob@firm.com", "2026-01-15", ""],
            ["", "VC", "Delhi", "", "", "", "", "", ""],                                             # missing name
            ["Kotak alt.", "PE", "", "", "abc", "", "", "", ""],                                     # dup in file + bad money
        ])
        up = self.upload(f)
        self.assertEqual(up["row_count"], 5)
        m = up["mapping"]
        self.assertEqual([x["field"] for x in m],
                         ["firm_name", "investor_type", "hq_city", "sectors", "min_ticket", "max_ticket",
                          "relationship_owner", "last_contacted", None])
        self.assertEqual(m[4]["currency"], "INR")
        self.assertEqual(m[4]["unit"], "Cr")

        r = self.client.post(BASE + "check/", {"token": up["token"], "mapping": m}, format="json").json()
        rows = {x["row"]: x for x in r["rows"]}
        self.assertEqual(rows[2]["errors"], {})
        self.assertEqual(rows[2]["duplicate_of"][0]["title"], "Blume Ventures")
        self.assertEqual(rows[3]["duplicate_of"][0]["title"], "Blume Ventures")
        self.assertTrue(any("Space" in w for w in rows[3]["warnings"]))
        self.assertIn("Firm name", rows[5]["errors"])
        self.assertIn("Min ticket size", rows[6]["errors"])
        self.assertEqual(rows[6]["duplicate_of_row"], 4)
        self.assertEqual(r["summary"]["ok"], 1)
        self.assertEqual(r["summary"]["duplicates"], 2)

        r = self.client.post(BASE + "commit/", {"token": up["token"], "mapping": m, "include_rows": [2, 4]}, format="json")
        self.assertEqual(r.json()["created"], 2, r.content)
        kotak = Investor.objects.get(firm_name="Kotak Alt")
        self.assertEqual(kotak.min_ticket_amount, 50)
        self.assertEqual((kotak.min_ticket_currency, kotak.min_ticket_unit), ("INR", "Cr"))
        self.assertEqual(kotak.relationship_owner, self.bob)
        self.assertEqual(sorted(o.label for o in kotak.sectors.all()), ["Fintech", "Healthcare"])
        self.assertEqual(str(Investor.objects.get(firm_name="Blume").last_contacted), "2026-04-03")
        self.assertEqual(kotak.created_by, self.admin)
        # token is single-use
        r = self.client.post(BASE + "check/", {"token": up["token"], "mapping": m}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_add_missing_options(self):
        up = self.upload(SimpleUploadedFile("a.csv", "Name,Sector\nOrbit Fund,Space tech\n".encode()))
        r = self.client.post(BASE + "check/", {"token": up["token"], "mapping": up["mapping"], "add_missing_options": True}, format="json").json()
        self.assertEqual(r["summary"]["new_options"], {"Sectors": ["Space tech"]})
        self.client.post(BASE + "commit/", {"token": up["token"], "mapping": up["mapping"], "include_rows": [2],
                                            "add_missing_options": True}, format="json")
        opt = ChoiceOption.objects.get(label="Space tech")
        self.assertEqual(list(Investor.objects.get(firm_name="Orbit Fund").sectors.all()), [opt])

    def test_website_duplicate_and_csv(self):
        up = self.upload(SimpleUploadedFile("a.csv", "Firm name;Website\nBlume VC;blume.vc\n".encode("utf-8-sig")))
        r = self.client.post(BASE + "check/", {"token": up["token"], "mapping": up["mapping"]}, format="json").json()
        self.assertEqual(r["rows"][0]["duplicate_of"][0]["title"], "Blume Ventures")

    def test_mapping_rules(self):
        up = self.upload(SimpleUploadedFile("a.csv", b"City,Other\nMumbai,x\n"))
        r = self.client.post(BASE + "check/", {"token": up["token"], "mapping": up["mapping"]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Firm name", r.json()["detail"])

    def test_bad_files(self):
        r = self.client.post(BASE + "upload/", {"file": SimpleUploadedFile("a.pdf", b"%PDF")}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(BASE + "upload/", {"file": SimpleUploadedFile("a.xlsx", b"garbage")}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_other_user_cannot_use_token(self):
        up = self.upload(SimpleUploadedFile("a.csv", b"Name\nX\n"))
        r = self.client_for(self.bob).post(BASE + "check/", {"token": up["token"], "mapping": up["mapping"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_import_needs_create(self):
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"investors": ["view"]}))
        r = self.client_for(viewer).post(BASE + "upload/", {"file": SimpleUploadedFile("a.csv", b"Name\nX\n")}, format="multipart")
        self.assertEqual(r.status_code, 403)


class ExportTests(APITestBase):
    def test_export_respects_filters_and_switches(self):
        vc = ChoiceOption.objects.get(choice_list__key="investor_types", label="VC")
        self.client.post("/api/m/investors/", {"firm_name": "Blume", "investor_type": vc.pk,
                                               "min_ticket": {"amount": "5", "currency": "INR", "unit": "Cr"},
                                               "last_contacted": "2026-09-01"}, format="json")
        self.client.post("/api/m/investors/", {"firm_name": "Kotak"}, format="json")
        self.client.patch("/api/settings/fields/", {"module": "investors", "field_key": "notes", "enabled": False}, format="json")
        r = self.client.get(f"/api/m/investors/export/?investor_type={vc.pk}")
        self.assertEqual(r.status_code, 200)
        ws = load_workbook(io.BytesIO(r.content)).active
        headers = [c.value for c in ws[1]]
        self.assertIn("Min ticket size – currency & unit", headers)
        self.assertNotIn("Notes", headers)
        self.assertEqual(ws.max_row, 2)
        row = {h: c.value for h, c in zip(headers, ws[2])}
        self.assertEqual(row["Firm name"], "Blume")
        self.assertEqual(row["Investor type"], "VC")
        self.assertEqual(row["Min ticket size"], 5)
        self.assertEqual(row["Min ticket size – currency & unit"], "INR Cr")
        self.assertEqual(row["Created by"], "Asha Admin")

    def test_export_round_trips_through_import(self):
        self.client.post("/api/m/investors/", {"firm_name": "Blume", "min_ticket": {"amount": "5", "currency": "USD", "unit": "Mn"}}, format="json")
        data = self.client.get("/api/m/investors/export/").content
        self.client.delete(f"/api/m/investors/{Investor.objects.get().pk}/")
        up = self.client.post("/api/m/investors/import/upload/", {"file": SimpleUploadedFile("e.xlsx", data)}, format="multipart").json()
        fields = [m["field"] for m in up["mapping"]]
        self.assertIn("min_ticket:cu", fields)
        r = self.client.post("/api/m/investors/import/commit/", {"token": up["token"], "mapping": up["mapping"], "include_rows": [2]}, format="json")
        self.assertEqual(r.json()["created"], 1, r.content)
        inv = Investor.objects.get(archived_at__isnull=True)
        self.assertEqual((inv.min_ticket_amount, inv.min_ticket_currency, inv.min_ticket_unit), (5, "USD", "Mn"))

    def test_export_needs_view(self):
        u = self.make_user("n@firm.com", Role.objects.create(name="Nobody"))
        self.assertEqual(self.client_for(u).get("/api/m/investors/export/").status_code, 403)
