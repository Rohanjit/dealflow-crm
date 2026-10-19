"""Bulk actions on selected records, adding investors to a tracker in one go, and merging duplicates."""

import io

from openpyxl import load_workbook
from rest_framework.test import APIClient

from accounts.models import Role
from contacts.models import Contact
from core.models import Note
from deals.models import Deal, DealInvestor
from investors.models import Investor
from outreach.models import Company, Outreach
from tasks.models import Task
from vault.models import Document

from .base import APITestBase


def tags(obj):
    return sorted(obj.tags.values_list("name", flat=True))


class BulkTestBase(APITestBase):
    def post(self, url, body, client=None):
        return (client or self.client).post(url, body, format="json")

    def investor(self, name, **extra):
        r = self.post("/api/m/investors/", {"firm_name": name, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return Investor.objects.get(pk=r.json()["id"])

    def company(self, name):
        return Company.objects.get(pk=self.post("/api/m/companies/", {"name": name}).json()["id"])

    def deal(self, company):
        r = self.post("/api/m/deals/", {"company": f"companies:{company.pk}", "stage": "data_collection"})
        self.assertEqual(r.status_code, 201, r.content)
        return Deal.objects.get(pk=r.json()["id"])


class BulkEditTests(BulkTestBase):
    def test_set_a_field_on_several_records(self):
        a, b, c = (self.investor(n) for n in ("Alpha Fund", "Beta Fund", "Gamma Fund"))
        pe = self.opt("investor_types", "PE").pk
        r = self.post("/api/m/investors/bulk/", {"ids": [a.pk, b.pk], "action": "edit", "values": {"investor_type": pe}})
        self.assertEqual(r.json(), {"updated": 2, "failed": []})
        self.assertEqual(Investor.objects.filter(investor_type=pe).count(), 2)
        c.refresh_from_db()
        self.assertIsNone(c.investor_type_id)

    def test_add_and_remove_values_in_multi_fields(self):
        a = self.investor("Alpha Fund", tags=["india"])
        b = self.investor("Beta Fund")
        saas = self.opt("sectors", "Technology / SaaS").pk
        r = self.post("/api/m/investors/bulk/", {"ids": [a.pk, b.pk], "action": "edit",
                                                 "values": {"tags": ["priority"], "sectors": [saas]},
                                                 "modes": {"tags": "add", "sectors": "add"}})
        self.assertEqual(r.json()["updated"], 2)
        self.assertEqual(tags(a), ["india", "priority"])
        self.assertEqual(list(b.sectors.values_list("pk", flat=True)), [saas])
        self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "edit", "values": {"tags": ["india"]}, "modes": {"tags": "remove"}})
        self.assertEqual(tags(a), ["priority"])
        self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "edit", "values": {"tags": ["x"]}, "modes": {"tags": "replace"}})
        self.assertEqual(tags(a), ["x"])

    def test_bad_values_are_reported_per_record(self):
        a = self.investor("Alpha Fund")
        r = self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "edit", "values": {"fund_vintage": "not a year"}})
        body = r.json()
        self.assertEqual(body["updated"], 0)
        self.assertEqual(body["failed"][0]["id"], a.pk)
        self.assertIn("fund_vintage", body["failed"][0]["errors"])

    def test_pipeline_stage_and_exit_rules_apply(self):
        co1, co2 = self.company("Acme"), self.company("Zeta")
        o1 = self.post("/api/m/outreach/", {"company": f"companies:{co1.pk}"}).json()["id"]
        o2 = self.post("/api/m/outreach/", {"company": f"companies:{co2.pk}"}).json()["id"]
        r = self.post("/api/m/outreach/bulk/", {"ids": [o1, o2], "action": "edit", "values": {"stage": "meeting"}})
        self.assertEqual(r.json()["updated"], 2)
        self.assertEqual(set(Outreach.objects.values_list("stage", flat=True)), {"meeting"})
        self.assertTrue(Note.objects.filter(module="outreach", record_id=o1, kind="stage").exists())
        # Lost needs a reason, as on the record page.
        r = self.post("/api/m/outreach/bulk/", {"ids": [o1], "action": "edit", "values": {"status": "lost"}})
        self.assertEqual(r.json()["updated"], 0)
        reason = self.opt("lost_reasons", "Fees not agreed").pk
        r = self.post("/api/m/outreach/bulk/", {"ids": [o1, o2], "action": "edit", "values": {"status": "lost", "lost_reason": reason}})
        self.assertEqual(r.json()["updated"], 2)

    def test_delete_moves_to_deleted_items(self):
        a, b = self.investor("Alpha Fund"), self.investor("Beta Fund")
        r = self.post("/api/m/investors/bulk/", {"ids": [a.pk, b.pk], "action": "delete"})
        self.assertEqual(r.json(), {"deleted": 2})
        self.assertEqual(Investor.objects.filter(archived_at__isnull=False).count(), 2)
        # Already deleted records can't be selected again.
        r = self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "delete"})
        self.assertEqual(r.status_code, 400)

    def test_permissions(self):
        a = self.investor("Alpha Fund")
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"investors": ["view"]}))
        member = self.make_user("m@firm.com", Role.objects.get(name="Member"))
        c = self.client_for(viewer)
        self.assertEqual(self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "edit", "values": {"hq_city": "X"}}, c).status_code, 403)
        c = self.client_for(member)  # can edit, can't delete
        self.assertEqual(self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "edit", "values": {"hq_city": "X"}}, c).status_code, 200)
        self.assertEqual(self.post("/api/m/investors/bulk/", {"ids": [a.pk], "action": "delete"}, c).status_code, 403)

    def test_vault_needs_its_pin(self):
        r = self.post("/api/m/vault/bulk/", {"ids": [1], "action": "delete"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "vault_pin_required")

    def test_export_only_the_selection(self):
        a, b, c = (self.investor(n) for n in ("Alpha Fund", "Beta Fund", "Gamma Fund"))
        data = self.client.get(f"/api/m/investors/export/?ids={a.pk},{c.pk}").content
        names = [row[0] for row in load_workbook(io.BytesIO(data)).active.iter_rows(min_row=2, values_only=True)]
        self.assertEqual(sorted(names), ["Alpha Fund", "Gamma Fund"])


class TrackerAddTests(BulkTestBase):
    def test_add_several_investors_to_a_deal(self):
        d = self.deal(self.company("Acme"))
        a, b, c = (self.investor(n) for n in ("Alpha Fund", "Beta Fund", "Gamma Fund"))
        r = self.post(f"/api/deals/{d.pk}/tracker/add/", {"investor_ids": [a.pk, b.pk]})
        self.assertEqual(r.json()["added"], 2)
        r = self.post(f"/api/deals/{d.pk}/tracker/add/", {"investor_ids": [b.pk, c.pk]})
        self.assertEqual((r.json()["added"], r.json()["skipped"]), (1, ["Beta Fund"]))
        self.assertEqual(DealInvestor.objects.filter(deal=d).count(), 3)
        self.assertEqual(set(DealInvestor.objects.values_list("stage", flat=True)), {"identified"})

    def test_needs_permission_on_deals(self):
        d = self.deal(self.company("Acme"))
        a = self.investor("Alpha Fund")
        u = self.make_user("i@firm.com", self.make_role("Investors only", {"investors": ["view", "create", "edit"]}))
        r = self.post(f"/api/deals/{d.pk}/tracker/add/", {"investor_ids": [a.pk]}, self.client_for(u))
        self.assertEqual(r.status_code, 403)


class MergeTests(BulkTestBase):
    def test_merge_investors_moves_everything(self):
        keep = self.investor("Blume Ventures", hq_city="Mumbai")
        dup = self.investor("Blume", hq_city="Bengaluru", tags=["seed"])
        contact = Contact.objects.get(pk=self.post("/api/m/contacts/", {"full_name": "Karan", "firm": f"investors:{dup.pk}"}).json()["id"])
        task = Task.objects.get(pk=self.post("/api/m/tasks/", {"title": "Call Karan", "related": f"investors:{dup.pk}"}).json()["id"])
        d1, d2 = self.deal(self.company("Acme")), self.deal(self.company("Zeta"))
        self.post(f"/api/deals/{d1.pk}/tracker/add/", {"investor_ids": [dup.pk]})
        self.post(f"/api/deals/{d2.pk}/tracker/add/", {"investor_ids": [keep.pk, dup.pk]})
        dup_entry_d2 = DealInvestor.objects.get(deal=d2, investor=dup)
        Note.objects.create(module="deal_investors", record_id=dup_entry_d2.pk, kind="call", date="2026-10-01", text="Spoke to Karan")
        self.client.patch(f"/api/m/deals/{d1.pk}/", {"lead_investors": [f"investors:{dup.pk}"]}, format="json")

        r = self.post("/api/m/investors/merge/", {"keep": keep.pk, "merge": [dup.pk],
                                                  "values": {"hq_city": "Bengaluru", "tags": ["seed"]}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["merged"], 1)
        keep.refresh_from_db()
        dup.refresh_from_db()
        self.assertEqual((keep.hq_city, tags(keep)), ("Bengaluru", ["seed"]))
        self.assertIsNotNone(dup.archived_at)
        contact.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual((contact.investor_id, task.investor_id), (keep.pk, keep.pk))
        # Tracker: moved where the keeper wasn't tracked; folded into the keeper's entry where it was.
        self.assertTrue(DealInvestor.objects.filter(deal=d1, investor=keep, archived_at__isnull=True).exists())
        live_d2 = DealInvestor.objects.filter(deal=d2, archived_at__isnull=True)
        self.assertEqual([e.investor_id for e in live_d2], [keep.pk])
        self.assertTrue(Note.objects.filter(module="deal_investors", record_id=live_d2[0].pk, text="Spoke to Karan").exists())
        self.assertEqual(list(Deal.objects.get(pk=d1.pk).lead_investors.values_list("pk", flat=True)), [keep.pk])
        # The merged-away record can be found in Deleted items.
        self.assertTrue(any(i["id"] == dup.pk for i in self.client.get("/api/archive/").json()["results"]
                            if i["module"] == "investors"))

    def test_merge_companies_moves_outreach_deals_contacts_and_documents(self):
        keep, dup = self.company("Acme Foods"), self.company("Acme Foods Pvt Ltd")
        o = self.post("/api/m/outreach/", {"company": f"companies:{dup.pk}"}).json()["id"]
        d = self.deal(dup)
        c = self.post("/api/m/contacts/", {"full_name": "Ravi", "firm": f"companies:{dup.pk}"}).json()["id"]
        doc = Document.objects.create(title="Teaser", file_name="t.pdf", file_type="PDF", file_size=1,
                                      content_type="application/pdf", sha256="x", file="vault/x.pdf")
        doc.companies.add(dup)
        r = self.post("/api/m/companies/merge/", {"keep": keep.pk, "merge": [dup.pk]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Outreach.objects.get(pk=o).company_id, keep.pk)
        self.assertEqual(Deal.objects.get(pk=d.pk).company_id, keep.pk)
        self.assertEqual(Contact.objects.get(pk=c).company_id, keep.pk)
        self.assertEqual(list(doc.companies.values_list("pk", flat=True)), [keep.pk])

    def test_merge_deals_moves_log_and_tracker(self):
        co = self.company("Acme")
        keep, dup = self.deal(co), self.deal(co)
        inv = self.investor("Alpha Fund")
        self.post(f"/api/deals/{dup.pk}/tracker/add/", {"investor_ids": [inv.pk]})
        Note.objects.create(module="deals", record_id=dup.pk, kind="meeting", date="2026-10-01", text="Kick-off")
        r = self.post("/api/m/deals/merge/", {"keep": keep.pk, "merge": [dup.pk]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(Note.objects.filter(module="deals", record_id=keep.pk, text="Kick-off").exists())
        self.assertTrue(Note.objects.filter(module="deals", record_id=keep.pk, text__startswith="Merged in").exists())
        self.assertEqual(DealInvestor.objects.get(investor=inv).deal_id, keep.pk)

    def test_merge_rules(self):
        a, b = self.investor("Alpha Fund"), self.investor("Alpha")
        self.assertEqual(self.post("/api/m/investors/merge/", {"keep": a.pk, "merge": [a.pk]}).status_code, 400)
        member = self.make_user("m@firm.com", Role.objects.get(name="Member"))  # no delete
        r = self.post("/api/m/investors/merge/", {"keep": a.pk, "merge": [b.pk]}, self.client_for(member))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post("/api/m/deal_investors/merge/", {"keep": 1, "merge": [2]}).status_code, 400)
        r = self.post("/api/m/investors/merge/", {"keep": a.pk, "merge": [b.pk], "values": {"fund_vintage": "nope"}})
        self.assertEqual(r.status_code, 400)
        b.refresh_from_db()
        self.assertIsNone(b.archived_at)  # nothing happened


class DuplicateFinderTests(BulkTestBase):
    def test_groups_look_alike_records(self):
        a = self.investor("Blume Ventures", website="https://blume.vc")
        b = self.investor("Blume")
        c = self.investor("Elevation", website="blume.vc/about")
        self.investor("Peak XV Partners")
        groups = self.client.get("/api/m/investors/duplicates/").json()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual({r["id"] for r in groups[0]["records"]}, {a.pk, b.pk, c.pk})
        self.assertEqual(groups[0]["reasons"], ["Same name", "Same website"])
        schema = {m["key"]: m for m in self.client.get("/api/schema/").json()["modules"]}
        self.assertTrue(schema["investors"]["finds_duplicates"] and schema["investors"]["mergeable"])
        self.assertFalse(schema["deal_investors"]["mergeable"])

    def test_contacts_by_email(self):
        self.post("/api/m/contacts/", {"full_name": "Ravi K", "work_email": "ravi@acme.in"})
        self.post("/api/m/contacts/", {"full_name": "Ravi Kulkarni", "work_email": "RAVI@acme.in"})
        groups = self.client.get("/api/m/contacts/duplicates/").json()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertIn("Same email", groups[0]["reasons"])
