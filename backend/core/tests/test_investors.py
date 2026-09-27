from accounts.models import Role
from core.models import FieldSwitch
from investors.models import Investor

from .base import APITestBase

URL = "/api/m/investors/"


class InvestorCrudTests(APITestBase):
    def payload(self, **kw):
        d = {
            "firm_name": "Blume Ventures",
            "investor_type": self.opt("investor_types", "VC").pk,
            "website": "blume.vc",
            "hq_city": "Mumbai",
            "sectors": [self.opt("sectors", "Fintech").pk, self.opt("sectors", "Consumer / D2C").pk],
            "min_ticket": {"amount": "5", "currency": "INR", "unit": "Cr"},
            "max_ticket": {"amount": "40", "currency": "INR", "unit": "Cr"},
            "actively_deploying": "yes",
            "relationship_owner": self.admin.pk,
            "relationship_strength": "warm",
            "last_contacted": "2026-09-01",
            "tags": ["Top tier", "fintech"],
        }
        d.update(kw)
        return d

    def test_create_read_update(self):
        r = self.client.post(URL, self.payload(), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["website"], "https://blume.vc")
        self.assertEqual(body["min_ticket"], {"amount": "5", "currency": "INR", "unit": "Cr"})
        self.assertEqual(sorted(body["tags"]), ["Top tier", "fintech"])
        self.assertEqual(body["_stamps"]["created_by"]["name"], "Asha Admin")
        inv = Investor.objects.get()
        self.assertEqual(inv.min_ticket_value, 50_000_000)

        other = self.make_user("b@firm.com", self.admin_role, full_name="Bina")
        c = self.client_for(other)
        r = c.patch(f"{URL}{inv.pk}/", {"hq_city": "Bengaluru", "tags": ["fintech"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["hq_city"], "Bengaluru")
        self.assertEqual(r.json()["tags"], ["fintech"])
        self.assertEqual(r.json()["_stamps"]["updated_by"]["name"], "Bina")
        self.assertEqual(r.json()["_stamps"]["created_by"]["name"], "Asha Admin")

    def test_validation(self):
        r = self.client.post(URL, {"hq_city": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("firm_name", r.json())
        r = self.client.post(URL, self.payload(
            min_ticket={"amount": "50", "currency": "INR", "unit": "Cr"},
            max_ticket={"amount": "10", "currency": "INR", "unit": "Cr"},
            website="not a url", last_contacted="31/12/2026",
            min_ticket_bad=1, investor_type=99999,
        ), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual({"max_ticket", "website", "last_contacted", "investor_type"} - set(r.json()), set())
        r = self.client.post(URL, self.payload(fund_size={"amount": "10"}), format="json")
        self.assertIn("fund_size", r.json())

    def test_units_compare_within_currency(self):
        # 50 Lakh < 1 Cr: valid even though 50 > 1
        r = self.client.post(URL, self.payload(min_ticket={"amount": "50", "currency": "INR", "unit": "Lakh"},
                                               max_ticket={"amount": "1", "currency": "INR", "unit": "Cr"}), format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_archive_and_restore(self):
        inv = self.client.post(URL, self.payload(), format="json").json()
        self.assertEqual(self.client.delete(f"{URL}{inv['id']}/").status_code, 204)
        self.assertEqual(self.client.get(URL).json()["count"], 0)
        self.assertEqual(self.client.get(f"{URL}{inv['id']}/").status_code, 404)
        arch = self.client.get("/api/archive/").json()["results"]
        self.assertEqual(arch[0]["title"], "Blume Ventures")
        self.assertEqual(arch[0]["archived_by"], "Asha Admin")
        self.client.post(f"/api/archive/investors/{inv['id']}/")
        self.assertEqual(self.client.get(URL).json()["count"], 1)
        self.client.delete(f"{URL}{inv['id']}/")
        self.assertEqual(self.client.delete(f"/api/archive/investors/{inv['id']}/").status_code, 204)
        self.assertFalse(Investor.objects.exists())

    def test_permissions(self):
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"investors": ["view"]}))
        c = self.client_for(viewer)
        self.assertEqual(c.get(URL).status_code, 200)
        self.assertEqual(c.post(URL, self.payload(), format="json").status_code, 403)
        inv = self.client.post(URL, self.payload(), format="json").json()
        self.assertEqual(c.patch(f"{URL}{inv['id']}/", {"hq_city": "X"}, format="json").status_code, 403)
        self.assertEqual(c.delete(f"{URL}{inv['id']}/").status_code, 403)
        self.assertEqual(c.get("/api/archive/").status_code, 403)

        editor = self.make_user("e@firm.com", Role.objects.get(name="Member"))
        c = self.client_for(editor)
        self.assertEqual(c.patch(f"{URL}{inv['id']}/", {"hq_city": "X"}, format="json").status_code, 200)
        self.assertEqual(c.delete(f"{URL}{inv['id']}/").status_code, 403)

        nobody = self.make_user("n@firm.com", self.make_role("Nobody"))
        c = self.client_for(nobody)
        self.assertEqual(c.get(URL).status_code, 403)
        self.assertEqual(c.get("/api/schema/").json()["modules"], [])
        self.assertEqual(c.get("/api/search/?q=blume").json()["results"], [])

    def test_restore_flag_without_admin_cannot_purge(self):
        arch_role = self.make_role("Archivist", {"investors": ["view", "delete"]}, restore_archive=True)
        c = self.client_for(self.make_user("a@firm.com", arch_role))
        inv = self.client.post(URL, self.payload(), format="json").json()
        c.delete(f"{URL}{inv['id']}/")
        self.assertEqual(c.delete(f"/api/archive/investors/{inv['id']}/").status_code, 403)
        self.assertEqual(c.post(f"/api/archive/investors/{inv['id']}/").status_code, 200)

    def test_filters_search_sort(self):
        self.client.post(URL, self.payload(), format="json")
        self.client.post(URL, self.payload(
            firm_name="Kotak Alt", investor_type=self.opt("investor_types", "PE").pk, hq_city="Mumbai",
            sectors=[self.opt("sectors", "Healthcare").pk],
            min_ticket={"amount": "50", "currency": "INR", "unit": "Cr"},
            max_ticket={"amount": "300", "currency": "INR", "unit": "Cr"},
            relationship_strength="strong", tags=["pe"], relationship_owner=None,
        ), format="json")
        self.client.post(URL, self.payload(
            firm_name="Sequoia US", min_ticket={"amount": "5", "currency": "USD", "unit": "Mn"},
            max_ticket={"amount": "50", "currency": "USD", "unit": "Mn"}, tags=[],
        ), format="json")

        def names(qs):
            return sorted(r["firm_name"] for r in self.client.get(URL + "?" + qs).json()["results"])

        self.assertEqual(names("q=kotak"), ["Kotak Alt"])
        self.assertEqual(names(f"sectors={self.opt('sectors', 'Fintech').pk}"), ["Blume Ventures", "Sequoia US"])
        self.assertEqual(names("relationship_strength=strong"), ["Kotak Alt"])
        self.assertEqual(names("relationship_owner=me"), ["Blume Ventures", "Sequoia US"])
        self.assertEqual(names("relationship_owner=none"), ["Kotak Alt"])
        self.assertEqual(names("tags=PE"), ["Kotak Alt"])
        self.assertEqual(names("ticket=100&ticket_currency=INR&ticket_unit=Cr"), ["Kotak Alt"])
        self.assertEqual(names("ticket=20&ticket_currency=INR&ticket_unit=Cr"), ["Blume Ventures"])
        self.assertEqual(names("ticket=10&ticket_currency=USD&ticket_unit=Mn"), ["Sequoia US"])
        self.assertEqual(names("last_contacted_from=2026-08-01&last_contacted_to=2026-09-30"),
                         ["Blume Ventures", "Kotak Alt", "Sequoia US"])
        order = [r["firm_name"] for r in self.client.get(URL + "?ordering=-min_ticket&ticket_currency=").json()["results"]]
        # grouped by currency (INR before USD), then by scaled amount within it
        self.assertEqual(order, ["Kotak Alt", "Blume Ventures", "Sequoia US"])
        order = [r["firm_name"] for r in self.client.get(URL + "?ordering=investor_type").json()["results"]]
        self.assertEqual(order[0], "Kotak Alt")  # PE < VC

    def test_global_search(self):
        self.client.post(URL, self.payload(), format="json")
        r = self.client.get("/api/search/?q=blu").json()["results"]
        self.assertEqual(r[0]["title"], "Blume Ventures")
        self.assertEqual(r[0]["subtitle"], "VC · Mumbai")


class FieldSwitchAndDropdownTests(APITestBase):
    def test_switched_off_field_is_ignored(self):
        self.client.patch("/api/settings/fields/", {"module": "investors", "field_key": "hq_city", "enabled": False}, format="json")
        self.assertFalse(FieldSwitch.objects.get().enabled)
        r = self.client.post(URL, {"firm_name": "A", "hq_city": "Delhi"}, format="json")
        self.assertEqual(Investor.objects.get().hq_city, "")
        schema = self.client.get("/api/schema/").json()["modules"][0]
        self.assertFalse(next(f for f in schema["fields"] if f["key"] == "hq_city")["enabled"])
        self.assertEqual(r.status_code, 201)

    def test_required_field_cannot_be_switched_off(self):
        r = self.client.patch("/api/settings/fields/", {"module": "investors", "field_key": "firm_name", "enabled": False}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_settings_need_flag(self):
        u = self.make_user("m@firm.com", Role.objects.get(name="Member"))
        c = self.client_for(u)
        self.assertEqual(c.patch("/api/settings/fields/", {"module": "investors", "field_key": "hq_city", "enabled": False}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/choice-lists/sectors/options/", {"label": "Space"}, format="json").status_code, 403)
        self.assertEqual(c.patch("/api/settings/firm/", {"firm_name": "X"}, format="json").status_code, 403)
        self.assertEqual(c.get("/api/settings/firm/").status_code, 200)

    def test_rename_propagates_and_removed_option_stays_on_records(self):
        fin = self.opt("sectors", "Fintech")
        inv = self.client.post(URL, {"firm_name": "A", "sectors": [fin.pk]}, format="json").json()
        self.client.patch(f"/api/choice-options/{fin.pk}/", {"label": "Financial technology"}, format="json")
        schema = self.client.get("/api/schema/").json()
        labels = {o["id"]: o["label"] for o in schema["choice_lists"]["sectors"]["options"]}
        self.assertEqual(labels[fin.pk], "Financial technology")
        # remove (deactivate)
        self.client.patch(f"/api/choice-options/{fin.pk}/", {"is_active": False}, format="json")
        # existing record keeps it, and can still be saved with it
        r = self.client.patch(f"{URL}{inv['id']}/", {"sectors": [fin.pk], "hq_city": "X"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["sectors"], [fin.pk])
        # new records can't pick it
        r = self.client.post(URL, {"firm_name": "B", "sectors": [fin.pk]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_add_option_and_duplicates(self):
        r = self.client.post("/api/choice-lists/sectors/options/", {"label": "  Space  tech "}, format="json")
        self.assertEqual(r.json()["label"], "Space tech")
        r = self.client.post("/api/choice-lists/sectors/options/", {"label": "space TECH"}, format="json")
        self.assertEqual(r.status_code, 400)
