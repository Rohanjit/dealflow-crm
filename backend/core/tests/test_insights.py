"""Insights (decision 59): stuck alerts, follow-up nudges, win-rate analytics and their settings."""

import datetime as dt

from django.utils import timezone

from core import stuck
from core.models import FirmSettings, Note
from deals.models import Deal, DealInvestor
from investors.models import Investor
from outreach.models import Company, Outreach
from tasks.models import Task

from .base import APITestBase

EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


def days_ago(n):
    return timezone.localdate() - dt.timedelta(days=n)


def at(n, hours=0):
    return timezone.now() - dt.timedelta(days=n, hours=hours)


class InsightBase(APITestBase):
    def setUp(self):
        super().setUp()
        self.role = self.make_role("Banker", EVERYTHING)
        self.rhea = self.make_user("rhea@firm.com", self.role, full_name="Rhea Banker")
        self.omar = self.make_user("omar@firm.com", self.role, full_name="Omar Other")
        self.c_rhea, self.c_omar = self.client_for(self.rhea), self.client_for(self.omar)
        self.n = 0

    def company(self, name=None, **kw):
        self.n += 1
        return Company.objects.create(name=name or f"Company {self.n}", created_by=self.admin, updated_by=self.admin, **kw)

    def attempt(self, stage="contacted", entered=0, status="open", banker=None, created=None, **kw):
        o = Outreach.objects.create(company=kw.pop("company", None) or self.company(), stage=stage, status=status,
                                    stage_entered=days_ago(entered), lead_banker=banker, created_by=self.admin,
                                    updated_by=self.admin, **kw)
        if created is not None:
            Outreach.objects.filter(pk=o.pk).update(created_at=at(created))
        return o

    def deal(self, stage="first_draft", entered=0, status="open", banker=None, **kw):
        d = Deal.objects.create(company=self.company(), stage=stage, status=status, stage_entered=days_ago(entered),
                                lead_banker=banker, created_by=self.admin, updated_by=self.admin, **kw)
        return d

    def alerts(self, client=None):
        r = (client or self.client).get("/api/insights/alerts/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def titles(self, kind, client=None):
        return sorted(i["title"] for i in self.alerts(client)[kind])

    def snooze(self, client, **body):
        r = client.post("/api/insights/alerts/snooze/", body, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()


class StuckRuleTests(InsightBase):
    def stay(self, rid, stage, start, days, paused=False):
        """A finished stay in `stage`: entered `start` days ago, left `days` later."""
        Note.objects.create(module="outreach", record_id=rid, kind="stage", date=days_ago(start),
                            meta={"to_stage": stage, "to_status": "open"})
        if paused:
            Note.objects.create(module="outreach", record_id=rid, kind="stage", date=days_ago(start - 1),
                                meta={"from_stage": stage, "to_stage": stage, "from_status": "open", "to_status": "lost"})
        Note.objects.create(module="outreach", record_id=rid, kind="stage", date=days_ago(start - days),
                            meta={"from_stage": stage, "to_stage": "meeting", "from_status": "lost" if paused else "open",
                                  "to_status": "open"})

    def rule(self, module, stage):
        return next(r for r in stuck.rules(module) if r["stage"] == stage)

    def test_defaults_until_enough_history_then_learned(self):
        r = self.rule("outreach", "contacted")
        self.assertEqual((r["days"], r["source"], r["samples"]), (14, "default", 0))
        for rid, d in enumerate([8, 10, 10, 12], start=1):
            self.stay(rid, "contacted", 60, d)
        self.assertEqual(self.rule("outreach", "contacted")["source"], "default")  # 4 stays: not enough yet
        self.stay(5, "contacted", 60, 30)
        r = self.rule("outreach", "contacted")
        self.assertEqual((r["median"], r["samples"], r["source"], r["days"]), (10, 5, "learned", 15))

    def test_paused_stays_and_quick_moves_are_handled(self):
        for rid in range(1, 6):
            self.stay(rid, "contacted", 60, 2)
        self.stay(9, "contacted", 60, 40, paused=True)  # lost then reopened: not a normal stay
        r = self.rule("outreach", "contacted")
        self.assertEqual((r["samples"], r["days"]), (5, stuck.FLOOR))  # 1.5 × 2 days, but never under 7

    def test_final_stage_has_no_rule(self):
        self.assertNotIn("won", [r["stage"] for r in stuck.rules("outreach")])
        self.assertNotIn("closed", [r["stage"] for r in stuck.rules("deals")])


class StuckAlertTests(InsightBase):
    def test_who_is_stuck_and_who_hears(self):
        self.attempt(entered=20, banker=self.rhea, company=self.company("Rhea's stuck"))
        self.attempt(entered=10, banker=self.rhea, company=self.company("Rhea's fine"))
        self.attempt(entered=20, company=self.company("Nobody's"))
        self.attempt(entered=90, stage="won", banker=self.rhea, company=self.company("Won long ago"))
        self.attempt(entered=90, status="lost", banker=self.rhea, company=self.company("Lost long ago"))
        self.attempt(entered=90, status="on_hold", banker=self.rhea, company=self.company("Paused"))
        self.assertEqual(self.titles("stuck", self.c_rhea), ["Rhea's stuck"])
        self.assertEqual(self.titles("stuck", self.c_omar), [])
        self.assertEqual(self.titles("stuck"), ["Nobody's"])  # unassigned ones go to admins
        item = self.alerts(self.c_rhea)["stuck"][0]
        self.assertEqual((item["days"], item["flag_after"], item["stage_label"]), (20, 14, "Contacted"))
        # The list filter shows all of them, whoever they belong to.
        rows = self.client.get("/api/m/outreach/?stuck=1&view=all").json()["results"]
        self.assertEqual(sorted(r["_title"] for r in rows), ["Nobody's", "Rhea's stuck"])

    def test_deal_team_hears_and_restricted_deals_stay_hidden(self):
        d = self.deal(entered=30, banker=self.omar)
        d.team_vp.add(self.rhea)
        self.assertEqual(len(self.alerts(self.c_rhea)["stuck"]), 1)
        self.assertEqual(len(self.alerts(self.c_omar)["stuck"]), 1)
        outsider = self.client_for(self.make_user("x@firm.com", self.role))
        self.assertEqual(self.alerts(outsider)["stuck"], [])
        Deal.objects.filter(pk=d.pk).update(restricted=True)
        self.assertEqual(self.client_for(self.make_user("y@firm.com", self.role)).get("/api/m/deals/?stuck=1").json()["count"], 0)
        self.assertEqual(self.c_rhea.get("/api/m/deals/?stuck=1").json()["count"], 1)

    def test_overrides_and_switch(self):
        self.attempt(entered=20, banker=self.rhea)
        r = self.client.put("/api/settings/alerts/", {"stuck_overrides": {"outreach": {"contacted": 30}}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        rule = next(x for x in r.json()["rules"]["outreach"] if x["stage"] == "contacted")
        self.assertEqual((rule["days"], rule["source"]), (30, "set"))
        self.assertEqual(self.alerts(self.c_rhea)["stuck"], [])
        self.client.put("/api/settings/alerts/", {"stuck_overrides": {"outreach": {"contacted": 5}}}, format="json")
        self.assertEqual(len(self.alerts(self.c_rhea)["stuck"]), 1)
        self.client.put("/api/settings/alerts/", {"stuck_overrides": {"outreach": {"contacted": 0}}}, format="json")
        self.assertEqual(self.alerts(self.c_rhea)["stuck"], [])  # 0 = never
        self.client.put("/api/settings/alerts/", {"stuck_overrides": {}, "stuck_alerts": False}, format="json")
        self.assertEqual(self.alerts(self.c_rhea)["stuck"], [])
        self.assertEqual(self.c_rhea.get("/api/m/outreach/?stuck=1").json()["count"], 1)  # the filter still works

    def test_snooze_ends_when_the_stage_changes(self):
        o = self.attempt(entered=20, banker=self.rhea)
        body = self.snooze(self.c_rhea, kind="stuck", module="outreach", id=o.pk)
        self.assertEqual(body["stuck"], [])
        Outreach.objects.filter(pk=o.pk).update(stage="meeting", stage_entered=days_ago(30))
        self.assertEqual(len(self.alerts(self.c_rhea)["stuck"]), 1)
        self.assertEqual(len(self.alerts(self.client)["stuck"]), 0)  # snoozes are per person anyway

    def test_settings_validation_and_permission(self):
        bad = [
            {"stuck_overrides": {"outreach": {"contacted": -1}}},
            {"stuck_overrides": {"outreach": {"contacted": "abc"}}},
            {"stuck_overrides": {"outreach": {"won": 5}}},
            {"stuck_overrides": {"nope": {}}},
            {"stuck_overrides": []},
            {"stuck_alerts": "yes"},
            {"nudge_quiet_days": 999},
            {"nudge_tracker_days": True},
        ]
        for body in bad:
            self.assertEqual(self.client.put("/api/settings/alerts/", body, format="json").status_code, 400, body)
        self.assertEqual(self.c_rhea.get("/api/settings/alerts/").status_code, 403)
        self.assertEqual(self.c_rhea.put("/api/settings/alerts/", {"stuck_alerts": False}, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/insights/alerts/snooze/", {"kind": "x", "module": "outreach", "id": 1},
                                          format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/insights/alerts/snooze/", {"kind": "stuck", "module": "outreach", "id": 999},
                                          format="json").status_code, 404)
        rules = self.c_rhea.get("/api/insights/stages/").json()
        self.assertEqual(set(rules), {"outreach", "deals"})


class NudgeTests(InsightBase):
    def test_quiet_records(self):
        d = self.deal(entered=3, banker=self.rhea)
        Deal.objects.filter(pk=d.pk).update(updated_at=at(20))
        self.assertEqual(self.alerts(self.c_rhea)["quiet"][0]["days"], 20)
        Note.objects.create(module="deals", record_id=d.pk, kind="call", date=days_ago(1), created_by=self.omar)
        self.assertEqual(self.alerts(self.c_rhea)["quiet"], [])
        # A record already shown as stuck isn't repeated as quiet.
        o = self.attempt(entered=40, banker=self.rhea)
        Outreach.objects.filter(pk=o.pk).update(updated_at=at(40))
        a = self.alerts(self.c_rhea)
        self.assertEqual((len(a["stuck"]), len(a["quiet"])), (1, 0))
        FirmSettings.objects.filter(pk=1).update(nudge_quiet_days=0)
        Deal.objects.filter(pk=d.pk).update(updated_at=at(20))
        Note.objects.all().delete()
        self.assertEqual(self.alerts(self.c_rhea)["quiet"], [])

    def test_tracker_investors_not_moving(self):
        d = self.deal(entered=1, banker=self.rhea)

        def entry(name, stage="teaser_sent", days=12, status="open"):
            inv = Investor.objects.create(firm_name=name, created_by=self.admin, updated_by=self.admin)
            e = DealInvestor.objects.create(deal=d, investor=inv, stage=stage, status=status, stage_entered=days_ago(days),
                                            created_by=self.admin, updated_by=self.admin)
            DealInvestor.objects.filter(pk=e.pk).update(updated_at=at(days))
            return e

        entry("Slow One")
        busy = entry("Slow Two")
        entry("Fresh", days=3)
        entry("Picked", stage="selected")
        entry("Said no", status="declined")
        t = self.alerts(self.c_rhea)["tracker"]
        self.assertEqual((len(t), t[0]["count"], sorted(t[0]["investors"])), (1, 2, ["Slow One", "Slow Two"]))
        Note.objects.create(module="deal_investors", record_id=busy.pk, kind="call", date=days_ago(0))
        self.assertEqual(self.alerts(self.c_rhea)["tracker"][0]["count"], 1)
        self.snooze(self.c_rhea, kind="tracker", module="deals", id=d.pk)
        self.assertEqual(self.alerts(self.c_rhea)["tracker"], [])

    def test_meeting_without_a_follow_up_task(self):
        o = self.attempt(banker=self.omar)
        r = self.c_rhea.post(f"/api/m/outreach/{o.pk}/notes/", {"kind": "meeting", "date": days_ago(0).isoformat(),
                                                                "text": "Met the CFO"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.alerts(self.c_rhea)["meeting"], [])  # two hours' grace
        Note.objects.filter(pk=r.json()["id"]).update(created_at=at(0, hours=3))
        m = self.alerts(self.c_rhea)["meeting"]
        self.assertEqual((len(m), m[0]["related"], m[0]["kind"]), (1, f"outreach:{o.pk}", "meeting"))
        self.assertEqual(self.alerts(self.c_omar)["meeting"], [])  # it's for whoever logged it
        Task.objects.create(title="Send the deck", outreach=o, created_by=self.rhea, updated_by=self.rhea)
        self.assertEqual(self.alerts(self.c_rhea)["meeting"], [])

    def test_no_follow_up_needed_until_the_next_meeting(self):
        o = self.attempt()
        n = Note.objects.create(module="outreach", record_id=o.pk, kind="call", date=days_ago(1), created_by=self.rhea)
        Note.objects.filter(pk=n.pk).update(created_at=at(1))
        self.assertEqual(len(self.alerts(self.c_rhea)["meeting"]), 1)
        self.snooze(self.c_rhea, kind="meeting", module="outreach", id=o.pk, forever=True)
        self.assertEqual(self.alerts(self.c_rhea)["meeting"], [])
        n2 = Note.objects.create(module="outreach", record_id=o.pk, kind="meeting", date=days_ago(0), created_by=self.rhea)
        Note.objects.filter(pk=n2.pk).update(created_at=at(0, hours=5))
        self.assertEqual(len(self.alerts(self.c_rhea)["meeting"]), 1)
        # Old meetings stop nudging after two weeks; people who can't add tasks aren't nudged.
        Note.objects.filter(pk=n2.pk).update(created_at=at(15))
        self.assertEqual(self.alerts(self.c_rhea)["meeting"], [])
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"outreach": ["view", "edit"], "tasks": ["view"]}))
        Note.objects.create(module="outreach", record_id=o.pk, kind="meeting", date=days_ago(1), created_by=viewer)
        Note.objects.filter(created_by=viewer).update(created_at=at(1))
        self.assertEqual(self.alerts(self.client_for(viewer))["meeting"], [])

    def test_counts(self):
        self.attempt(entered=20, banker=self.rhea)
        a = self.alerts(self.c_rhea)
        self.assertEqual((a["count"], a["counts"]["stuck"]), (1, 1))


class AnalyticsTests(InsightBase):
    def analytics(self, client=None, period="12"):
        r = (client or self.client).get(f"/api/insights/analytics/?period={period}")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_win_rates_funnel_and_breakdowns(self):
        ref, cold = self.opt("outreach_sources", "Referral"), self.opt("outreach_sources", "Cold outreach")
        lost = self.opt("lost_reasons", "Chose another banker")
        money = {"expected_size_currency": "INR", "expected_size_unit": "Cr"}
        for i in range(3):
            self.attempt(stage="won", source=ref, banker=self.rhea, created=40,
                         expected_size_amount=50, expected_size_value=50 * 10**7, **money)
        self.attempt(stage="proposal", status="lost", lost_reason=lost, source=cold, banker=self.omar,
                     expected_size_amount=600, expected_size_value=600 * 10**7, **money)
        self.attempt(stage="meeting", source=cold, banker=self.omar)
        self.attempt(stage="identified", status="on_hold", banker=self.omar)
        self.attempt(stage="won", created=500)  # outside the last 12 months
        o = self.analytics()["outreach"]
        self.assertEqual((o["total"], o["won"], o["lost"], o["open"], o["paused"]), (6, 3, 1, 1, 1))
        self.assertEqual(o["win_rate"], 0.75)
        funnel = {f["stage"]: f["reached"] for f in o["funnel"]}
        self.assertEqual((funnel["identified"], funnel["meeting"], funnel["proposal"], funnel["won"]), (6, 5, 4, 3))
        by_source = {r["label"]: r for r in o["by"]["source"]}
        self.assertEqual((by_source["Referral"]["rate"], by_source["Cold outreach"]["rate"]), (1.0, 0.0))
        self.assertEqual(by_source["Not set"]["rate"], None)
        self.assertEqual(o["lost_reasons"], [{"label": "Chose another banker", "count": 1}])
        self.assertEqual([r["label"] for r in o["by"]["size"]], ["₹25–100 Cr", "₹500 Cr and above"])
        self.assertEqual(o["median_days_to_mandate"], 40)
        self.assertEqual(self.analytics(period="all")["outreach"]["total"], 7)

    def test_banker_table_needs_the_permission(self):
        self.attempt(stage="won", banker=self.rhea)
        self.assertIsNone(self.analytics(self.c_rhea)["bankers"])
        self.assertFalse(self.analytics(self.c_rhea)["can_see_team"])
        boss = self.make_user("boss@firm.com", self.make_role("Partner", EVERYTHING, see_team_performance=True))
        rows = self.analytics(self.client_for(boss))["bankers"]
        self.assertEqual((rows[0]["name"], rows[0]["won"], rows[0]["win_rate"]), ("Rhea Banker", 1, 1.0))
        self.assertIsNotNone(self.analytics()["bankers"])  # admins

    def test_deals_and_investors(self):
        a = self.deal(stage="closed", mandate_date=days_ago(100), closing_date=days_ago(10))
        self.deal(stage="term_sheet", status="withdrawn")
        self.deal(stage="first_draft")
        inv = Investor.objects.create(firm_name="Keen Capital", created_by=self.admin, updated_by=self.admin)
        b = self.deal(stage="due_diligence")
        for deal, stage in ((a, "term_sheet_received"), (b, "nda_signed")):
            DealInvestor.objects.create(deal=deal, investor=inv, stage=stage, created_by=self.admin, updated_by=self.admin)
        r = self.analytics()
        d = r["deals"]
        self.assertEqual((d["total"], d["closed"], d["withdrawn"], d["close_rate"]), (4, 1, 1, 0.5))
        self.assertEqual(d["median_days_to_close"], 90)
        top = r["investors"]["top"][0]
        self.assertEqual((top["name"], top["deals"], top["engaged"], top["term_sheets"], top["rate"]), ("Keen Capital", 2, 2, 1, 1.0))

    def test_needs_outreach_or_deals(self):
        nobody = self.make_user("n@firm.com", self.make_role("Contacts only", {"contacts": ["view"]}))
        self.assertEqual(self.client_for(nobody).get("/api/insights/analytics/").status_code, 403)
        only_o = self.client_for(self.make_user("o@firm.com", self.make_role("Outreach only", {"outreach": ["view"]})))
        body = self.analytics(only_o)
        self.assertIsNone(body["deals"])
        self.assertEqual(set(body["stages"]), {"outreach"})

    def test_role_flag_round_trip(self):
        r = self.client.post("/api/roles/", {"name": "Partner", "flags": {"see_team_performance": True}, "perms": {}}, format="json")
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertTrue(r.json()["flags"]["see_team_performance"])


class SampleTests(InsightBase):
    def test_sample_firm_has_something_to_show(self):
        from core import sample

        guest = sample.load()
        a = self.alerts(self.client_for(guest))
        self.assertEqual([i["title"] for i in a["stuck"]], ["Brightpath Clinics"])
        self.assertEqual([i["title"] for i in a["meeting"]], ["Halcyon Hotels"])
        self.assertEqual([i["title"] for i in a["tracker"]], ["Nova Retail"])
        self.assertEqual([i["title"] for i in a["quiet"]], ["Nova Retail"])
        body = self.client_for(guest).get("/api/insights/analytics/").json()
        self.assertGreater(body["outreach"]["won"], 0)
        self.assertIsNotNone(body["bankers"])
