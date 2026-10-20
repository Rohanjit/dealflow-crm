"""Relationships going cold: who is alerted, when, and what "Contacted today" and "Snooze" do."""

import datetime as dt

from django.utils import timezone

from contacts.models import Contact
from core.models import FirmSettings
from core.views import set_field_hidden
from investors.models import Investor

from .base import APITestBase


def days_ago(n):
    return timezone.localdate() - dt.timedelta(days=n)


class StaleTests(APITestBase):
    def setUp(self):
        super().setUp()
        self.member_role = self.make_role("Banker", {"investors": ["view", "edit"], "contacts": ["view", "edit"]})
        self.member = self.make_user("rhea@firm.com", self.member_role)

    def investor(self, name, last=None, owner=None, added_days_ago=0):
        inv = Investor.objects.create(firm_name=name, last_contacted=last, relationship_owner=owner,
                                      created_by=self.admin, updated_by=self.admin)
        if added_days_ago:
            Investor.objects.filter(pk=inv.pk).update(created_at=timezone.now() - dt.timedelta(days=added_days_ago))
        return inv

    def titles(self, client=None):
        r = (client or self.client).get("/api/stale/")
        self.assertEqual(r.status_code, 200)
        return sorted(i["title"] for i in r.json()["items"])

    def test_who_counts_as_going_cold(self):
        self.investor("Old Contact", last=days_ago(91), owner=self.admin)
        self.investor("Recent Contact", last=days_ago(30), owner=self.admin)
        self.investor("Exactly 90", last=days_ago(90), owner=self.admin)
        self.investor("Never, added long ago", owner=self.admin, added_days_ago=120)
        self.investor("Never, added today", owner=self.admin)
        self.assertEqual(self.titles(), ["Never, added long ago", "Old Contact"])
        r = self.client.get("/api/m/investors/?stale=1")
        self.assertEqual(sorted(x["_title"] for x in r.json()["results"]), ["Never, added long ago", "Old Contact"])

    def test_separate_days_for_investors_and_contacts_and_zero_switches_off(self):
        self.investor("Fund 70 days", last=days_ago(70), owner=self.admin)
        Contact.objects.create(full_name="Person 70 days", last_contacted=days_ago(70), relationship_owner=self.admin,
                               created_by=self.admin, updated_by=self.admin)
        self.assertEqual(self.titles(), ["Person 70 days"])  # contacts go cold after 60, investors after 90
        fs = FirmSettings.load()
        fs.stale_contact_days = 0
        fs.stale_investor_days = 60
        fs.save()
        self.assertEqual(self.titles(), ["Fund 70 days"])
        self.assertEqual(self.client.get("/api/m/contacts/?stale=1").json()["count"], 0)

    def test_owner_sees_theirs_admins_see_unowned(self):
        self.investor("Rhea's", last=days_ago(200), owner=self.member)
        self.investor("Nobody's", last=days_ago(200))
        self.investor("Admin's", last=days_ago(200), owner=self.admin)
        self.assertEqual(self.titles(self.client_for(self.member)), ["Rhea's"])
        self.assertEqual(self.titles(), ["Admin's", "Nobody's"])
        items = {i["title"]: i for i in self.client.get("/api/stale/").json()["items"]}
        self.assertTrue(items["Nobody's"]["unowned"])
        self.assertEqual(items["Admin's"]["days"], 200)

    def test_no_view_permission_no_alerts(self):
        role = self.make_role("Contacts only", {"contacts": ["view"]})
        u = self.make_user("c@firm.com", role)
        self.investor("Hidden", last=days_ago(200), owner=u)
        self.assertEqual(self.titles(self.client_for(u)), [])

    def test_contacted_today_and_snooze(self):
        a = self.investor("A Fund", last=days_ago(200), owner=self.admin)
        b = self.investor("B Fund", last=days_ago(200), owner=self.admin)
        r = self.client.post(f"/api/stale/investors/{a.pk}/", {"action": "contacted"}, format="json")
        self.assertEqual(r.status_code, 200)
        a.refresh_from_db()
        self.assertEqual(a.last_contacted, timezone.localdate())
        self.assertEqual(a.updated_by, self.admin)
        r = self.client.post(f"/api/stale/investors/{b.pk}/", {"action": "snooze"}, format="json")
        self.assertEqual([i["title"] for i in r.json()["items"]], [])
        b.refresh_from_db()
        self.assertEqual(b.stale_snoozed_until, timezone.localdate() + dt.timedelta(days=30))
        # The list's "Going cold" button still shows snoozed ones; only the reminder is hidden.
        self.assertEqual([x["_title"] for x in self.client.get("/api/m/investors/?stale=1").json()["results"]], ["B Fund"])
        # After the snooze runs out, it's back.
        Investor.objects.filter(pk=b.pk).update(stale_snoozed_until=days_ago(1))
        self.assertEqual(self.titles(), ["B Fund"])

    def test_actions_need_edit_permission_and_a_live_record(self):
        viewer = self.make_user("v@firm.com", self.make_role("Viewer", {"investors": ["view"]}))
        a = self.investor("A Fund", last=days_ago(200), owner=viewer)
        c = self.client_for(viewer)
        self.assertEqual(c.post(f"/api/stale/investors/{a.pk}/", {"action": "snooze"}, format="json").status_code, 403)
        self.assertEqual(self.client.post(f"/api/stale/investors/{a.pk}/", {"action": "maybe"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/stale/deals/1/", {"action": "snooze"}, format="json").status_code, 404)
        self.client.delete(f"/api/m/investors/{a.pk}/")
        self.assertEqual(self.client.post(f"/api/stale/investors/{a.pk}/", {"action": "snooze"}, format="json").status_code, 404)

    def test_switched_off_last_contacted_means_no_alerts(self):
        self.investor("Old", last=days_ago(200), owner=self.admin)
        set_field_hidden("investors", "last_contacted", True)
        self.assertEqual(self.titles(), [])

    def test_settings(self):
        r = self.client.patch("/api/settings/firm/", {"stale_investor_days": 120, "stale_contact_days": 0}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["stale_investor_days"], r.json()["stale_contact_days"]), (120, 0))
        for bad in (-1, 731, "x", True, None):
            r = self.client.patch("/api/settings/firm/", {"stale_investor_days": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_schema_offers_the_going_cold_button(self):
        mods = {m["key"]: m for m in self.client.get("/api/schema/").json()["modules"]}
        self.assertEqual(mods["investors"]["quick_filters"][0]["param"], "stale")
        self.assertEqual(mods["contacts"]["quick_filters"][0]["label"], "Going cold")
        self.assertEqual([q["param"] for q in mods["deals"]["quick_filters"]], ["stuck"])  # not going cold
        self.assertEqual(mods["tasks"]["quick_filters"], [])
