"""Odd and hostile input: every case here used to crash the server (an error page instead of a
plain message) or store something it shouldn't. Found by sending junk to every endpoint."""

from django.core.files.uploadedfile import SimpleUploadedFile

from investors.models import Investor
from outreach.models import Company

from .base import APITestBase

HUGE = 10**30
HUGE_ID = "99999999999999999999999"


class BodyShapeTests(APITestBase):
    """A JSON list, number or string where an object belongs is refused with a 400 everywhere."""

    def test_non_object_bodies_are_refused(self):
        urls = ["/api/auth/login/", "/api/auth/change-password/", "/api/users/", "/api/roles/", "/api/invites/",
                "/api/m/investors/", "/api/m/investors/bulk/", "/api/m/investors/merge/", "/api/m/investors/import/check/",
                "/api/vault/pin/", "/api/vault/unlock/", "/api/settings/fields/", "/api/settings/firm/"]
        for url in urls:
            for body in ([], [1, 2], "x", 5):
                for method in ("post", "patch"):
                    r = getattr(self.client, method)(url, body, format="json")
                    self.assertIn(r.status_code, (400, 403, 405), f"{method} {url} {body!r}: {r.status_code}")


class RecordValueTests(APITestBase):
    def create(self, **values):
        return self.client.post("/api/m/investors/", {"firm_name": "Edge Fund", **values}, format="json")

    def test_numbers_too_big_for_the_database(self):
        r = self.create(fund_vintage=HUGE)
        self.assertEqual(r.status_code, 400)
        self.assertIn("fund_vintage", r.json())
        r = self.create(fund_size={"amount": "1e400", "currency": "INR", "unit": "Cr"})
        self.assertEqual(r.status_code, 400)
        r = self.create(fund_size={"amount": "1000000000000000", "currency": "INR", "unit": "Cr"})
        self.assertEqual(r.status_code, 400)
        r = self.create(fund_size={"amount": "999999999999999.99", "currency": "USD", "unit": "Bn"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.get(f"/api/m/investors/{r.json()['id']}/").status_code, 200)

    def test_year_fields_take_sensible_years(self):
        self.assertEqual(self.create(fund_vintage=1200).status_code, 400)
        self.assertEqual(self.create(fund_vintage=-5).status_code, 400)
        self.assertEqual(self.create(fund_vintage=2024).status_code, 201)
        r = self.client.post("/api/m/companies/", {"name": "Future Co", "year_founded": 3000}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_tags_must_be_a_list_of_text(self):
        for bad in (5, 1.5, True, {"a": 1}, [{"a": 1}], [[1]]):
            r = self.create(tags=bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.create(tags="india, saas").status_code, 201)

    def test_money_currency_and_unit_must_be_text(self):
        for bad in ({"amount": 1, "currency": 5, "unit": "Cr"}, {"amount": 1, "currency": "INR", "unit": [1]}):
            self.assertEqual(self.create(fund_size=bad).status_code, 400, bad)

    def test_link_with_odd_module_or_id(self):
        for bad in ({"module": [], "id": 1}, {"module": "investors", "id": HUGE}, {"module": {}, "id": 1},
                    f"investors:{HUGE_ID}", "investors:-1", {"module": "investors", "id": True}):
            r = self.client.post("/api/m/contacts/", {"full_name": "Odd Link", "firm": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_dropdown_and_user_ids_out_of_range(self):
        self.assertEqual(self.create(investor_type=HUGE).status_code, 400)
        self.assertEqual(self.create(relationship_owner=HUGE).status_code, 400)
        self.assertEqual(self.create(investor_type=True).status_code, 400)

    def test_nul_characters_are_dropped_from_text(self):
        r = self.create(firm_name="Nul\x00 Capital", notes="a\x00b")
        self.assertEqual(r.status_code, 201)
        inv = Investor.objects.get(pk=r.json()["id"])
        self.assertEqual(inv.firm_name, "Nul Capital")

    def test_very_long_text(self):
        self.assertEqual(self.create(firm_name="x" * 300).status_code, 400)
        self.assertEqual(self.create(notes="x" * 200_001).status_code, 400)
        self.assertEqual(self.create(website="https://" + "a" * 300 + ".com").status_code, 400)
        self.assertEqual(self.create(firm_name=["a list"]).status_code, 400)


class FilterTests(APITestBase):
    def test_huge_ids_in_filters_and_lookups(self):
        for url in (f"/api/m/investors/?ids={HUGE_ID}", f"/api/lookup/investors/?ids={HUGE_ID}",
                    f"/api/m/investors/export/?ids={HUGE_ID}", f"/api/m/investors/?investor_type={HUGE_ID}",
                    f"/api/m/contacts/?firm=investors:{HUGE_ID}", f"/api/m/tasks/?assigned_to={HUGE_ID}",
                    "/api/m/investors/?ids=²", "/api/lookup/investors/?ids=²,3"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_huge_amounts_in_range_filters(self):
        for q in ("fund_size_min=1e400", "fund_size_max=99999999999999999999999999999&fund_size_unit=Bn",
                  "fund_vintage_min=99999999999999999999"):
            self.assertEqual(self.client.get(f"/api/m/investors/?{q}").status_code, 200, q)


class NoteTests(APITestBase):
    def test_odd_note_fields(self):
        company = Company.objects.create(name="Note Co", created_by=self.admin, updated_by=self.admin)
        r = self.client.post("/api/m/outreach/", {"company": f"companies:{company.pk}"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        url = f"/api/m/outreach/{r.json()['id']}/notes/"
        for body in ({"kind": [1], "text": "x"}, {"kind": {"a": 1}, "text": "x"}, {"kind": "note", "text": ["x"]},
                     {"kind": "note", "text": {"a": 1}}):
            r = self.client.post(url, body, format="json")
            self.assertEqual(r.status_code, 400, body)


class BulkMergeInputTests(APITestBase):
    def test_merge_with_odd_lists(self):
        a = Investor.objects.create(firm_name="A Fund", created_by=self.admin, updated_by=self.admin)
        for body in ({"keep": a.pk, "merge": 5}, {"keep": a.pk, "merge": True}, {"keep": a.pk, "merge": "x"},
                     {"keep": True, "merge": [a.pk]}, {"keep": a.pk, "merge": [HUGE]}, {"keep": a.pk, "merge": [[1]]}):
            r = self.client.post("/api/m/investors/merge/", body, format="json")
            self.assertEqual(r.status_code, 400, body)

    def test_bulk_with_odd_ids(self):
        for ids in ([HUGE], [[1]], [{"a": 1}], ["²"], [True]):
            r = self.client.post("/api/m/investors/bulk/", {"action": "delete", "ids": ids}, format="json")
            self.assertEqual(r.status_code, 400, ids)


class ImportInputTests(APITestBase):
    def upload(self, content: bytes, name="a.csv"):
        f = SimpleUploadedFile(name, content, content_type="text/csv")
        return self.client.post("/api/m/investors/import/upload/", {"file": f}, format="multipart")

    def test_unreadable_csv(self):
        r = self.upload(b"Firm name\n" + b"x" * 200_000 + b"\n")
        self.assertEqual(r.status_code, 400)
        r = self.upload(b'Firm name,Notes\n"Alpha\x00",x\n')
        self.assertEqual(r.status_code, 200)

    def test_odd_mappings_tokens_and_rows(self):
        r = self.upload(b"Firm name,Junk\nAlpha,1\nBeta,2\n")
        token, good = r.json()["token"], r.json()["mapping"]
        for mapping in ([5, None], ["x", None], [{"field": 5}, None], [{"field": [1]}, None], [{"field": {"a": 1}}, None],
                        [{"field": "firm_name", "currency": 5}, None]):
            for step in ("check", "commit"):
                r = self.client.post(f"/api/m/investors/import/{step}/", {"token": token, "mapping": mapping, "include_rows": [2]},
                                     format="json")
                self.assertEqual(r.status_code, 400, mapping)
        for tok in (5, [], None, {"a": 1}):
            r = self.client.post("/api/m/investors/import/check/", {"token": tok, "mapping": good}, format="json")
            self.assertEqual(r.status_code, 400, tok)
        r = self.client.post("/api/m/investors/import/commit/", {"token": token, "mapping": good, "include_rows": ["²", HUGE, 2]},
                             format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["created"], 1)


class SettingsInputTests(APITestBase):
    def test_users_with_odd_roles_names_and_switches(self):
        for role in ("", "\x00", {}, [], HUGE, "😀"):
            r = self.client.post("/api/users/", {"full_name": "X", "email": "x@y.com", "role": role}, format="json")
            self.assertEqual(r.status_code, 400, role)
        member = self.make_user("m@firm.com", self.make_role("Odd"))
        self.assertEqual(self.client.patch(f"/api/users/{member.pk}/", {"role": {}}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"/api/users/{member.pk}/", {"full_name": "y" * 200}, format="json").status_code, 400)
        r = self.client.post("/api/users/", {"full_name": "z" * 200, "email": "z@y.com", "role": self.admin_role.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/users/", {"full_name": "Z", "email": "a" * 250 + "@y.com", "role": self.admin_role.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        # "false" as text doesn't count as yes.
        r = self.client.patch(f"/api/users/{member.pk}/", {"is_active": "false"}, format="json")
        self.assertEqual(r.status_code, 200)
        member.refresh_from_db()
        self.assertFalse(member.is_active)
        self.assertEqual(self.client.patch(f"/api/users/{member.pk}/", {"is_active": "maybe"}, format="json").status_code, 400)

    def test_roles_with_odd_permissions(self):
        role = self.make_role("Odd")
        for body in ({"flags": [1]}, {"flags": True}, {"perms": 5}, {"perms": {"investors": 5}}, {"perms": {"investors": [1]}},
                     {"flags": {"manage_users": "maybe"}}):
            r = self.client.patch(f"/api/roles/{role.pk}/", body, format="json")
            self.assertEqual(r.status_code, 400, body)

    def test_preferences_and_dropdowns(self):
        self.assertEqual(self.client.patch("/api/auth/me/", {"date_format": ["x"]}, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/auth/me/", {"full_name": "n" * 200}, format="json").status_code, 400)
        for ids in (5, True, ["a", 1], [HUGE]):
            r = self.client.post("/api/choice-lists/sectors/reorder/", {"ids": ids}, format="json")
            self.assertEqual(r.status_code, 400, ids)
        opt = self.opt("sectors", "Technology / SaaS")
        self.assertEqual(self.client.patch(f"/api/choice-options/{opt.pk}/", {"is_active": "false"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/choice-lists/sectors/options/", {"label": {"a": 1}}, format="json").status_code, 400)

    def test_field_switch_input(self):
        self.assertEqual(self.client.patch("/api/settings/fields/", {"module": ["investors"], "field_key": "notes", "enabled": False},
                                           format="json").status_code, 400)
        r = self.client.patch("/api/settings/fields/", {"module": "investors", "field_key": "notes", "enabled": "false"}, format="json")
        self.assertEqual(r.status_code, 400)


class QueryAndColumnTests(APITestBase):
    def test_nul_in_the_address_is_ignored(self):
        # Postgres refuses NUL in text comparisons; the address is cleaned before any view sees it.
        for url in ("/api/m/investors/?q=a%00b", "/api/m/investors/?hq_city=%00", "/api/lookup/investors/?q=%00",
                    "/api/search/?q=ab%00", "/api/m/investors/export/?q=%00", "/api/archive/?q=%00"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_team_and_about_filters_with_huge_ids(self):
        for url in (f"/api/m/deals/?team={HUGE_ID}", "/api/m/deals/?team=²", f"/api/m/vault/?about=deals:{HUGE_ID}"):
            r = self.client.get(url)
            self.assertIn(r.status_code, (200, 403), url)  # the Vault answers 403 until its PIN is entered

    def test_text_fields_fit_their_columns(self):
        """A text box can't take more than the database column holds (Postgres refuses the save)."""
        from core.registry import all_modules, get_module

        for m in all_modules():
            for f in m.fields:
                if f.type not in ("text", "phone", "url", "email") or f.readonly:
                    continue
                model = get_module(f.via_module).model if f.via else m.model
                column = model._meta.get_field(f.key)
                self.assertLessEqual(f.max_length, column.max_length, f"{m.key}.{f.key}")
        r = self.client.post("/api/m/companies/", {"name": "FY Co", "financials_fy": "F" * 25}, format="json")
        self.assertEqual(r.status_code, 400)


class BulkFieldTests(APITestBase):
    def test_bulk_edit_of_fields_that_cant_change_is_refused(self):
        from core.views import set_field_hidden

        ids = [Investor.objects.create(firm_name=n, created_by=self.admin, updated_by=self.admin).pk for n in ("P Fund", "Q Fund")]
        set_field_hidden("investors", "hq_city", True)
        for values in ({"hq_city": "Goa"}, {"nope": 1}, {"created_by": self.admin.pk}):
            r = self.client.post("/api/m/investors/bulk/", {"action": "edit", "ids": ids, "values": values}, format="json")
            self.assertEqual(r.status_code, 400, values)
        self.assertFalse(Investor.objects.filter(hq_city="Goa").exists())
