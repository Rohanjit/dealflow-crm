"""Custom fields and layouts (Settings > Fields & layout): adding fields of every type, using them
everywhere a built-in field works, moving / renaming / hiding fields, linked fields in other
modules, switching back to the original layout, and the password on save."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook

from contacts.models import Contact
from core.models import ChoiceList, CustomField
from deals.models import Deal
from investors.models import Investor
from outreach.models import Company, Outreach

from .base import APITestBase

ALL_TYPES = [
    ("Strategy", "text", {}), ("Thesis", "longtext", {}), ("Team size", "integer", {}), ("Hit rate", "decimal", {}),
    ("Carry", "percent", {}), ("Dry powder", "money", {}), ("Next fund close", "date", {}), ("Next call", "datetime", {}),
    ("ESG mandate", "boolean", {}), ("Co-invests", "tristate", {}), ("Fund type", "dropdown", {"options": ["Evergreen", "Closed-end"]}),
    ("Themes", "dropdown", {"multi": True, "options": ["Climate", "Fintech", "Health"]}), ("Coverage banker", "user", {}),
    ("IR email", "email", {}), ("IR phone", "phone", {}), ("Data room", "url", {}), ("Labels", "tags", {}),
    ("Anchor LP", "link", {"targets": ["investors"]}), ("Portfolio companies", "link", {"targets": ["companies"], "multi": True}),
]


class CustomBase(APITestBase):
    def layout(self, module):
        r = self.client.get("/api/settings/layouts/")
        self.assertEqual(r.status_code, 200)
        return next(m for m in r.json()["modules"] if m["module"] == module)

    def save(self, module, *, password=None, expect=200, **changes):
        cur = self.layout(module)
        body = {"password": password or self.password, "customised": True, "sections": cur["saved"]["sections"],
                "items": cur["saved"]["items"], "labels": cur["saved"]["labels"], "hidden": cur["saved"]["hidden"], **changes}
        r = self.client.put(f"/api/settings/layouts/{module}/", body, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def add(self, module, fields, **more):
        new = [{"id": f"new{i}", "label": label, "type": t, **extra} for i, (label, t, extra) in enumerate(fields)]
        self.save(module, new_fields=new, **more)
        return {cf.label: cf for cf in CustomField.objects.filter(module=module)}

    def schema_fields(self, module):
        mods = self.client.get("/api/schema/").json()["modules"]
        return {f["key"]: f for f in next(m for m in mods if m["key"] == module)["fields"]}


class TypesTests(CustomBase):
    def test_every_type_saves_reads_filters_sorts_and_exports(self):
        cfs = self.add("investors", ALL_TYPES)
        k = {label: cf.key for label, cf in cfs.items()}
        fields = self.schema_fields("investors")
        self.assertTrue(all(fields[key]["custom"] for key in k.values()))
        evergreen, closed = (o.pk for o in ChoiceList.objects.get(key=k["Fund type"]).options.order_by("order"))
        climate, fintech, health = (o.pk for o in ChoiceList.objects.get(key=k["Themes"]).options.order_by("order"))
        anchor = Investor.objects.create(firm_name="Anchor LP Co", created_by=self.admin, updated_by=self.admin)
        co = Company.objects.create(name="Portfolio One", created_by=self.admin, updated_by=self.admin)
        values = {
            k["Strategy"]: "Growth buyouts", k["Thesis"]: "Long thoughts", k["Team size"]: 12, k["Hit rate"]: "2.5",
            k["Carry"]: "20", k["Dry powder"]: {"amount": "500", "currency": "INR", "unit": "Cr"},
            k["Next fund close"]: "2027-03-31", k["Next call"]: "2026-11-02T15:30", k["ESG mandate"]: True,
            k["Co-invests"]: "yes", k["Fund type"]: evergreen, k["Themes"]: [climate, fintech], k["Coverage banker"]: self.admin.pk,
            k["IR email"]: "ir@fund.com", k["IR phone"]: "+91 98100 00000", k["Data room"]: "dataroom.example.com",
            k["Labels"]: ["priority", "warm"], k["Anchor LP"]: f"investors:{anchor.pk}", k["Portfolio companies"]: [f"companies:{co.pk}"],
        }
        r = self.client.post("/api/m/investors/", {"firm_name": "Custom Capital", **values}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        rec = r.json()
        self.assertEqual(rec[k["Hit rate"]], "2.5")
        self.assertEqual(rec[k["Dry powder"]], {"amount": "500", "currency": "INR", "unit": "Cr"})
        self.assertEqual(rec[k["Data room"]], "https://dataroom.example.com")
        self.assertEqual(rec[k["Anchor LP"]]["title"], "Anchor LP Co")
        self.assertEqual([x["title"] for x in rec[k["Portfolio companies"]]], ["Portfolio One"])
        self.assertEqual(rec[k["Themes"]], [climate, fintech])
        other = self.client.post("/api/m/investors/", {"firm_name": "Plain Partners", k["Team size"]: 3, k["Fund type"]: closed,
                                                       k["Themes"]: [health], k["Dry powder"]: {"amount": "2", "currency": "INR", "unit": "Bn"}},
                                 format="json").json()

        def ids(q):
            r = self.client.get(f"/api/m/investors/?{q}")
            self.assertEqual(r.status_code, 200, (q, r.content))
            return [x["id"] for x in r.json()["results"]]

        mine, theirs = rec["id"], other["id"]
        self.assertEqual(ids(f"{k['Strategy']}=growth"), [mine])
        self.assertEqual(ids(f"{k['Team size']}_min=10"), [mine])
        self.assertEqual(ids(f"{k['Fund type']}={closed}"), [theirs])
        self.assertEqual(ids(f"{k['Themes']}={fintech}"), [mine])
        self.assertEqual(ids(f"{k['Themes']}={fintech + 100}"), [])
        self.assertEqual(ids(f"{k['ESG mandate']}=true"), [mine])
        self.assertEqual(ids(f"{k['Co-invests']}=yes"), [mine])
        self.assertEqual(set(ids(f"{k['Co-invests']}=unknown")), {theirs, anchor.pk})
        self.assertEqual(ids(f"{k['Labels']}=priority"), [mine])
        self.assertEqual(ids(f"{k['Next fund close']}_from=2027-01-01"), [mine])
        self.assertEqual(ids(f"{k['Next call']}_to=2026-11-02"), [mine])
        # ₹500 Cr (5 Bn) vs ₹2 Bn: only the first is at least ₹300 Cr... and both are at least ₹100 Cr.
        self.assertEqual(ids(f"{k['Dry powder']}_min=300&{k['Dry powder']}_unit=Cr&{k['Dry powder']}_currency=INR"), [mine])
        self.assertEqual(set(ids(f"{k['Dry powder']}_min=100&{k['Dry powder']}_unit=Cr")), {mine, theirs})
        self.assertEqual(ids(f"{k['Coverage banker']}=me"), [mine])
        self.assertEqual(ids(f"{k['Anchor LP']}=investors:{anchor.pk}"), [mine])
        self.assertEqual(ids(f"{k['Portfolio companies']}=companies:{co.pk}"), [mine])
        self.assertEqual(ids("q=growth buyouts"), [mine])
        # Sorting: by number, by money (within currency), by dropdown order; empty last.
        self.assertEqual(ids(f"ordering=-{k['Team size']}")[:2], [mine, theirs])
        self.assertEqual(ids(f"ordering=-{k['Dry powder']}")[:2], [mine, theirs])
        self.assertEqual(ids(f"ordering={k['Fund type']}")[:2], [mine, theirs])
        # Edit, clear, export.
        r = self.client.patch(f"/api/m/investors/{mine}/", {k["Strategy"]: "", k["Team size"]: 14}, format="json")
        self.assertEqual((r.json()[k["Strategy"]], r.json()[k["Team size"]]), ("", 14))
        self.assertNotIn(k["Strategy"], Investor.objects.get(pk=mine).custom)
        wb = load_workbook(io.BytesIO(self.client.get(f"/api/m/investors/export/?ids={mine}").content))
        rows = list(wb.active.values)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["Team size"], 14)
        self.assertEqual(row["Themes"], "Climate, Fintech")
        self.assertEqual(row["Anchor LP"], "Anchor LP Co")
        self.assertEqual(row["Dry powder – currency & unit"], "INR Cr")

    def test_bad_values_are_refused_like_built_in_fields(self):
        k = {label: cf.key for label, cf in self.add("investors", ALL_TYPES).items()}
        for key, bad in ((k["Team size"], "lots"), (k["Carry"], 120), (k["IR email"], "nope"), (k["Fund type"], 999999),
                         (k["Anchor LP"], "deals:1"), (k["Next fund close"], "2027-02-30"), (k["Dry powder"], {"amount": 1}),
                         (k["Labels"], 5), (k["Co-invests"], "maybe")):
            r = self.client.post("/api/m/investors/", {"firm_name": "Bad", key: bad}, format="json")
            self.assertEqual(r.status_code, 400, (key, bad))
            self.assertIn(key, r.json())

    def test_import_and_merge_and_bulk(self):
        k = {label: cf.key for label, cf in self.add("investors", [("Strategy", "text", {}), ("Themes", "dropdown", {"multi": True, "options": ["Climate", "Fintech"]})]).items()}
        f = SimpleUploadedFile("i.csv", b"Firm name,Strategy,Themes\nImported One,Buyout,Climate\nImported Two,Growth,\"Climate, Fintech\"\n")
        up = self.client.post("/api/m/investors/import/upload/", {"file": f}, format="multipart").json()
        self.assertEqual([m["field"] for m in up["mapping"]], ["firm_name", k["Strategy"], k["Themes"]])
        r = self.client.post("/api/m/investors/import/commit/", {"token": up["token"], "mapping": up["mapping"], "include_rows": [2, 3]}, format="json")
        self.assertEqual(r.json()["created"], 2)
        one, two = Investor.objects.get(firm_name="Imported One"), Investor.objects.get(firm_name="Imported Two")
        self.assertEqual(one.custom[k["Strategy"]], "Buyout")
        self.assertEqual(len(two.custom[k["Themes"]]), 2)
        r = self.client.post("/api/m/investors/bulk/", {"action": "edit", "ids": [one.pk, two.pk], "values": {k["Strategy"]: "Credit"}}, format="json")
        self.assertEqual(r.json()["updated"], 2)
        r = self.client.post("/api/m/investors/merge/", {"keep": one.pk, "merge": [two.pk], "values": {k["Themes"]: two.custom[k["Themes"]]}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        one.refresh_from_db()
        self.assertEqual(len(one.custom[k["Themes"]]), 2)

    def test_links_follow_merges_and_permanent_deletes(self):
        k = self.add("contacts", [("Introduced via", "link", {"targets": ["investors"]})])["Introduced via"].key
        a, b = (Investor.objects.create(firm_name=n, created_by=self.admin, updated_by=self.admin) for n in ("Alpha One", "Alpha Two"))
        c = self.client.post("/api/m/contacts/", {"full_name": "Ravi", k: f"investors:{b.pk}"}, format="json").json()
        self.client.post("/api/m/investors/merge/", {"keep": a.pk, "merge": [b.pk]}, format="json")
        self.assertEqual(Contact.objects.get(pk=c["id"]).custom[k], f"investors:{a.pk}")
        self.client.delete(f"/api/m/investors/{a.pk}/")
        self.client.delete(f"/api/archive/investors/{a.pk}/")
        self.assertNotIn(k, Contact.objects.get(pk=c["id"]).custom)


class LayoutTests(CustomBase):
    def test_password_and_permission(self):
        self.save("investors", password="wrong", expect=400)
        member = self.make_user("m@firm.com", self.make_role("Plain", {"investors": ["view", "edit"]}))
        c = self.client_for(member)
        self.assertEqual(c.get("/api/settings/layouts/").status_code, 403)
        self.assertEqual(c.put("/api/settings/layouts/investors/", {"password": self.password}, format="json").status_code, 403)
        self.assertEqual(self.client.put("/api/settings/layouts/tasks/", {"password": self.password, "hidden": ["description"]},
                                         format="json").status_code, 200)

    def test_names_must_be_unique(self):
        self.add("investors", [("Strategy", "text", {})])
        self.save("investors", new_fields=[{"id": "n1", "label": "strategy", "type": "text"}], expect=400)
        self.save("investors", new_fields=[{"id": "n1", "label": "HQ City", "type": "text"}], expect=400)
        self.save("investors", labels={"country": "Strategy"}, expect=400)
        self.save("investors", new_fields=[{"id": "n1", "label": "", "type": "text"}], expect=400)
        self.save("investors", new_fields=[{"id": "n1", "label": "Odd", "type": "spreadsheet"}], expect=400)

    def test_move_rename_hide_and_new_sections(self):
        cur = self.layout("investors")
        sections = [{"key": "deal_room", "label": "Deal room"}, *cur["saved"]["sections"]]
        items = [{"field": "firm_name", "section": "deal_room"}, {"field": "notes", "section": "deal_room"},
                 *[it for it in cur["saved"]["items"] if it["field"] not in ("firm_name", "notes")]]
        self.save("investors", sections=sections, items=items, labels={"hq_city": "Head office", "firm_name": "Nope"}, hidden=["country"])
        fields = list(self.schema_fields("investors").values())
        self.assertEqual([f["key"] for f in fields[:2]], ["firm_name", "notes"])
        self.assertEqual(fields[1]["group"], "Deal room")
        by_key = {f["key"]: f for f in fields}
        self.assertEqual(by_key["hq_city"]["label"], "Head office")
        self.assertEqual(by_key["firm_name"]["label"], "Firm name")  # the primary field can't be renamed
        self.assertFalse(by_key["country"]["enabled"])
        self.save("investors", hidden=["firm_name"], expect=400)
        # Back to the original layout: everything as it was, nothing lost.
        cur = self.layout("investors")
        r = self.client.put("/api/settings/layouts/investors/", {"password": self.password, "customised": False}, format="json")
        self.assertEqual(r.status_code, 200)
        fields = self.schema_fields("investors")
        self.assertEqual(fields["hq_city"]["label"], "HQ city")
        self.assertTrue(fields["country"]["enabled"])
        self.assertEqual(self.layout("investors")["saved"]["labels"], {"hq_city": "Head office"})

    def test_switching_off_hides_custom_fields_and_keeps_their_values(self):
        key = self.add("investors", [("Strategy", "text", {})])["Strategy"].key
        pk = self.client.post("/api/m/investors/", {"firm_name": "Kept Values", key: "Growth"}, format="json").json()["id"]
        self.client.put("/api/settings/layouts/investors/", {"password": self.password, "customised": False}, format="json")
        self.assertNotIn(key, self.client.get(f"/api/m/investors/{pk}/").json())
        r = self.client.patch(f"/api/m/investors/{pk}/", {key: "Changed while hidden"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Investor.objects.get(pk=pk).custom[key], "Growth")
        self.save("investors")
        self.assertEqual(self.client.get(f"/api/m/investors/{pk}/").json()[key], "Growth")

    def test_deleting_a_custom_field_hides_it_and_it_can_come_back(self):
        key = self.add("investors", [("Strategy", "text", {})])["Strategy"].key
        self.save("investors", deleted_fields=[key])
        self.assertNotIn(key, self.schema_fields("investors"))
        self.assertEqual([d["key"] for d in self.layout("investors")["deleted"]], [key])
        self.save("investors", restored_fields=[key])
        self.assertIn(key, self.schema_fields("investors"))

    def test_required_custom_field(self):
        key = self.add("investors", [("Strategy", "text", {"required": True})])["Strategy"].key
        old = Investor.objects.create(firm_name="Before", created_by=self.admin, updated_by=self.admin)
        self.assertEqual(self.client.post("/api/m/investors/", {"firm_name": "No strategy"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"/api/m/investors/{old.pk}/", {"hq_city": "Pune"}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(f"/api/m/investors/{old.pk}/", {key: ""}, format="json").status_code, 400)

    def test_dropdown_options_are_an_editable_list(self):
        key = self.add("investors", [("Fund type", "dropdown", {"options": ["Evergreen"]})])["Fund type"].key
        lists = self.client.get("/api/choice-lists/").json()["lists"]
        self.assertIn(key, [cl["key"] for cl in lists])
        self.save("investors", field_updates={key: {"label": "Vehicle type", "options": ["SPV"]}})
        self.assertEqual(ChoiceList.objects.get(key=key).label, "Vehicle type")
        self.assertEqual(sorted(ChoiceList.objects.get(key=key).options.values_list("label", flat=True)), ["Evergreen", "SPV"])


class LinkedFieldTests(CustomBase):
    def test_company_fields_show_on_outreach_and_deals_and_save_on_the_company(self):
        key = self.add("companies", [("Promoter group", "text", {})])["Promoter group"].key
        lay = self.layout("outreach")
        self.assertIn(key, [x["key"] for x in lay["linked"]])
        self.assertNotIn(key, self.schema_fields("outreach"))  # an option, not automatic
        self.save("outreach", items=[*lay["saved"]["items"], {"field": key, "section": lay["saved"]["sections"][0]["key"]}])
        f = self.schema_fields("outreach")[key]
        self.assertFalse(f["readonly"])
        co = Company.objects.create(name="Shared Co", created_by=self.admin, updated_by=self.admin)
        o = Outreach.objects.create(company=co, created_by=self.admin, updated_by=self.admin)
        r = self.client.patch(f"/api/m/outreach/{o.pk}/", {key: "Tata"}, format="json")
        self.assertEqual(r.json()[key], "Tata")
        co.refresh_from_db()
        self.assertEqual(co.custom[key], "Tata")
        self.assertEqual([x["id"] for x in self.client.get(f"/api/m/outreach/?{key}=tat").json()["results"]], [o.pk])

    def test_investor_fields_shown_read_only_on_contacts(self):
        key = self.add("investors", [("Strategy", "text", {})])["Strategy"].key
        lay = self.layout("contacts")
        self.save("contacts", items=[*lay["saved"]["items"], {"field": key, "section": lay["saved"]["sections"][0]["key"]}])
        inv = Investor.objects.create(firm_name="Read Only Fund", custom={key: "Buyout"}, created_by=self.admin, updated_by=self.admin)
        c = Contact.objects.create(full_name="Mira", investor=inv, created_by=self.admin, updated_by=self.admin)
        r = self.client.get(f"/api/m/contacts/{c.pk}/").json()
        self.assertEqual(r[key], "Buyout")
        self.assertTrue(self.schema_fields("contacts")[key]["readonly"])
        self.client.patch(f"/api/m/contacts/{c.pk}/", {key: "Changed"}, format="json")
        inv.refresh_from_db()
        self.assertEqual(inv.custom[key], "Buyout")
        # Switching Investors back to the original layout hides it on contacts too.
        self.client.put("/api/settings/layouts/investors/", {"password": self.password, "customised": False}, format="json")
        self.assertNotIn(key, self.client.get(f"/api/m/contacts/{c.pk}/").json())

    def test_restricted_deal_masked_in_custom_links(self):
        key = self.add("contacts", [("Working on", "link", {"targets": ["deals"]})])["Working on"].key
        co = Company.objects.create(name="Secret Co", created_by=self.admin, updated_by=self.admin)
        deal = Deal.objects.create(company=co, created_by=self.admin, updated_by=self.admin)
        c = self.client.post("/api/m/contacts/", {"full_name": "Zed", key: f"deals:{deal.pk}"}, format="json").json()
        Deal.objects.filter(pk=deal.pk).update(restricted=True)
        outsider = self.make_user("o@firm.com", self.make_role("All", {m: ["view", "edit"] for m in ("contacts", "deals")}))
        r = self.client_for(outsider).get(f"/api/m/contacts/{c['id']}/").json()
        self.assertEqual(r[key]["title"], "Restricted deal")


class LayoutInputTests(CustomBase):
    def test_odd_layout_bodies_are_refused(self):
        for k, v in (("deleted_fields", [{}]), ("restored_fields", [{"a": 1}]), ("sections", 5), ("items", [{}]),
                     ("labels", [1]), ("hidden", [{}]), ("new_fields", [{"id": "n", "label": "A", "type": "nope"}]),
                     ("field_updates", {"cf_999": {"label": "x"}})):
            self.save("investors", expect=400, **{k: v})
