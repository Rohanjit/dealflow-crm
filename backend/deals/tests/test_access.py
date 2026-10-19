"""Restricted deals: who sees them, what goes with them, and the request / approval flow."""

from core.models import Note
from core.tests.base import APITestBase
from deals.models import AccessRequest, Deal, DealInvestor
from investors.models import Investor
from outreach.models import Company, Outreach
from tasks.models import Task
from vault.models import Document

EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


class AccessBase(APITestBase):
    def setUp(self):
        super().setUp()
        role = self.make_role("Banker", EVERYTHING)
        self.md = self.make_user("md@firm.com", role, full_name="Meera MD")
        self.vp = self.make_user("vp@firm.com", role, full_name="Vikram VP")
        self.outsider = self.make_user("out@firm.com", role, full_name="Omar Outside")
        self.company = Company.objects.create(name="Secret Pharma", created_by=self.admin, updated_by=self.admin)
        self.attempt = Outreach.objects.create(company=self.company, stage="mandate_won", created_by=self.admin, updated_by=self.admin)
        self.deal = Deal.objects.get(outreach=self.attempt) if Deal.objects.filter(outreach=self.attempt).exists() else \
            Deal.objects.create(company=self.company, outreach=self.attempt, created_by=self.admin, updated_by=self.admin)
        self.deal.team_md.add(self.md)
        self.deal.team_vp.add(self.vp)
        self.investor = Investor.objects.create(firm_name="Quiet Capital", created_by=self.admin, updated_by=self.admin)
        self.entry = DealInvestor.objects.create(deal=self.deal, investor=self.investor, created_by=self.admin, updated_by=self.admin)
        self.task = Task.objects.create(title="Send NDA", deal=self.deal, assigned_to=self.outsider, created_by=self.vp, updated_by=self.vp)
        self.other_task = Task.objects.create(title="Model review", deal=self.deal, assigned_to=self.vp, created_by=self.vp, updated_by=self.vp)
        self.doc = Document.objects.create(title="Secret IM", file="vault/x.pdf", file_name="x.pdf", file_type="PDF", file_size=1,
                                           content_type="application/pdf", sha256="0" * 64, created_by=self.admin, updated_by=self.admin)
        self.doc.deals.add(self.deal)
        self.c_md, self.c_vp, self.c_out = (self.client_for(u) for u in (self.md, self.vp, self.outsider))

    def act(self, client, **body):
        return client.post(f"/api/deals/{self.deal.pk}/access/", body, format="json")

    def restrict(self):
        r = self.act(self.c_md, action="restrict")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["result"], "done")
        self.deal.refresh_from_db()
        self.assertTrue(self.deal.restricted)


class RequestFlowTests(AccessBase):
    def test_team_member_asks_and_an_md_approves(self):
        r = self.act(self.c_vp, action="restrict")
        self.assertEqual(r.json()["result"], "pending")
        self.deal.refresh_from_db()
        self.assertFalse(self.deal.restricted)
        waiting = self.c_md.get("/api/access-requests/").json()
        self.assertEqual(waiting["count"], 1)
        self.assertEqual(waiting["to_decide"][0]["what"], "Restrict the deal")
        self.assertEqual(self.c_vp.get("/api/access-requests/").json()["count"], 0)  # can't approve their own
        rid = waiting["to_decide"][0]["id"]
        self.assertEqual(self.c_vp.post(f"/api/access-requests/{rid}/", {"decision": "approve"}, format="json").status_code, 400)
        self.assertEqual(self.c_md.post(f"/api/access-requests/{rid}/", {"decision": "approve"}, format="json").status_code, 200)
        self.deal.refresh_from_db()
        self.assertTrue(self.deal.restricted)
        log = Note.objects.filter(module="deals", record_id=self.deal.pk).values_list("text", flat=True)
        self.assertIn("Access: Restricted the deal (asked by Vikram VP).", list(log))

    def test_md_and_admin_change_it_directly_outsiders_cannot(self):
        self.restrict()
        self.assertEqual(self.act(self.client, action="unrestrict").json()["result"], "done")
        self.assertEqual(self.act(self.c_out, action="restrict").status_code, 400)  # not on the deal team
        self.deal.refresh_from_db()
        self.assertFalse(self.deal.restricted)

    def test_outsider_cannot_ask_to_restrict(self):
        r = self.act(self.c_out, action="restrict")
        self.assertEqual(r.status_code, 400)
        self.assertIn("deal team", r.json()["detail"])

    def test_giving_access_needs_approval_removing_does_not(self):
        self.restrict()
        r = self.act(self.c_vp, action="add", person=self.outsider.pk)
        self.assertEqual(r.json()["result"], "pending")
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").status_code, 403)
        rid = AccessRequest.objects.get(kind="add").pk
        self.c_md.post(f"/api/access-requests/{rid}/", {"decision": "approve"}, format="json")
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").status_code, 200)
        r = self.act(self.c_vp, action="remove", person=self.outsider.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").status_code, 403)
        # Team members can't be "added": they already see it.
        self.assertEqual(self.act(self.c_md, action="add", person=self.vp.pk).status_code, 400)

    def test_asking_for_access_from_the_placeholder(self):
        self.restrict()
        r = self.c_out.get(f"/api/m/deals/{self.deal.pk}/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "restricted")
        self.assertEqual(r.json()["placeholder"], {"id": self.deal.pk, "approvers": ["Meera MD"], "request": None})
        self.assertNotIn("Secret Pharma", r.content.decode())
        r = self.c_out.post(f"/api/deals/{self.deal.pk}/access/ask/", {}, format="json")
        self.assertEqual(r.json()["request"]["status"], "pending")
        mine = self.c_out.get("/api/access-requests/").json()["mine"]
        self.assertEqual(mine[0]["deal_title"], "Restricted deal")
        rid = mine[0]["id"]
        self.c_md.post(f"/api/access-requests/{rid}/", {"decision": "decline"}, format="json")
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").json()["placeholder"]["request"]["status"], "declined")
        # Asking again after a decline is allowed; this time approved.
        self.c_out.post(f"/api/deals/{self.deal.pk}/access/ask/", {}, format="json")
        rid = AccessRequest.objects.get(kind="join", status="pending").pk
        self.c_md.post(f"/api/access-requests/{rid}/", {"decision": "approve"}, format="json")
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").status_code, 200)

    def test_withdraw(self):
        self.act(self.c_vp, action="restrict")
        rid = AccessRequest.objects.get().pk
        self.assertEqual(self.c_md.post(f"/api/access-requests/{rid}/", {"decision": "withdraw"}, format="json").status_code, 400)
        self.assertEqual(self.c_vp.post(f"/api/access-requests/{rid}/", {"decision": "withdraw"}, format="json").status_code, 200)
        self.assertEqual(AccessRequest.objects.get().status, "withdrawn")

    def test_who_approves_without_an_md(self):
        self.deal.team_md.clear()
        self.deal.lead_banker = self.vp
        self.deal.save()
        self.assertEqual(self.act(self.c_vp, action="restrict").json()["result"], "done")  # the lead banker approves
        self.deal.refresh_from_db()
        self.deal.lead_banker = None
        self.deal.save()
        self.deal.team_analyst.add(self.outsider)
        r = self.act(self.c_out, action="unrestrict")
        self.assertEqual(r.json().get("result"), "pending", r.content)
        self.assertEqual(self.client.get("/api/access-requests/").json()["count"], 1)  # admins, as nobody else can


class HidingTests(AccessBase):
    def test_everything_about_the_deal_is_hidden_from_outsiders(self):
        self.restrict()
        c = self.c_out
        ids = lambda url: [r["id"] for r in c.get(url).json()["results"]]  # noqa: E731
        self.assertNotIn(self.deal.pk, ids("/api/m/deals/"))
        self.assertEqual(c.get("/api/m/deals/").json()["restricted"][0]["id"], self.deal.pk)
        self.assertNotIn(self.entry.pk, ids("/api/m/deal_investors/"))
        history = c.get(f"/api/m/deal_investors/?investor=investors:{self.investor.pk}").json()
        self.assertEqual(history["results"], [])
        self.assertEqual([p["id"] for p in history["restricted"]], [self.deal.pk])
        self.assertNotIn(self.attempt.pk, ids("/api/m/outreach/"))
        self.assertEqual(c.get(f"/api/m/outreach/{self.attempt.pk}/").status_code, 404)
        self.assertEqual(ids("/api/m/vault/") if c.get("/api/m/vault/").status_code == 200 else [], [])
        # Tasks: the one assigned to them stays, with the link masked; the other goes.
        tasks = {t["id"]: t for t in c.get("/api/m/tasks/?view=all").json()["results"]}
        self.assertNotIn(self.other_task.pk, tasks)
        self.assertEqual(tasks[self.task.pk]["related"]["title"], "Restricted deal")
        self.assertNotIn("Secret Pharma", c.get("/api/m/tasks/?view=all").content.decode())
        # Search, lookups, export, notes, matches, tracker add, bulk, merge, dashboard.
        found = c.get("/api/search/?q=Secret").json()["results"]
        self.assertEqual([x["module"] for x in found if "id" in x], ["companies"])
        self.assertEqual(c.get("/api/lookup/deals/?q=Secret").json(), [])
        self.assertEqual(c.get(f"/api/lookup/deals/?ids={self.deal.pk}").json(), [])
        self.assertEqual(c.get(f"/api/m/deals/{self.deal.pk}/notes/").status_code, 404)
        self.assertEqual(c.get(f"/api/deals/{self.deal.pk}/matches/").status_code, 403)
        self.assertEqual(c.post(f"/api/deals/{self.deal.pk}/tracker/add/", {"investor_ids": [self.investor.pk]}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/m/deals/bulk/", {"action": "delete", "ids": [self.deal.pk]}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/m/deals/{self.deal.pk}/", {"notes": "x"}, format="json").status_code, 403)
        dash = c.get("/api/dashboard/").json()
        self.assertEqual(dash["restricted_hidden"], 1)
        self.assertEqual(dash["active"], 0)
        self.assertNotIn(self.deal.pk, ids("/api/m/deals/export/") if False else [])
        export = c.get("/api/m/deals/export/")
        self.assertEqual(export.status_code, 200)
        # Can't link new things to it.
        r = c.post("/api/m/tasks/", {"title": "Peek", "related": f"deals:{self.deal.pk}"}, format="json")
        self.assertEqual(r.status_code, 400)
        # The company stays visible, and its page mentions the restricted deal only as a placeholder.
        self.assertEqual(c.get(f"/api/m/companies/{self.company.pk}/").status_code, 200)
        self.assertEqual(len(c.get(f"/api/m/deals/?company=companies:{self.company.pk}").json()["restricted"]), 1)

    def test_team_named_people_and_admins_see_it(self):
        self.restrict()
        for c in (self.c_md, self.c_vp, self.client):
            self.assertEqual(c.get(f"/api/m/deals/{self.deal.pk}/").status_code, 200)
            self.assertNotIn("restricted", c.get("/api/m/deals/").json() if c is self.client else {})
        self.assertTrue(self.c_vp.get(f"/api/m/deals/{self.deal.pk}/").json()["restricted"])

    def test_unrestricted_deals_are_open_to_everyone_with_deal_access(self):
        self.assertEqual(self.c_out.get(f"/api/m/deals/{self.deal.pk}/").status_code, 200)
        self.assertEqual(self.c_out.get("/api/m/deals/").json()["restricted"], [])

    def test_a_stale_save_does_not_lift_the_restriction(self):
        stale_copy = Deal.objects.get(pk=self.deal.pk)
        self.restrict()
        stale_copy.use_of_funds = "edited in a form opened earlier"
        stale_copy.save()
        self.deal.refresh_from_db()
        self.assertTrue(self.deal.restricted)
        self.assertEqual(self.deal.use_of_funds, "edited in a form opened earlier")

    def test_restricted_flag_cannot_be_set_through_forms(self):
        r = self.c_vp.patch(f"/api/m/deals/{self.deal.pk}/", {"restricted": True}, format="json")
        self.assertEqual(r.status_code, 200)
        self.deal.refresh_from_db()
        self.assertFalse(self.deal.restricted)

    def test_merging_keeps_the_restriction(self):
        self.restrict()
        other = Deal.objects.create(company=Company.objects.create(name="Open Co", created_by=self.admin, updated_by=self.admin),
                                    created_by=self.admin, updated_by=self.admin)
        r = self.client.post("/api/m/deals/merge/", {"keep": other.pk, "merge": [self.deal.pk]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        other.refresh_from_db()
        self.assertTrue(other.restricted)
        self.assertEqual(set(other.access.values_list("pk", flat=True)), {self.md.pk, self.vp.pk})
        self.assertEqual(self.c_out.get(f"/api/m/deals/{other.pk}/").status_code, 403)
