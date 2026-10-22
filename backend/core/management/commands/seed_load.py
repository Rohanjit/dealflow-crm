"""Fill an empty install with a very large made-up firm, for load testing (never on a real install).

    python manage.py seed_load --scale 1      # a big firm
    python manage.py seed_load --scale 20     # extreme

Per unit of scale: 5,000 investors, 20,000 contacts, 2,500 companies, 4,000 outreach attempts,
400 deals with 20 tracker investors each, 50,000 log entries, 5,000 tasks, 1,000 Vault documents
(tiny files), 10 people. Everything goes in with bulk inserts, so signals, stamps and the audit log
are skipped on purpose. Refuses to run on an install that already has records, or without
DEALFLOW_LOAD_TEST=1.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import random
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

WORDS = ("Apex Blue Cedar Delta Ember Falcon Granite Harbor Indigo Juniper Kestrel Lumen Meridian Nimbus Orchid "
         "Pinnacle Quartz Raven Sierra Tidal Umber Vertex Willow Xenon Yonder Zephyr Aurora Banyan Cobalt Dune "
         "Everest Fjord Grove Helix Iris Jade Kite Lotus Monsoon Nova Onyx Prism Quill Ridge Saffron Teak").split()
FIRM_SUFFIX = ["Ventures", "Capital", "Partners", "Investments", "Fund", "Advisors", "Holdings", "Equity"]
CO_SUFFIX = ["Pharma", "Foods", "Retail", "Tech", "Logistics", "Fintech", "Health", "Motors", "Textiles", "Energy"]
FIRST = ("Aarav Aditi Akash Ananya Arjun Bhavna Chirag Deepa Farhan Gauri Harsh Isha Jay Kavya Kunal Leela Manav "
         "Meera Neha Nikhil Pooja Rahul Riya Rohan Sanjay Shreya Tanvi Uday Varun Zara").split()
LAST = "Shah Rao Mehta Iyer Gupta Kapoor Menon Nair Reddy Das Bose Kulkarni Joshi Verma Singh Pillai Desai".split()
CITIES = ["Mumbai", "Bengaluru", "Delhi", "Pune", "Chennai", "Hyderabad", "Singapore", "London", "New York", "Dubai"]


def _name(rng, i, suffixes):
    return f"{rng.choice(WORDS)} {rng.choice(WORDS)} {rng.choice(suffixes)} {i}"


class Command(BaseCommand):
    help = "Fill an empty install with a very large made-up firm for load testing."

    def add_arguments(self, parser):
        parser.add_argument("--scale", type=int, default=1)
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, *args, scale: int, seed: int, **opts):
        if os.environ.get("DEALFLOW_LOAD_TEST") != "1":
            raise CommandError("Set DEALFLOW_LOAD_TEST=1: this fills the install with made-up records.")
        from investors.models import Investor

        if Investor.objects.exists():
            raise CommandError("This install already has investors. Load tests need an empty install.")
        # Made-up test data, repeatable from the seed: not used for anything secret.
        rng = random.Random(seed)  # nosec B311
        self.t0 = time.monotonic()
        users = self.people(rng, 10 * scale)
        opts_ = self.options()
        investors = self.investors(rng, 5000 * scale, users, opts_)
        companies = self.companies(rng, 2500 * scale, users, opts_)
        self.contacts(rng, 20000 * scale, investors, companies, users, opts_)
        outreach = self.outreach(rng, 4000 * scale, companies, users, opts_)
        deals = self.deals(rng, 400 * scale, companies, users, opts_)
        entries = self.tracker(rng, deals, investors, users)
        self.notes(rng, 50000 * scale, outreach, deals, entries, investors, users)
        self.tasks(rng, 5000 * scale, outreach, deals, investors, users)
        self.documents(rng, 1000 * scale, deals, companies, users)
        self.log("done")

    def log(self, what):
        self.stdout.write(f"[{time.monotonic() - self.t0:7.1f}s] {what}")

    # ------------------------------------------------------------ people and lists

    def people(self, rng, n):
        from accounts.models import Role, User

        from core.models import FirmSettings

        fs = FirmSettings.load()
        fs.firm_name = "Loadtest Capital"
        fs.save()
        role = Role.objects.get(name="Member")
        role.share_with_investors = True
        role.send_campaigns = True
        role.save()
        admin_role = Role.objects.get(is_admin=True)
        from django.contrib.auth.hashers import make_password

        pw = make_password("Load-test-pass-1")  # one hash for everyone: Argon2 per person would take minutes
        users = [User(email=f"banker{i}@loadtest.example", full_name=f"{rng.choice(FIRST)} {rng.choice(LAST)} {i}",
                      role=admin_role if i == 0 else role, password=pw) for i in range(n)]
        User.objects.bulk_create(users, batch_size=500)
        users = list(User.objects.filter(email__endswith="@loadtest.example").order_by("pk"))
        # Everyone signs in with the same authenticator secret (the load tester computes the codes).
        from accounts.mfa import _seal
        from accounts.models import TotpDevice

        sealed = _seal("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP")
        TotpDevice.objects.bulk_create([TotpDevice(user=u, secret=sealed, confirmed=True) for u in users])
        self.log(f"{len(users)} people")
        return users

    def options(self):
        from core.models import ChoiceOption

        out = {}
        for o in ChoiceOption.objects.filter(is_active=True).select_related("choice_list"):
            out.setdefault(o.choice_list.key, []).append(o)
        return out

    # ------------------------------------------------------------ records

    def investors(self, rng, n, users, opts):
        from investors.models import Investor

        today = timezone.localdate()
        rows = []
        for i in range(n):
            lo = rng.choice([5, 10, 25, 50, 100])
            rows.append(Investor(
                firm_name=_name(rng, i, FIRM_SUFFIX), website=f"https://inv{i}.example.com",
                hq_city=rng.choice(CITIES), country="India", description="Made up for load testing. " * 5,
                investor_type=rng.choice(opts.get("investor_types") or [None]),
                min_ticket_amount=lo, min_ticket_currency="INR", min_ticket_unit="Cr", min_ticket_value=lo * 10**7,
                max_ticket_amount=lo * 5, max_ticket_currency="INR", max_ticket_unit="Cr", max_ticket_value=lo * 5 * 10**7,
                actively_deploying=rng.choice(["yes", "no", "unknown"]),
                relationship_owner=rng.choice(users), relationship_strength=rng.choice(["strong", "warm", "cold", ""]),
                last_contacted=today - dt.timedelta(days=rng.randint(0, 400)) if rng.random() < 0.8 else None,
                created_by=users[0], updated_by=users[0]))
        Investor.objects.bulk_create(rows, batch_size=2000)
        ids = list(Investor.objects.values_list("pk", flat=True))
        through = Investor.sectors.through
        sec = opts.get("sectors") or []
        if sec:
            through.objects.bulk_create([through(investor_id=pk, choiceoption_id=o.pk) for pk in ids
                                         for o in rng.sample(sec, k=min(3, len(sec)))], batch_size=5000)
        geo = opts.get("geographies") or []
        if geo:
            t2 = Investor.geography_focus.through
            t2.objects.bulk_create([t2(investor_id=pk, choiceoption_id=rng.choice(geo).pk) for pk in ids], batch_size=5000)
        self.log(f"{len(ids)} investors")
        return ids

    def companies(self, rng, n, users, opts):
        from outreach.models import Company

        rows = [Company(name=_name(rng, i, CO_SUFFIX) + " Pvt Ltd", website=f"https://co{i}.example.in",
                        city=rng.choice(CITIES), sector=rng.choice(opts.get("sectors") or [None]),
                        geography=rng.choice(opts.get("geographies") or [None]),
                        description="A made-up company for load testing. " * 4,
                        revenue_amount=rng.randint(10, 900), revenue_currency="INR", revenue_unit="Cr",
                        created_by=users[0], updated_by=users[0]) for i in range(n)]
        for r in rows:
            r.revenue_value = r.revenue_amount * 10**7
        Company.objects.bulk_create(rows, batch_size=2000)
        ids = list(Company.objects.values_list("pk", flat=True))
        self.log(f"{len(ids)} companies")
        return ids

    def contacts(self, rng, n, investors, companies, users, opts):
        from contacts.models import Contact

        today = timezone.localdate()
        levels = opts.get("decision_maker_levels") or [None]
        batch = []
        for i in range(n):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            on_inv = rng.random() < 0.7
            batch.append(Contact(
                full_name=f"{first} {last} {i}", designation=rng.choice(["Partner", "Principal", "VP", "CFO", "Director"]),
                investor_id=rng.choice(investors) if on_inv else None, company_id=None if on_inv else rng.choice(companies),
                work_email=f"{first.lower()}.{last.lower()}{i}@firm{i % 9000}.example.com",
                mobile=f"+9198{rng.randint(10_000_000, 99_999_999)}", city=rng.choice(CITIES),
                linkedin=f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-{i}",
                decision_level=rng.choice(levels), primary_contact=rng.random() < 0.2,
                relationship_owner=rng.choice(users),
                last_contacted=today - dt.timedelta(days=rng.randint(0, 400)) if rng.random() < 0.7 else None,
                created_by=users[0], updated_by=users[0]))
            if len(batch) == 5000:
                Contact.objects.bulk_create(batch)
                batch = []
        Contact.objects.bulk_create(batch)
        self.log(f"{Contact.objects.count()} contacts")

    def outreach(self, rng, n, companies, users, opts):
        from outreach.models import STAGES, Outreach

        stages = [k for k, _ in STAGES]
        today = timezone.localdate()
        rows = []
        for i in range(n):
            st = rng.choice(stages)
            status = rng.choices(["open", "on_hold", "lost"], [6, 1, 2])[0]
            rows.append(Outreach(
                company_id=companies[i % len(companies)], stage=st, status=status,
                stage_entered=today - dt.timedelta(days=rng.randint(0, 200)),
                lead_banker=rng.choice(users), transaction_type=rng.choice(opts.get("transaction_types") or [None]),
                source=rng.choice(opts.get("outreach_sources") or [None]),
                lost_reason=rng.choice(opts.get("lost_reasons") or [None]) if status == "lost" else None,
                hold_reason=rng.choice(opts.get("hold_reasons") or [None]) if status == "on_hold" else None,
                revisit_date=today + dt.timedelta(days=30) if status == "on_hold" else None,
                expected_size_amount=rng.randint(10, 500), expected_size_currency="INR", expected_size_unit="Cr",
                created_by=users[0], updated_by=users[0]))
        for r in rows:
            r.expected_size_value = r.expected_size_amount * 10**7
        Outreach.objects.bulk_create(rows, batch_size=2000)
        ids = list(Outreach.objects.values_list("pk", flat=True))
        self.log(f"{len(ids)} outreach attempts")
        return ids

    def deals(self, rng, n, companies, users, opts):
        from deals.models import STAGES, Deal

        stages = [k for k, _ in STAGES]
        today = timezone.localdate()
        rows = []
        for i in range(n):
            amt = rng.randint(20, 800)
            rows.append(Deal(
                company_id=companies[-(i % len(companies)) - 1], stage=rng.choice(stages),
                status=rng.choices(["open", "on_hold", "withdrawn", "dropped"], [8, 1, 1, 1])[0],
                stage_entered=today - dt.timedelta(days=rng.randint(0, 150)), lead_banker=rng.choice(users),
                mandate_date=today - dt.timedelta(days=rng.randint(30, 700)),
                target_close_date=today + dt.timedelta(days=rng.randint(-60, 300)),
                mandate_expiry=today + dt.timedelta(days=rng.randint(-30, 400)),
                target_raise_amount=amt, target_raise_currency="INR", target_raise_unit="Cr", target_raise_value=amt * 10**7,
                instrument=rng.choice(["equity", "ccps", "debt", "mix"]),
                transaction_type=rng.choice(opts.get("transaction_types") or [None]),
                created_by=users[0], updated_by=users[0]))
        Deal.objects.bulk_create(rows, batch_size=1000)
        ids = list(Deal.objects.values_list("pk", flat=True))
        md = Deal.team_md.through
        md.objects.bulk_create([md(deal_id=pk, user_id=rng.choice(users).pk) for pk in ids], batch_size=5000)
        vp = Deal.team_vp.through
        vp.objects.bulk_create([vp(deal_id=pk, user_id=rng.choice(users).pk) for pk in ids], batch_size=5000)
        # One in ten deals restricted, so the visibility rules are part of every query.
        Deal.objects.filter(pk__in=ids[::10]).update(restricted=True)
        self.log(f"{len(ids)} deals")
        return ids

    def tracker(self, rng, deals, investors, users):
        from deals.models import TRACKER_STAGES, DealInvestor

        stages = [k for k, _ in TRACKER_STAGES]
        today = timezone.localdate()
        batch, n = [], 0
        for d in deals:
            for inv in rng.sample(investors, k=min(20, len(investors))):
                batch.append(DealInvestor(deal_id=d, investor_id=inv, stage=rng.choice(stages),
                                          status=rng.choices(["open", "declined", "no_response"], [6, 2, 1])[0],
                                          stage_entered=today - dt.timedelta(days=rng.randint(0, 90)),
                                          created_by=users[0], updated_by=users[0]))
                if len(batch) == 5000:
                    DealInvestor.objects.bulk_create(batch)
                    n += len(batch)
                    batch = []
        DealInvestor.objects.bulk_create(batch)
        n += len(batch)
        self.log(f"{n} tracker entries")
        return list(DealInvestor.objects.values_list("pk", flat=True)[:200000])

    def notes(self, rng, n, outreach, deals, entries, investors, users):
        from core.models import Note
        from deals.models import STAGES as DSTAGES
        from outreach.models import STAGES as OSTAGES

        today = timezone.localdate()
        targets = [("outreach", outreach, [k for k, _ in OSTAGES]), ("deals", deals, [k for k, _ in DSTAGES]),
                   ("deal_investors", entries, None), ("investors", investors, None)]
        batch, made = [], 0
        for i in range(n):
            module, ids, stages = rng.choices(targets, [4, 3, 2, 1])[0]
            if stages and rng.random() < 0.4:
                a = rng.randrange(len(stages) - 1)
                batch.append(Note(module=module, record_id=rng.choice(ids), kind="stage",
                                  date=today - dt.timedelta(days=rng.randint(0, 700)),
                                  text=f"Moved to {stages[a + 1]}",
                                  meta={"from_stage": stages[a], "to_stage": stages[a + 1], "from_status": "open",
                                        "to_status": "open"}, created_by=rng.choice(users)))
            else:
                batch.append(Note(module=module, record_id=rng.choice(ids), kind=rng.choice(["note", "call", "meeting", "email"]),
                                  date=today - dt.timedelta(days=rng.randint(0, 700)),
                                  text="Discussed the deal, next steps agreed. " * rng.randint(1, 6),
                                  created_by=rng.choice(users)))
            if len(batch) == 10000:
                Note.objects.bulk_create(batch)
                made += len(batch)
                batch = []
        Note.objects.bulk_create(batch)
        made += len(batch)
        self.log(f"{made} log entries")

    def tasks(self, rng, n, outreach, deals, investors, users):
        from tasks.models import Task

        today = timezone.localdate()
        rows = []
        for i in range(n):
            kind = rng.randrange(3)
            rows.append(Task(title=f"Follow up {i}", due=today + dt.timedelta(days=rng.randint(-20, 40)),
                             priority=rng.choice(["low", "medium", "high"]),
                             status=rng.choices(["open", "done"], [2, 1])[0], assigned_to=rng.choice(users),
                             outreach_id=rng.choice(outreach) if kind == 0 else None,
                             deal_id=rng.choice(deals) if kind == 1 else None,
                             investor_id=rng.choice(investors) if kind == 2 else None,
                             created_by=rng.choice(users), updated_by=users[0]))
        Task.objects.bulk_create(rows, batch_size=5000)
        self.log(f"{len(rows)} tasks")

    def documents(self, rng, n, deals, companies, users):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from vault.models import Document

        rows = []
        for i in range(n):
            body = f"%PDF-1.4\n% made-up document {i}\n".encode() + os.urandom(64).hex().encode()
            name = default_storage.save(f"vault/load/{i}.pdf", ContentFile(body))
            rows.append(Document(title=f"Document {i}", file=name, file_name=f"Document {i}.pdf", file_type="PDF",
                                 file_size=len(body), content_type="application/pdf",
                                 sha256=hashlib.sha256(body).hexdigest(), created_by=rng.choice(users),
                                 updated_by=users[0]))
        with transaction.atomic():
            Document.objects.bulk_create(rows, batch_size=2000)
        ids = list(Document.objects.values_list("pk", flat=True))
        t = Document.deals.through
        t.objects.bulk_create([t(document_id=pk, deal_id=rng.choice(deals)) for pk in ids], batch_size=5000)
        self.log(f"{len(ids)} Vault documents")
