import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from accounts.models import Role
from contacts.models import Contact
from core.importer import parse_birthday
from core.models import ModuleLayout
from core.tests.base import APITestBase
from investors.models import Investor

URL = "/api/m/contacts/"


def xlsx(rows, name="contacts.xlsx"):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


class ContactTestBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.blume = self.client.post("/api/m/investors/", {"firm_name": "Blume Ventures"}, format="json").json()
        self.kotak = self.client.post("/api/m/investors/", {"firm_name": "Kotak Alternate Assets"}, format="json").json()

    def link(self, inv):
        return f"investors:{inv['id']}"

    def contact(self, **kw):
        d = {"full_name": "Karthik Reddy", "firm": self.link(self.blume)}
        d.update(kw)
        r = self.client.post(URL, d, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()


class ContactCrudTests(ContactTestBase):
    def test_create_with_firm_link(self):
        c = self.contact(designation="Partner", decision_level=self.opt("decision_maker_levels", "Partner / MD").pk,
                         primary_contact=True, mobile="+91 98200 12345", birthday="03-12",
                         preferred_channel="whatsapp", tags=["LP"])
        self.assertEqual(c["firm"], {"module": "investors", "id": self.blume["id"], "title": "Blume Ventures", "archived": False})
        self.assertEqual(c["birthday"], "03-12")
        self.assertTrue(c["primary_contact"])
        self.assertEqual(Contact.objects.get().investor_id, self.blume["id"])
        # object form works too, and unlinking
        r = self.client.patch(f"{URL}{c['id']}/", {"firm": {"module": "investors", "id": self.kotak["id"]}}, format="json")
        self.assertEqual(r.json()["firm"]["title"], "Kotak Alternate Assets")
        r = self.client.patch(f"{URL}{c['id']}/", {"firm": None}, format="json")
        self.assertIsNone(r.json()["firm"])

    def test_bad_links(self):
        for bad in ("investors:999999", "outreach:1", "nonsense", {"module": "investors", "id": "x"}):
            r = self.client.post(URL, {"full_name": "X", "firm": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("firm", r.json())

    def test_archived_firm(self):
        c = self.contact()
        self.client.delete(f"/api/m/investors/{self.blume['id']}/")
        # still shown, flagged as archived
        r = self.client.get(f"{URL}{c['id']}/").json()
        self.assertTrue(r["firm"]["archived"])
        # the contact can still be saved while keeping it
        self.assertEqual(self.client.patch(f"{URL}{c['id']}/", {"firm": self.link(self.blume), "city": "Mumbai"}, format="json").status_code, 200)
        # but nobody new can be linked to it
        r = self.client.post(URL, {"full_name": "New", "firm": self.link(self.blume)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Archive", r.json()["firm"])

    def test_permanently_deleted_firm_unlinks(self):
        c = self.contact()
        self.client.delete(f"/api/m/investors/{self.blume['id']}/")
        self.client.delete(f"/api/archive/investors/{self.blume['id']}/")
        self.assertIsNone(self.client.get(f"{URL}{c['id']}/").json()["firm"])

    def test_birthdays(self):
        ok = {"03-12": "03-12", "--03-12": "03-12", "1985-03-12": "1985-03-12", "02-29": "02-29",
              "2000-02-29": "2000-02-29"}
        for given, stored in ok.items():
            self.assertEqual(self.contact(full_name=given, birthday=given)["birthday"], stored, given)
        self.assertEqual(self.contact(full_name="d", birthday={"day": 5, "month": 11, "year": None})["birthday"], "11-05")
        for bad in ("13-01", "02-30", "1985-02-29", "1850-01-01", "yesterday"):
            r = self.client.post(URL, {"full_name": "X", "birthday": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_firm_field_cannot_be_switched_off(self):
        r = self.client.patch("/api/settings/fields/", {"module": "contacts", "field_key": "firm", "enabled": False}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ModuleLayout.objects.exists())


class ContactListTests(ContactTestBase):
    def setUp(self):
        super().setUp()
        pm = self.opt("decision_maker_levels", "Partner / MD").pk
        vp = self.opt("decision_maker_levels", "Principal / VP").pk
        self.contact(full_name="Karthik Reddy", designation="Partner", decision_level=pm, primary_contact=True, birthday="1980-03-12")
        self.contact(full_name="Anita Rao", designation="", decision_level=vp, birthday="11-02")
        self.contact(full_name="Rohit Shah", firm=self.link(self.kotak), designation="Associate")
        self.contact(full_name="Meera Lawyer", firm=None, designation="Counsel")

    def names(self, qs):
        return [r["full_name"] for r in self.client.get(URL + "?" + qs).json()["results"]]

    def test_side_and_firm_filters(self):
        self.assertEqual(sorted(self.names(f"firm={self.link(self.blume)}")), ["Anita Rao", "Karthik Reddy"])
        self.assertEqual(sorted(self.names("firm_type=investors")), ["Anita Rao", "Karthik Reddy", "Rohit Shah"])
        self.assertEqual(self.names("firm_type=none"), ["Meera Lawyer"])
        self.assertEqual(self.names("firm=investors:abc"), [])

    def test_other_filters(self):
        self.assertEqual(self.names("primary_contact=true"), ["Karthik Reddy"])
        self.assertEqual(self.names(f"decision_level={self.opt('decision_maker_levels', 'Principal / VP').pk}"), ["Anita Rao"])
        self.assertEqual(self.names("birthday_month=3"), ["Karthik Reddy"])
        self.assertEqual(sorted(self.names("birthday_month=3,11")), ["Anita Rao", "Karthik Reddy"])

    def test_search_includes_firm_name(self):
        self.assertEqual(self.names("q=kotak"), ["Rohit Shah"])
        hits = self.client.get("/api/search/?q=kotak").json()["results"]
        self.assertEqual({(h["module"], h.get("title")) for h in hits},
                         {("investors", "Kotak Alternate Assets"), ("contacts", "Rohit Shah")})
        contact_hit = next(h for h in hits if h["module"] == "contacts")
        self.assertEqual(contact_hit["subtitle"], "Associate · Kotak Alternate Assets")

    def test_ordering(self):
        firms = [r["firm"]["title"] if r["firm"] else None for r in self.client.get(URL + "?ordering=firm").json()["results"]]
        self.assertEqual(firms, ["Blume Ventures", "Blume Ventures", "Kotak Alternate Assets", None])
        # blanks sort last either way
        self.assertEqual(self.names("ordering=designation")[-1], "Anita Rao")
        self.assertEqual(self.names("ordering=-designation")[-1], "Anita Rao")
        # several keys; decision level follows the list's order (seniority), not the alphabet
        self.assertEqual(self.names(f"firm={self.link(self.blume)}&ordering=-primary_contact,decision_level,full_name"),
                         ["Karthik Reddy", "Anita Rao"])
        self.assertEqual(self.names("ordering=decision_level")[:2], ["Karthik Reddy", "Anita Rao"])

    def test_schema_segments_and_sections(self):
        schema = self.client.get("/api/schema/").json()
        contacts = next(m for m in schema["modules"] if m["key"] == "contacts")
        self.assertEqual(contacts["segments"], {"param": "firm_type", "default": "", "options": [
            {"value": "", "label": "All"}, {"value": "investors", "label": "Investor side"},
            {"value": "companies", "label": "Company side"}, {"value": "none", "label": "Not linked"}]})
        firm = next(f for f in contacts["fields"] if f["key"] == "firm")
        self.assertEqual(firm["targets"], ["investors", "companies"])
        self.assertFalse(firm["can_disable"])
        investors = next(m for m in schema["modules"] if m["key"] == "investors")
        sections = {s["key"]: s["status"] for s in investors["sections"]}
        self.assertEqual(sections, {"contacts": "ready", "tasks": "ready", "documents": "ready", "deal_history": "ready"})
        # someone who can't see contacts doesn't get the section
        u = self.make_user("inv@firm.com", self.make_role("Investors only", {"investors": ["view"]}))
        investors = self.client_for(u).get("/api/schema/").json()["modules"][0]
        self.assertEqual({s["key"]: s["status"] for s in investors["sections"]}["contacts"], "hidden")


class LookupTests(ContactTestBase):
    def test_lookup_permissions(self):
        r = self.client.get("/api/lookup/investors/?q=blu").json()
        self.assertEqual([(x["title"], x["module"]) for x in r], [("Blume Ventures", "investors")])
        # can add contacts but can't open Investors: may still pick a firm by name
        u = self.make_user("c@firm.com", self.make_role("Contacts", {"contacts": ["view", "create"]}))
        self.assertEqual(self.client_for(u).get("/api/lookup/investors/?q=kot").status_code, 200)
        self.assertEqual(self.client_for(u).get("/api/m/investors/").status_code, 403)
        # view-only on contacts: no reason to look firms up
        v = self.make_user("v@firm.com", self.make_role("Viewer", {"contacts": ["view"]}))
        self.assertEqual(self.client_for(v).get("/api/lookup/investors/?q=kot").status_code, 403)

    def test_lookup_by_ids_includes_archived(self):
        self.client.delete(f"/api/m/investors/{self.blume['id']}/")
        self.assertEqual(self.client.get("/api/lookup/investors/?q=blu").json(), [])
        r = self.client.get(f"/api/lookup/investors/?ids={self.blume['id']},{self.kotak['id']}").json()
        self.assertEqual({(x["title"], x["archived"]) for x in r}, {("Blume Ventures", True), ("Kotak Alternate Assets", False)})


class ContactImportTests(ContactTestBase):
    BASE = "/api/m/contacts/import/"

    def upload(self, f):
        r = self.client.post(self.BASE + "upload/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_parse_birthday(self):
        cases = {"12 Mar": "03-12", "12-Mar": "03-12", "March 12": "03-12", "Mar 12, 1985": "1985-03-12",
                 "12/03": "03-12", "12/03/1985": "1985-03-12", "1985-03-12": "1985-03-12",
                 "1604-03-12": "03-12", "3rd Nov": "11-03"}
        for raw, want in cases.items():
            self.assertEqual(parse_birthday(raw, "DD/MM/YYYY"), want, raw)
        self.assertEqual(parse_birthday("03/12", "MM/DD/YYYY"), "03-12")

    def test_linkedin_style_export(self):
        self.contact(full_name="Existing Person", work_email="dup@blume.vc")
        self.client.post("/api/m/investors/", {"firm_name": "Kotak Investment Advisors"}, format="json")
        f = xlsx([
            ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Birthday", "Connected On"],
            ["Sanjay", "Nath", "https://www.linkedin.com/in/sanjaynath/", "sanjay@blume.vc", "Blume", "Partner", "12 Mar", "01 Jan 2024"],
            ["Priya", "Iyer", "", "PRIYA@acme.com", "Acme Capital", "VP", "", ""],
            ["Dup", "Person", "", "dup@blume.vc", "Blume Ventures", "", "", ""],
            ["Rohit", "Shah", "", "", "Kotak", "Associate", "", ""],  # two Kotak firms: ambiguous
        ])
        up = self.upload(f)
        self.assertEqual([m["field"] for m in up["mapping"]],
                         ["full_name", "full_name", "linkedin", "work_email", "firm", "designation", "birthday", None])
        self.assertEqual(up["can_create_links"], {"firm": ["investors", "companies"]})

        r = self.client.post(self.BASE + "check/", {"token": up["token"], "mapping": up["mapping"]}, format="json").json()
        rows = {x["row"]: x for x in r["rows"]}
        self.assertEqual(rows[2]["title"], "Sanjay Nath")
        self.assertEqual(rows[2]["errors"], {})
        self.assertTrue(any("Acme Capital" in w for w in rows[3]["warnings"]))
        self.assertEqual(rows[4]["duplicate_of"][0]["title"], "Existing Person")
        self.assertTrue(any("matches 2" in w for w in rows[5]["warnings"]))
        self.assertEqual(r["summary"]["new_records"], {})

        # ask for missing firms to be created
        opts = {"token": up["token"], "mapping": up["mapping"], "create_links": {"firm": "investors"}}
        r = self.client.post(self.BASE + "check/", opts, format="json").json()
        self.assertEqual(r["summary"]["new_records"], {"Investors": ["Acme Capital"]})
        r = self.client.post(self.BASE + "commit/", {**opts, "include_rows": [2, 3, 5]}, format="json").json()
        self.assertEqual((r["created"], r["new_records"]), (3, {"Investors": 1}), r)

        sanjay = Contact.objects.get(full_name="Sanjay Nath")
        self.assertEqual(sanjay.investor.firm_name, "Blume Ventures")
        self.assertEqual((sanjay.birthday, sanjay.birthday_year), ("03-12", None))
        self.assertEqual(sanjay.linkedin, "https://www.linkedin.com/in/sanjaynath/")
        acme = Investor.objects.get(firm_name="Acme Capital")
        self.assertEqual(acme.created_by, self.admin)
        self.assertEqual(Contact.objects.get(full_name="Priya Iyer").investor, acme)
        self.assertIsNone(Contact.objects.get(full_name="Rohit Shah").investor)

    def test_duplicates_by_mobile_linkedin_and_name_at_firm(self):
        self.contact(full_name="Karthik Reddy", mobile="+91 98200 12345", linkedin="linkedin.com/in/karthikr")
        f = xlsx([
            ["Name", "Firm", "Mobile", "LinkedIn"],
            ["K. Reddy", "", "098200 12345", ""],
            ["Someone", "", "", "https://in.linkedin.com/in/KarthikR?trk=x"],
            ["Karthik  Reddy", "Blume Ventures", "", ""],
            ["Karthik Reddy", "Kotak Alternate Assets", "", ""],
        ])
        up = self.upload(f)
        r = self.client.post(self.BASE + "check/", {"token": up["token"], "mapping": up["mapping"]}, format="json").json()
        dups = {x["row"]: bool(x["duplicate_of"]) for x in r["rows"]}
        self.assertEqual(dups, {2: True, 3: True, 4: True, 5: False})

    def test_create_links_needs_create_permission(self):
        role = self.make_role("Contacts", {"contacts": ["view", "create"], "investors": ["view"]})
        c = self.client_for(self.make_user("c@firm.com", role))
        up = c.post(self.BASE + "upload/", {"file": xlsx([["Name", "Company"], ["A", "New Firm"]])}, format="multipart").json()
        self.assertEqual(up["can_create_links"], {"firm": []})  # Investors: view only; Outreach: none
        opts = {"token": up["token"], "mapping": up["mapping"], "create_links": {"firm": "investors"}, "include_rows": [2]}
        r = c.post(self.BASE + "commit/", opts, format="json").json()
        self.assertEqual(r["created"], 1)
        self.assertFalse(Investor.objects.filter(firm_name="New Firm").exists())

    def test_export_round_trip(self):
        self.contact(full_name="Karthik Reddy", designation="Partner", birthday="1980-03-12", work_email="k@blume.vc")
        self.contact(full_name="Rohit Shah", firm=self.link(self.kotak), birthday="11-02")
        data = self.client.get("/api/m/contacts/export/?ordering=full_name").content
        ws = load_workbook(io.BytesIO(data)).active
        headers = [c.value for c in ws[1]]
        rows = [{h: c.value for h, c in zip(headers, r)} for r in ws.iter_rows(min_row=2)]
        self.assertEqual([(r["Full name"], r["Firm / company"], r["Birthday"]) for r in rows],
                         [("Karthik Reddy", "Blume Ventures", "12 Mar 1980"), ("Rohit Shah", "Kotak Alternate Assets", "2 Nov")])
        for c in Contact.objects.all():
            self.client.delete(f"{URL}{c.pk}/")
        up = self.upload(SimpleUploadedFile("e.xlsx", data))
        r = self.client.post(self.BASE + "commit/", {"token": up["token"], "mapping": up["mapping"], "include_rows": [2, 3]}, format="json")
        self.assertEqual(r.json()["created"], 2, r.content)
        k = Contact.objects.get(full_name="Karthik Reddy", archived_at__isnull=True)
        self.assertEqual((k.investor_id, k.birthday, k.birthday_year, k.designation), (self.blume["id"], "03-12", 1980, "Partner"))


class ContactPermissionTests(ContactTestBase):
    def test_contacts_permissions_are_separate(self):
        u = self.make_user("i@firm.com", self.make_role("Investors only", {"investors": ["view", "create"]}))
        c = self.client_for(u)
        self.assertEqual(c.get(URL).status_code, 403)
        self.assertEqual(c.get(f"{URL}?firm={self.link(self.blume)}").status_code, 403)
        self.assertEqual(c.post(URL, {"full_name": "X"}, format="json").status_code, 403)

    def test_member_role_can_add_contacts(self):
        u = self.make_user("m@firm.com", Role.objects.get(name="Member"))
        r = self.client_for(u).post(URL, {"full_name": "X", "firm": self.link(self.blume)}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client_for(u).delete(f"{URL}{r.json()['id']}/").status_code, 403)
