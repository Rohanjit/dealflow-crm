import datetime as dt
import io
from unittest import mock

from django.utils import timezone
from openpyxl import load_workbook

from core.tests.base import APITestBase
from tasks.models import Task

URL = "/api/m/tasks/"


class TaskBase(APITestBase):
    def post(self, data, status=201, client=None):
        r = (client or self.client).post(URL, data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def patch(self, pk, data, status=200, client=None):
        r = (client or self.client).patch(f"{URL}{pk}/", data, format="json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()


class TaskBasicsTests(TaskBase):
    def test_defaults_and_due_date_and_time(self):
        t = self.post({"title": "Send teaser to Blume", "due": "2026-10-12T14:30"})
        self.assertEqual((t["status"], t["priority"], t["assigned_to"], t["created_by"]),
                         ("open", "medium", self.admin.pk, self.admin.pk))
        self.assertEqual(t["due"], "2026-10-12T14:30")
        t2 = self.post({"title": "Call CFO", "due": "2026-10-12"})
        self.assertEqual(t2["due"], "2026-10-12")
        r = self.client.post(URL, {"title": "Bad", "due": "2026-10-12T25:00"}, format="json")
        self.assertIn("due", r.json())
        r = self.client.post(URL, {"due": "2026-10-12"}, format="json")
        self.assertIn("title", r.json())

    def test_read_only_fields_ignored(self):
        other = self.make_user("o@firm.com", self.make_role("Team", {"tasks": ["view", "create", "edit"]}))
        t = self.post({"title": "Prep IM", "created_by": other.pk, "completed_on": "2026-01-01"})
        self.assertEqual((t["created_by"], t["completed_on"]), (self.admin.pk, None))

    def test_completing_and_reopening(self):
        t = self.post({"title": "Chase NDA"})
        done = self.patch(t["id"], {"status": "done"})
        self.assertEqual(done["completed_on"], timezone.localdate().isoformat())
        again = self.patch(t["id"], {"title": "Chase signed NDA"})
        self.assertEqual(again["completed_on"], done["completed_on"])
        reopened = self.patch(t["id"], {"status": "in_progress"})
        self.assertIsNone(reopened["completed_on"])

    def test_assigning_someone_else_or_nobody(self):
        role = self.make_role("Team", {"tasks": ["view", "create", "edit"]})
        vikram = self.make_user("v@firm.com", role, full_name="Vikram")
        t = self.post({"title": "Model update", "assigned_to": vikram.pk})
        self.assertEqual(t["assigned_to"], vikram.pk)
        t = self.post({"title": "Unowned", "assigned_to": None})
        self.assertIsNone(t["assigned_to"])


class TaskLinksTests(TaskBase):
    def setUp(self):
        super().setUp()
        self.company = self.client.post("/api/m/companies/", {"name": "Acme Foods"}, format="json").json()
        self.deal = self.client.post("/api/m/deals/", {"company": f"companies:{self.company['id']}"}, format="json").json()
        self.outreach = self.client.post("/api/m/outreach/", {"company": f"companies:{self.company['id']}"}, format="json").json()
        self.investor = self.client.post("/api/m/investors/", {"firm_name": "Blume Ventures"}, format="json").json()
        self.contact = self.client.post("/api/m/contacts/", {"full_name": "Ravi Kulkarni"}, format="json").json()

    def test_link_to_each_kind_of_record(self):
        for module, rec, title in (("deals", self.deal, "Acme Foods"), ("outreach", self.outreach, "Acme Foods"),
                                   ("investors", self.investor, "Blume Ventures"), ("contacts", self.contact, "Ravi Kulkarni")):
            t = self.post({"title": f"About {module}", "related": f"{module}:{rec['id']}"})
            self.assertEqual((t["related"]["module"], t["related"]["title"]), (module, title))
            listed = self.client.get(f"{URL}?related={module}:{rec['id']}").json()["results"]
            self.assertEqual([x["id"] for x in listed], [t["id"]])
        results = self.client.get("/api/search/?q=about").json()["results"]
        self.assertIn("Ravi Kulkarni · Open", {r.get("subtitle") for r in results})
        schema = self.client.get("/api/schema/").json()
        for key in ("deals", "outreach", "investors", "contacts"):
            mod = next(m for m in schema["modules"] if m["key"] == key)
            section = next(s for s in mod["sections"] if s["key"] == "tasks")
            self.assertEqual((section["status"], section["panel"]), ("ready", "tasks"))

    def test_deleting_the_record_for_good_unlinks(self):
        t = self.post({"title": "Follow up", "related": f"investors:{self.investor['id']}"})
        self.client.delete(f"/api/m/investors/{self.investor['id']}/")
        self.assertEqual(self.client.delete(f"/api/archive/investors/{self.investor['id']}/").status_code, 204)
        self.assertIsNone(self.client.get(f"{URL}{t['id']}/").json()["related"])


class TaskViewsTests(TaskBase):
    def setUp(self):
        super().setUp()
        role = self.make_role("Team", {"tasks": ["view", "create", "edit"]})
        self.vikram = self.make_user("v@firm.com", role, full_name="Vikram")

    def ids(self, qs, client=None):
        return [t["id"] for t in (client or self.client).get(f"{URL}?{qs}").json()["results"]]

    def test_views_filters_and_order(self):
        a = self.post({"title": "Later", "due": "2026-11-01"})
        b = self.post({"title": "Sooner", "due": "2026-10-01T09:00"})
        c = self.post({"title": "Same day, no time", "due": "2026-10-01"})
        d = self.post({"title": "No date"})
        e = self.post({"title": "Vikram's", "assigned_to": self.vikram.pk})
        done = self.post({"title": "Finished"})
        self.patch(done["id"], {"status": "done"})
        self.assertEqual(self.ids("view=mine&ordering=due"), [b["id"], c["id"], a["id"], d["id"]])
        self.assertEqual(self.ids("view=mine", self.client_for(self.vikram)), [e["id"]])
        self.assertEqual(set(self.ids("view=open")), {a["id"], b["id"], c["id"], d["id"], e["id"]})
        self.assertEqual(self.ids("view=done"), [done["id"]])
        self.assertEqual(self.ids(f"assigned_to={self.vikram.pk}"), [e["id"]])
        self.assertEqual(set(self.ids("status=done,in_progress")), {done["id"]})
        self.assertEqual(set(self.ids("due_from=2026-10-01&due_to=2026-10-01")), {b["id"], c["id"]})
        self.assertEqual(len(self.ids("")), 6)  # no view: everything

    def test_export_writes_date_and_time(self):
        self.post({"title": "Timed", "due": "2026-10-12T14:30"})
        self.post({"title": "Dated", "due": "2026-10-13"})
        wb = load_workbook(io.BytesIO(self.client.get(f"{URL}export/").content))
        header = [c.value for c in wb.active[1]]
        col = header.index("Due")
        values = {r[header.index("Title")]: r[col] for r in wb.active.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(values["Timed"], dt.datetime(2026, 10, 12, 14, 30))
        self.assertEqual(values["Dated"], dt.datetime(2026, 10, 13, 0, 0))

    def test_import_not_offered(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        r = self.client.post(f"{URL}import/upload/", {"file": SimpleUploadedFile("t.csv", b"Title\nX\n")})
        self.assertEqual(r.status_code, 404)


class TaskAlertsTests(TaskBase):
    def test_overdue_and_due_today(self):
        role = self.make_role("Team", {"tasks": ["view", "create", "edit"]})
        vikram = self.make_user("v@firm.com", role, full_name="Vikram")
        now = timezone.make_aware(dt.datetime(2026, 10, 12, 15, 0))
        with mock.patch("django.utils.timezone.now", return_value=now):
            late = self.post({"title": "Yesterday", "due": "2026-10-11"})
            passed = self.post({"title": "This morning", "due": "2026-10-12T10:00"})
            later = self.post({"title": "This evening", "due": "2026-10-12T18:00"})
            allday = self.post({"title": "Sometime today", "due": "2026-10-12"})
            self.post({"title": "Tomorrow", "due": "2026-10-13"})
            self.post({"title": "Someone else's", "due": "2026-10-11", "assigned_to": vikram.pk})
            finished = self.post({"title": "Done already", "due": "2026-10-10"})
            self.patch(finished["id"], {"status": "done"})
            archived = self.post({"title": "Archived", "due": "2026-10-10"})
            self.client.delete(f"{URL}{archived['id']}/")
            body = self.client.get("/api/tasks/alerts/").json()
        self.assertEqual(body["now"], "2026-10-12T15:00")
        self.assertEqual([t["id"] for t in body["overdue"]], [late["id"], passed["id"]])
        self.assertEqual([t["id"] for t in body["today"]], [allday["id"], later["id"]])
        self.assertEqual(body["count"], 4)

    def test_needs_tasks_access(self):
        user = self.make_user("n@firm.com", self.make_role("Investor desk", {"investors": ["view"]}))
        self.assertEqual(self.client_for(user).get("/api/tasks/alerts/").status_code, 403)
        self.assertEqual(self.client_for(user).get(URL).status_code, 403)
