import datetime as dt
import importlib
import io

from django.apps import apps
from django.utils import timezone
from openpyxl import load_workbook

from core.models import FirmSettings, Note
from core.tests.base import APITestBase
from deals.models import Deal, DealInvestor
from investors.models import Investor
from outreach.models import Company, Outreach

C_URL, O_URL, D_URL, T_URL = "/api/m/companies/", "/api/m/outreach/", "/api/m/deals/", "/api/m/deal_investors/"


class DealBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.acme = self.client.post(C_URL, {"name": "Acme Foods", "sector": self.opt("sectors", "Consumer / D2C").pk},
                                     format="json").json()

    def post(self, url, data, status=201):
        r = self.client.post(url, data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def patch(self, url, data, status=200, client=None):
        r = (client or self.client).patch(url, data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def deal(self, **kw):
        return self.post(D_URL, {"company": f"companies:{self.acme['id']}", **kw})

    def investor(self, name, **kw):
        return self.post("/api/m/investors/", {"firm_name": name, **kw})

    def notes(self, module, pk):
        return [n["text"] for n in self.client.get(f"/api/m/{module}/{pk}/notes/").json()]


class MandateWonTests(DealBase):
    def outreach(self, **kw):
        return self.post(O_URL, {
            "company": f"companies:{self.acme['id']}",
            "transaction_type": self.opt("transaction_types", "Equity raise").pk,
            "expected_size": {"amount": "150", "currency": "INR", "unit": "Cr"},
            "proposed_retainer": {"amount": "25", "currency": "INR", "unit": "Lakh"},
            "proposed_success_fee": "2.5",
            "final_agreed_fees": "₹25 L retainer + 2.5%",
            "lead_banker": self.admin.pk,
            "tags": ["priority"],
            **kw,
        })

    def test_won_mandate_becomes_a_deal(self):
        o = self.outreach(stage="negotiating")
        self.assertFalse(Deal.objects.exists())
        self.patch(f"{O_URL}{o['id']}/", {"stage": "won"})
        deal = Deal.objects.get()
        d = self.client.get(f"{D_URL}{deal.pk}/").json()
        self.assertEqual(d["_title"], "Acme Foods")
        self.assertEqual((d["stage"], d["status"]), ("data_collection", "open"))
        self.assertEqual(d["outreach"]["id"], o["id"])
        self.assertEqual(d["target_raise"], {"amount": "150", "currency": "INR", "unit": "Cr"})
        self.assertEqual(d["agreed_retainer"], {"amount": "25", "currency": "INR", "unit": "Lakh"})
        self.assertEqual((d["success_fee_pct"], d["agreed_fee_terms"]), ("2.5", "₹25 L retainer + 2.5%"))
        self.assertEqual((d["lead_banker"], d["tags"]), (self.admin.pk, ["priority"]))
        self.assertEqual(d["transaction_type"], self.opt("transaction_types", "Equity raise").pk)
        self.assertEqual(d["mandate_date"], timezone.localdate().isoformat())
        self.assertEqual(d["sector"], self.opt("sectors", "Consumer / D2C").pk)  # company details are shared
        self.assertEqual(self.notes("deals", deal.pk), ["Created from outreach: mandate won"])
        self.assertEqual(self.notes("outreach", o["id"])[0], "Moved to Active Deals")
        # the outreach page finds its deal
        found = self.client.get(f"{D_URL}?outreach=outreach:{o['id']}&view=all").json()["results"]
        self.assertEqual([r["id"] for r in found], [deal.pk])

    def test_only_one_deal_per_won_attempt(self):
        o = self.outreach(stage="won")
        self.assertEqual(Deal.objects.count(), 1)
        self.patch(f"{O_URL}{o['id']}/", {"stage": "negotiating"})
        self.patch(f"{O_URL}{o['id']}/", {"stage": "won"})
        self.assertEqual(Deal.objects.count(), 1)
        # an archived deal isn't recreated either (restore it from the Archive)
        self.client.delete(f"{D_URL}{Deal.objects.get().pk}/")
        self.patch(f"{O_URL}{o['id']}/", {"stage": "negotiating"})
        self.patch(f"{O_URL}{o['id']}/", {"stage": "won"})
        self.assertEqual(Deal.objects.count(), 1)

    def test_readonly_outreach_link(self):
        o = self.outreach(stage="won")
        other = self.outreach(stage="contacted")
        deal = Deal.objects.get()
        d = self.patch(f"{D_URL}{deal.pk}/", {"outreach": f"outreach:{other['id']}", "engagement_letter_ref": "EL/26/14"})
        self.assertEqual((d["outreach"]["id"], d["engagement_letter_ref"]), (o["id"], "EL/26/14"))

    def test_imported_won_rows_become_deals(self):
        from openpyxl import Workbook
        from django.core.files.uploadedfile import SimpleUploadedFile

        wb = Workbook()
        for row in [["Company", "Stage", "Deal size"], ["Nova Retail", "Mandate won", "₹ 80 Cr"],
                    ["Orbit Labs", "Meeting held", ""]]:
            wb.active.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        up = self.client.post("/api/m/outreach/import/upload/", {"file": SimpleUploadedFile("p.xlsx", buf.getvalue())})
        body = {"token": up.json()["token"], "mapping": up.json()["mapping"], "create_links": {"company": "companies"},
                "include_rows": [2, 3]}
        r = self.post("/api/m/outreach/import/commit/", body, status=200)
        self.assertEqual(r["created"], 2)
        deal = Deal.objects.get()
        self.assertEqual((deal.company.name, deal.stage, deal.target_raise_amount), ("Nova Retail", "data_collection", 80))

    def test_existing_won_mandates_converted_by_migration(self):
        o = self.outreach(stage="won")
        Deal.objects.all().delete()
        Note.objects.filter(module="deals").delete()
        archived = self.outreach(stage="won")
        Deal.objects.filter(outreach_id=archived["id"]).delete()
        Outreach.objects.filter(pk=archived["id"]).update(archived_at=timezone.now())
        Outreach.objects.filter(pk=o["id"]).update(stage_entered=dt.date(2026, 3, 1))
        migration = importlib.import_module("deals.migrations.0003_deals_for_won_mandates")
        migration.convert(apps, None)
        deal = Deal.objects.get()
        self.assertEqual((deal.outreach_id, deal.stage, deal.mandate_date), (o["id"], "data_collection", dt.date(2026, 3, 1)))
        self.assertEqual(deal.target_raise_value, 150 * 10**7)
        self.assertEqual(list(deal.tags.values_list("name", flat=True)), ["priority"])
        self.assertEqual(self.notes("deals", deal.pk), ["Created from outreach: mandate won"])


class DealPipelineTests(DealBase):
    def test_new_deal_and_phases(self):
        d = self.deal()
        self.assertEqual((d["stage"], d["status"], d["stage_entered"]), ("data_collection", "open", timezone.localdate().isoformat()))
        schema = next(m for m in self.client.get("/api/schema/").json()["modules"] if m["key"] == "deals")
        p = schema["pipeline"]
        self.assertEqual([g["label"] for g in p["groups"]], ["Preparation", "Execution"])
        self.assertEqual(len(p["stages"]), 12)
        self.assertEqual(p["won"], "closed")
        self.assertEqual([(e["value"], e["action"], e["paused"]) for e in p["exits"]],
                         [("on_hold", "Put on hold", True), ("withdrawn", "Company withdrew", False),
                          ("dropped", "Drop the deal", False)])
        self.assertFalse(schema["importable"])
        self.assertTrue(next(f for f in schema["fields"] if f["key"] == "outreach")["readonly"])

    def test_exits_need_no_reason_and_keep_the_stage(self):
        d = self.deal(stage="term_sheet")
        url = f"{D_URL}{d['id']}/"
        r = self.patch(url, {"status": "withdrawn"})
        self.assertEqual((r["stage"], r["status"]), ("term_sheet", "withdrawn"))
        self.assertEqual(self.notes("deals", d["id"])[0], "Withdrawn by company at Term sheet")
        r = self.patch(url, {"status": "open"})
        self.assertEqual(self.notes("deals", d["id"])[0], "Reopened at Term sheet")
        self.patch(url, {"status": "on_hold"})
        self.assertEqual(self.notes("deals", d["id"])[0], "Put on hold at Term sheet")
        self.patch(url, {"status": "open"})
        self.assertEqual(self.notes("deals", d["id"])[0], "Resumed at Term sheet")
        self.patch(url, {"stage": "closed"})
        self.assertEqual(self.notes("deals", d["id"])[0], "Closed (from Term sheet)")
        r = self.client.patch(url, {"status": "dropped"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.json())

    def test_views(self):
        a = self.deal(stage="due_diligence")
        b = self.deal(stage="closed")
        c = self.deal()
        self.patch(f"{D_URL}{c['id']}/", {"status": "dropped"})
        e = self.deal()
        self.patch(f"{D_URL}{e['id']}/", {"status": "on_hold"})
        ids = lambda v: {r["id"] for r in self.client.get(f"{D_URL}?view={v}").json()["results"]}  # noqa: E731
        self.assertEqual(ids("active"), {a["id"]})
        self.assertEqual(ids("closed"), {b["id"]})
        self.assertEqual(ids("ended"), {c["id"]})
        self.assertEqual(ids("on_hold"), {e["id"]})
        self.assertEqual(ids("all"), {a["id"], b["id"], c["id"], e["id"]})

    def test_company_page_lists_deals_and_company_cant_be_purged(self):
        d = self.deal()
        rows = self.client.get(f"{D_URL}?company=companies:{self.acme['id']}&view=all").json()["results"]
        self.assertEqual([r["id"] for r in rows], [d["id"]])
        self.client.delete(f"{C_URL}{self.acme['id']}/")
        r = self.client.delete(f"/api/archive/companies/{self.acme['id']}/")
        self.assertEqual(r.status_code, 400)

    def test_search_finds_deals_by_company(self):
        d = self.deal()
        results = self.client.get("/api/search/?q=acme").json()["results"]
        self.assertIn(("deals", d["id"]), {(r["module"], r.get("id")) for r in results})

    def test_import_is_not_offered(self):
        r = self.client.get(f"{D_URL}import/upload/")
        self.assertIn(r.status_code, (404, 405))
        from django.core.files.uploadedfile import SimpleUploadedFile

        r = self.client.post(f"{D_URL}import/upload/", {"file": SimpleUploadedFile("d.csv", b"Company\nAcme\n")})
        self.assertEqual(r.status_code, 404)


class DealTeamTests(DealBase):
    def setUp(self):
        super().setUp()
        role = self.make_role("Bankers", {"deals": ["view", "create", "edit"]})
        self.vp = self.make_user("vp@firm.com", role, full_name="Vikram VP")
        self.an = self.make_user("an@firm.com", role, full_name="Anu Analyst")

    def test_team_members(self):
        d = self.deal(lead_banker=self.admin.pk, team_vp=[self.vp.pk], team_analyst=[self.an.pk, self.vp.pk])
        self.assertEqual(d["team_vp"], [self.vp.pk])
        self.assertEqual(sorted(d["team_analyst"]), sorted([self.an.pk, self.vp.pk]))
        other = self.deal()
        vp_client = self.client_for(self.vp)
        mine = lambda qs: [r["id"] for r in vp_client.get(f"{D_URL}?{qs}").json()["results"]]  # noqa: E731
        self.assertEqual(mine("team_vp=me"), [d["id"]])
        self.assertEqual(mine("team=me"), [d["id"]])
        self.assertEqual(mine("team_md=none"), [other["id"], d["id"]])
        self.assertEqual(mine(f"team_analyst={self.an.pk},{self.vp.pk}"), [d["id"]])  # once, not twice
        # a deactivated person stays on the team but can't be newly added
        self.an.is_active = False
        self.an.save()
        self.patch(f"{D_URL}{d['id']}/", {"team_analyst": [self.an.pk]})
        r = self.client.patch(f"{D_URL}{other['id']}/", {"team_analyst": [self.an.pk]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("deactivated", r.json()["team_analyst"])

    def test_lead_investors(self):
        northwind = self.investor("Northwind Ventures")
        arclight = self.investor("Arclight")
        d = self.deal(lead_investors=[f"investors:{northwind['id']}", {"module": "investors", "id": arclight["id"]}])
        self.assertEqual([i["title"] for i in d["lead_investors"]], ["Arclight", "Northwind Ventures"])
        rows = self.client.get(f"{D_URL}?lead_investors=investors:{northwind['id']},investors:{arclight['id']}&view=all").json()
        self.assertEqual(rows["count"], 1)
        # archived investors stay linked but can't be newly added
        self.client.delete(f"/api/m/investors/{arclight['id']}/")
        r = self.patch(f"{D_URL}{d['id']}/", {"lead_investors": [f"investors:{arclight['id']}", f"investors:{northwind['id']}"]})
        self.assertTrue(next(i for i in r["lead_investors"] if i["id"] == arclight["id"])["archived"])
        other = self.deal()
        r = self.client.patch(f"{D_URL}{other['id']}/", {"lead_investors": [f"investors:{arclight['id']}"]}, format="json")
        self.assertEqual(r.status_code, 400)
        # export writes the names in one cell and the team as names
        self.patch(f"{D_URL}{d['id']}/", {"team_vp": [self.vp.pk]})
        wb = load_workbook(io.BytesIO(self.client.get(f"{D_URL}export/?view=all").content))
        header = [c.value for c in wb.active[1]]
        row = next(r for r in wb.active.iter_rows(min_row=2, values_only=True) if r[header.index("Lead investor(s)")])
        self.assertEqual(row[header.index("Lead investor(s)")], "Arclight, Northwind Ventures")
        self.assertEqual(row[header.index("VP")], "Vikram VP")


class TrackerTests(DealBase):
    def setUp(self):
        super().setUp()
        self.d = self.deal(stage="investor_outreach")
        self.northwind = self.investor("Northwind Ventures")

    def track(self, inv, **kw):
        return self.post(T_URL, {"deal": f"deals:{self.d['id']}", "investor": f"investors:{inv['id']}", **kw})

    def test_add_move_decline(self):
        t = self.track(self.northwind)
        self.assertEqual((t["_title"], t["stage"], t["status"]), ("Northwind Ventures", "identified", "open"))
        r = self.client.post(T_URL, {"deal": f"deals:{self.d['id']}", "investor": f"investors:{self.northwind['id']}"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already", r.json()["investor"])
        url = f"{T_URL}{t['id']}/"
        self.patch(url, {"stage": "nda_signed"})
        r = self.client.patch(url, {"status": "declined"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("decline_reason", r.json())
        r = self.patch(url, {"status": "declined", "decline_reason": self.opt("decline_reasons", "Valuation mismatch").pk})
        self.assertEqual((r["stage"], r["status"]), ("nda_signed", "declined"))
        self.assertEqual(self.notes("deal_investors", t["id"])[0], "Declined at NDA signed · Valuation mismatch")
        arclight = self.investor("Arclight")
        t2 = self.track(arclight, stage="teaser_sent")
        self.patch(f"{T_URL}{t2['id']}/", {"status": "no_response"})
        self.assertEqual(self.notes("deal_investors", t2["id"])[0], "No response at Teaser sent")
        rows = self.client.get(f"{T_URL}?deal=deals:{self.d['id']}").json()["results"]
        self.assertEqual({r["_title"] for r in rows}, {"Northwind Ventures", "Arclight"})

    def test_investor_deal_history(self):
        t = self.track(self.northwind, stage="meeting_held")
        other = self.deal()
        self.post(T_URL, {"deal": f"deals:{other['id']}", "investor": f"investors:{self.northwind['id']}"})
        history = self.client.get(f"{T_URL}?investor=investors:{self.northwind['id']}").json()["results"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["deal"]["title"], "Acme Foods")
        # entries of archived deals drop out of the history, and the tracker isn't in global search
        self.client.delete(f"{D_URL}{other['id']}/")
        history = self.client.get(f"{T_URL}?investor=investors:{self.northwind['id']}").json()["results"]
        self.assertEqual([h["id"] for h in history], [t["id"]])
        modules = {r["module"] for r in self.client.get("/api/search/?q=northwind").json()["results"]}
        self.assertEqual(modules, {"investors"})
        schema = self.client.get("/api/schema/").json()
        inv = next(m for m in schema["modules"] if m["key"] == "investors")
        self.assertEqual({s["key"]: s["status"] for s in inv["sections"]},
                         {"contacts": "ready", "tasks": "ready", "documents": "ready", "deal_history": "ready"})

    def test_permissions_follow_active_deals(self):
        viewer = self.make_user("v@firm.com", self.make_role("Viewers", {"deals": ["view"], "investors": ["view"]}))
        c = self.client_for(viewer)
        self.assertEqual(c.get(f"{T_URL}?deal=deals:{self.d['id']}").status_code, 200)
        r = c.post(T_URL, {"deal": f"deals:{self.d['id']}", "investor": f"investors:{self.northwind['id']}"}, format="json")
        self.assertEqual(r.status_code, 403)
        outsider = self.make_user("o@firm.com", self.make_role("Investor desk", {"investors": ["view", "edit"]}))
        self.assertEqual(self.client_for(outsider).get(T_URL).status_code, 403)
        schema = self.client_for(outsider).get("/api/schema/").json()
        inv = next(m for m in schema["modules"] if m["key"] == "investors")
        self.assertEqual(next(s for s in inv["sections"] if s["key"] == "deal_history")["status"], "hidden")
        # people who can add to trackers can find investor names for the picker
        editor = self.make_user("e@firm.com", self.make_role("Deal team", {"deals": ["view", "create", "edit"]}))
        names = self.client_for(editor).get("/api/lookup/investors/?q=blu").json()
        self.assertEqual([n["title"] for n in names], ["Northwind Ventures"])

    def test_purging_a_deal_removes_its_tracker(self):
        t = self.track(self.northwind)
        self.assertTrue(Note.objects.filter(module="deal_investors", record_id=t["id"]).exists())
        self.client.delete(f"{D_URL}{self.d['id']}/")
        r = self.client.delete(f"/api/archive/deals/{self.d['id']}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(DealInvestor.objects.exists())
        self.assertFalse(Note.objects.filter(module__in=["deals", "deal_investors"]).exists())

    def test_investor_on_a_tracker_cant_be_purged(self):
        self.track(self.northwind)
        self.client.delete(f"/api/m/investors/{self.northwind['id']}/")
        r = self.client.delete(f"/api/archive/investors/{self.northwind['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("deal tracker entries", r.json()["detail"])


class MatchingTests(DealBase):
    def setUp(self):
        super().setUp()
        self.patch(f"{C_URL}{self.acme['id']}/", {"geography": self.opt("geographies", "India").pk})
        self.d = self.deal(target_raise={"amount": "100", "currency": "INR", "unit": "Cr"}, instrument="ccps",
                           investment_stage=self.opt("investment_stages", "Growth").pk)

    def inv(self, name, **kw):
        return self.investor(name, **kw)

    def matches(self, **params):
        r = self.client.get(f"/api/deals/{self.d['id']}/matches/", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_ranking_and_reasons(self):
        consumer, growth, india = (self.opt("sectors", "Consumer / D2C").pk, self.opt("investment_stages", "Growth").pk,
                                   self.opt("geographies", "India").pk)
        self.inv("Perfect Capital", sectors=[consumer], stages=[growth], geography_focus=[india], instrument="equity",
                 actively_deploying="yes", min_ticket={"amount": "10", "currency": "INR", "unit": "Cr"},
                 max_ticket={"amount": "40", "currency": "INR", "unit": "Cr"})
        self.inv("Blank Partners")
        self.inv("Debt Fund", sectors=[consumer], instrument="debt", actively_deploying="no",
                 geography_focus=[self.opt("geographies", "US").pk],
                 min_ticket={"amount": "500", "currency": "INR", "unit": "Cr"})
        self.inv("Global Dollar", geography_focus=[self.opt("geographies", "Global").pk], instrument="both",
                 min_ticket={"amount": "5", "currency": "USD", "unit": "Mn"})
        body = self.matches()
        self.assertEqual(body["facts"], {"sector": "Consumer / D2C", "stage": "Growth", "geography": "India",
                                         "raise": "₹100 Cr", "kind": "equity"})
        ranked = [(r["investor"]["title"], r["score"]) for r in body["results"]]
        self.assertEqual(ranked[0], ("Perfect Capital", 100))
        self.assertEqual(ranked[-1][0], "Debt Fund")
        by_name = {r["investor"]["title"]: {x["factor"]: x for x in r["reasons"]} for r in body["results"]}
        self.assertEqual(by_name["Blank Partners"]["sector"]["fit"], "unknown")
        self.assertEqual(by_name["Debt Fund"]["ticket"]["text"], "Minimum ticket ₹500 Cr is above the raise")
        self.assertEqual(by_name["Debt Fund"]["instrument"]["fit"], "no")
        self.assertEqual(by_name["Global Dollar"]["geography"]["text"], "Invests globally")
        self.assertEqual(by_name["Global Dollar"]["ticket"]["text"], "Tickets in USD, raise in INR")
        self.assertEqual(sum(x["points"] for x in by_name["Blank Partners"].values()), 10 + 8 + 6 + 6 + 6 + 5)

    def test_leaves_out_tracked_and_archived_investors(self):
        a = self.inv("Alpha")
        b = self.inv("Beta")
        self.inv("Gamma")
        self.post(T_URL, {"deal": f"deals:{self.d['id']}", "investor": f"investors:{a['id']}"})
        self.client.delete(f"/api/m/investors/{b['id']}/")
        self.assertEqual([r["investor"]["title"] for r in self.matches()["results"]], ["Gamma"])
        self.assertEqual(self.matches(q="gam")["results"][0]["investor"]["title"], "Gamma")

    def test_needs_investor_access(self):
        user = self.make_user("d@firm.com", self.make_role("Deals only", {"deals": ["view"]}))
        r = self.client_for(user).get(f"/api/deals/{self.d['id']}/matches/")
        self.assertEqual(r.status_code, 403)


class DashboardTests(DealBase):
    def test_snapshot(self):
        today = timezone.localdate()
        soon = self.deal(stage="term_sheet", target_close_date=(today + dt.timedelta(days=10)).isoformat(),
                         mandate_expiry=(today + dt.timedelta(days=40)).isoformat())
        late = self.deal(stage="due_diligence", target_close_date=(today - dt.timedelta(days=3)).isoformat(),
                         mandate_expiry=(today + dt.timedelta(days=5)).isoformat())
        self.deal(target_close_date=(today + dt.timedelta(days=90)).isoformat())
        done = self.deal(stage="closed", target_close_date=today.isoformat())
        paused = self.deal(stage="term_sheet", mandate_expiry=(today - dt.timedelta(days=1)).isoformat())
        self.patch(f"{D_URL}{paused['id']}/", {"status": "on_hold"})
        body = self.client.get("/api/dashboard/").json()
        self.assertEqual(body["days"], 30)
        counts = {s["value"]: s["count"] for s in body["stages"]}
        self.assertEqual((counts["term_sheet"], counts["due_diligence"], counts["data_collection"], counts["closed"]),
                         (1, 1, 1, 1))
        self.assertEqual(body["active"], 3)
        self.assertEqual({e["value"]: e["count"] for e in body["exits"]}["on_hold"], 1)
        self.assertEqual([d["id"] for d in body["near_close"]], [late["id"], soon["id"]])
        self.assertEqual([d["id"] for d in body["expiring"]], [paused["id"], late["id"]])
        self.assertNotIn(done["id"], [d["id"] for d in body["near_close"]])
        # a longer window picks up more
        self.client.patch("/api/settings/firm/", {"dashboard_days": 60}, format="json")
        self.assertEqual(FirmSettings.load().dashboard_days, 60)
        body = self.client.get("/api/dashboard/").json()
        self.assertEqual(len(body["expiring"]), 3)
        r = self.client.patch("/api/settings/firm/", {"dashboard_days": 0}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_needs_deals_access(self):
        user = self.make_user("i@firm.com", self.make_role("Investors only", {"investors": ["view"]}))
        self.assertEqual(self.client_for(user).get("/api/dashboard/").status_code, 403)


class TrackerRestoreTests(DealBase):
    def test_restore_refused_when_investor_was_added_again(self):
        d = self.deal()
        inv = self.investor("Northwind Ventures")
        body = {"deal": f"deals:{d['id']}", "investor": f"investors:{inv['id']}"}
        first = self.post(T_URL, body)
        self.client.delete(f"{T_URL}{first['id']}/")
        self.post(T_URL, body)
        r = self.client.post(f"/api/archive/deal_investors/{first['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already on this deal's tracker", r.json()["detail"])
