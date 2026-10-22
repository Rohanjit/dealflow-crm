"""Time every screen's request on a large install, one at a time (load testing; see seed_load).

    DEALFLOW_LOAD_TEST=1 python manage.py load_probe [--only name,name] [--skip-slow]

Each request goes through the whole stack (middleware, sessions, audit log) in this process, as
banker0 (admin) or banker5 (member). Prints time, database queries, response size and status, and the
process's peak memory so far. Writes JSON lines to --out.
"""

from __future__ import annotations

import io
import json
import os
import resource
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext


def _peak_mb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


class Command(BaseCommand):
    help = "Time every screen's request on a large install."

    def add_arguments(self, parser):
        parser.add_argument("--only", default="")
        parser.add_argument("--skip-slow", action="store_true")
        parser.add_argument("--out", default="")

    def handle(self, *args, only, skip_slow, out, **opts):
        if os.environ.get("DEALFLOW_LOAD_TEST") != "1":
            raise CommandError("Set DEALFLOW_LOAD_TEST=1.")
        from django.conf import settings

        settings.ANTIVIRUS = "off"  # imports need the scanner; it's not what's being measured here
        from accounts.models import User
        from deals.models import Deal, DealInvestor
        from investors.models import Investor
        from contacts.models import Contact

        admin = User.objects.get(email="banker0@loadtest.example")
        member = User.objects.get(email="banker5@loadtest.example")
        deal = Deal.objects.filter(restricted=False, status="open").order_by("pk").first()
        inv = Investor.objects.order_by("pk")[500]
        con = Contact.objects.order_by("pk")[500]
        self._strangers(admin)
        clients = {}
        for u in (admin, member):
            c = Client(HTTP_HOST="localhost")
            c.force_login(u)
            clients[u.pk] = c
        self.out = open(out, "w") if out else None
        A, M = admin.pk, member.pk
        sec = __import__("core.models", fromlist=["ChoiceOption"]).ChoiceOption.objects.filter(choice_list__key="sectors").first()
        probes = [
            ("app", A, "GET", "/api/app/", None),
            ("me", A, "GET", "/api/auth/me/", None),
            ("schema", A, "GET", "/api/schema/", None),
            ("dashboard", A, "GET", "/api/dashboard/", None),
            ("dashboard (member)", M, "GET", "/api/dashboard/", None),
            ("investors list", A, "GET", "/api/m/investors/?page=1", None),
            ("investors list (member)", M, "GET", "/api/m/investors/?page=1", None),
            ("investors page 2000", A, "GET", "/api/m/investors/?page=2000", None),
            ("investors search", A, "GET", "/api/m/investors/?q=Apex%20Blue", None),
            ("investors sort last contacted", A, "GET", "/api/m/investors/?ordering=-last_contacted,firm_name", None),
            ("investors filter sector", A, "GET", f"/api/m/investors/?sectors={sec.pk if sec else 1}", None),
            ("investors going cold", A, "GET", "/api/m/investors/?stale=1", None),
            ("contacts list", A, "GET", "/api/m/contacts/?page=1", None),
            ("contacts search", A, "GET", "/api/m/contacts/?q=meera", None),
            ("contacts sort decision level", A, "GET", "/api/m/contacts/?ordering=-primary_contact,decision_level,full_name", None),
            ("companies list", A, "GET", "/api/m/companies/?page=1", None),
            ("outreach list", A, "GET", "/api/m/outreach/?segment=active", None),
            ("outreach board (all, 500)", A, "GET", "/api/m/outreach/?page_size=500", None),
            ("outreach stuck", A, "GET", "/api/m/outreach/?stuck=1", None),
            ("deals list", A, "GET", "/api/m/deals/?page=1", None),
            ("deals list (member)", M, "GET", "/api/m/deals/?page=1", None),
            ("deals stuck", A, "GET", "/api/m/deals/?stuck=1", None),
            ("tracker list", A, "GET", "/api/m/deal_investors/?page=1", None),
            ("tasks mine", A, "GET", "/api/m/tasks/?view=mine", None),
            ("tasks all", A, "GET", "/api/m/tasks/?view=all", None),
            ("investor page", A, "GET", f"/api/m/investors/{inv.pk}/", None),
            ("investor log", A, "GET", f"/api/m/investors/{inv.pk}/notes/", None),
            ("contact page", A, "GET", f"/api/m/contacts/{con.pk}/", None),
            ("deal page", A, "GET", f"/api/m/deals/{deal.pk}/", None),
            ("deal log", A, "GET", f"/api/m/deals/{deal.pk}/notes/", None),
            ("deal tracker panel", A, "GET", f"/api/m/deal_investors/?deal=deals:{deal.pk}&page_size=500", None),
            ("deal room panel", A, "GET", f"/api/deals/{deal.pk}/room/", None),
            ("deal access panel", A, "GET", f"/api/deals/{deal.pk}/access/", None),
            ("investor matching", A, "GET", f"/api/deals/{deal.pk}/matches/", None),
            ("global search", A, "GET", "/api/search/?q=apex", None),
            ("lookup investors", A, "GET", "/api/lookup/investors/?q=ced", None),
            ("bell: tasks", A, "GET", "/api/tasks/alerts/", None),
            ("bell: insights (stuck, follow-ups)", A, "GET", "/api/insights/alerts/", None),
            ("bell: insights (member)", M, "GET", "/api/insights/alerts/", None),
            ("bell: going cold", A, "GET", "/api/stale/", None),
            ("bell: going cold (member)", M, "GET", "/api/stale/", None),
            ("bell: access requests", A, "GET", "/api/access-requests/", None),
            ("bell: meetings", A, "GET", "/api/sync/agenda/", None),
            ("bell: room questions", A, "GET", "/api/rooms/alerts/", None),
            ("bell: security", A, "GET", "/api/security/admin-alerts/", None),
            ("stuck rules", A, "GET", "/api/insights/stages/", None),
            ("insights 12 months", A, "GET", "/api/insights/analytics/?period=12", None),
            ("insights all time", A, "GET", "/api/insights/analytics/?period=all", None),
            ("deleted items", A, "GET", "/api/archive/", None),
            ("security overview", A, "GET", "/api/settings/security/overview/", None),
            ("capture suggestions", A, "GET", "/api/capture/suggestions/", None),
            ("capture check", A, "POST", "/api/capture/check/", {"full_name": "Meera Rao 77", "work_email": "x@inv77.example.com"}),
            ("bulk edit 500 investors", A, "POST", "/api/m/investors/bulk/",
             {"action": "edit", "ids": list(Investor.objects.order_by("pk").values_list("pk", flat=True)[:500]),
              "values": {"relationship_strength": "warm"}}),
            ("edit one investor", A, "PATCH", f"/api/m/investors/{inv.pk}/", {"hq_city": "Pune"}),
            ("add a note", A, "POST", f"/api/m/deals/{deal.pk}/notes/", {"kind": "note", "text": "Load test note",
                                                                         "date": "2026-10-22"}),
            ("move tracker stage", A, "PATCH", f"/api/m/deal_investors/{DealInvestor.objects.filter(deal=deal).first().pk}/",
             {"stage": "meeting_held"}),
        ]
        slow = [
            ("investors duplicates", A, "GET", "/api/m/investors/duplicates/", None),
            ("contacts duplicates", A, "GET", "/api/m/contacts/duplicates/", None),
            ("export investors (100k)", A, "GET", "/api/m/investors/export/", None),
            ("export contacts (400k)", A, "GET", "/api/m/contacts/export/", None),
            ("report: contacts CSV", A, "POST", "/api/reports/run/",
             {"module": "contacts", "format": "csv"}),
            ("report: deals PDF table", A, "POST", "/api/reports/run/",
             {"module": "deals", "format": "pdf", "layout": "table"}),
            ("report: deals CSV (8k)", A, "POST", "/api/reports/run/", {"module": "deals", "format": "csv"}),
            ("report: deals PDF pages (filtered)", A, "POST", "/api/reports/run/",
             {"module": "deals", "format": "pdf", "layout": "pages", "filters": {"q": "Apex Blue"}}),
            ("import 10k contacts: upload+check", A, "IMPORT", "/api/m/contacts/import/", 10000),
            ("backup now", A, "POST", "/api/backups/", {}),
        ]
        todo = probes + ([] if skip_slow else slow)
        if only:
            names = {n.strip() for n in only.split("|")}
            todo = [p for p in todo if p[0] in names]
        self.stdout.write(f"{'request':42} {'ms':>9} {'queries':>8} {'KB':>8} {'status':>6} {'peak MB':>8}")
        for name, uid, method, url, body in todo:
            self.probe(clients[uid], name, method, url, body)

    def _strangers(self, user):
        from capture.models import Stranger
        from django.utils import timezone

        if Stranger.objects.filter(user=user).exists():
            return
        now = timezone.now()
        Stranger.objects.bulk_create([Stranger(user=user, email=f"person{i}@newfirm{i}.example.org", name=f"Person {i}",
                                               count=1, first_seen=now, last_seen=now) for i in range(100)])

    def probe(self, c, name, method, url, body):
        status, size = 0, 0
        with CaptureQueriesContext(connection) as q:
            t = time.monotonic()
            try:
                if method == "IMPORT":
                    status, size = self._import(c, url, body)
                else:
                    kw = {"data": json.dumps(body), "content_type": "application/json"} if body is not None else {}
                    r = getattr(c, method.lower())(url, **kw)
                    status = r.status_code
                    if getattr(r, "streaming", False):
                        size = sum(len(chunk) for chunk in r.streaming_content)
                    else:
                        size = len(r.content)
            except Exception as e:  # a crash is a finding, not the end of the run
                status = f"ERR {type(e).__name__}: {str(e)[:80]}"
            ms = (time.monotonic() - t) * 1000
        row = {"name": name, "ms": round(ms), "queries": len(q.captured_queries), "kb": size // 1024, "status": status,
               "peak_mb": _peak_mb()}
        self.stdout.write(f"{name:42} {row['ms']:>9} {row['queries']:>8} {row['kb']:>8} {str(status):>6} {row['peak_mb']:>8}")
        if self.out:
            self.out.write(json.dumps(row) + "\n")
            self.out.flush()

    def _import(self, c, base, n):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Full name", "Designation", "Work email", "Mobile", "Firm"])
        for i in range(n):
            ws.append([f"Imported Person {i}", "Associate", f"imp{i}@importfirm{i % 300}.example.net",
                       f"+9199{i:08d}", f"Apex Blue Ventures {i % 5000}"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "contacts.xlsx"
        r = c.post(base + "upload/", {"file": buf})
        if r.status_code != 200:
            return r.status_code, len(r.content)
        token = r.json().get("token")
        cols = r.json().get("columns") or r.json().get("headers") or []
        mapping = r.json().get("mapping") or r.json().get("suggested")
        r2 = c.post(base + "check/", data=json.dumps({"token": token, "mapping": mapping}), content_type="application/json")
        return r2.status_code, len(r2.content) + len(cols)
