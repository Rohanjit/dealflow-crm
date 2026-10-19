"""Sample data for guests: a small, believable advisory firm with every module in use, so someone
can walk through Dealflow without setting anything up. Dates are relative to today, so overdue
tasks, mandates nearing expiry and deals near close always show. Loaded by core/guest.py."""

from __future__ import annotations

import datetime as dt
import hashlib

from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

GUEST_EMAIL = "guest@example.com"
GUEST_PIN = "2580"
FIRM_NAME = "Northbridge Capital (sample)"


def _pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with a title and a few lines of text (enough to open and read in the Vault)."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text = ["BT /F1 20 Tf 60 770 Td (" + esc(title) + ") Tj ET"]
    y = 735
    for line in lines:
        text.append(f"BT /F1 11 Tf 60 {y} Td (" + esc(line) + ") Tj ET")
        y -= 18
    text.append("BT /F1 9 Tf 60 60 Td (Sample document for the Dealflow guest tour. Not a real company.) Tj ET")
    stream = "\n".join(text).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@transaction.atomic
def load():
    """Create the sample firm. Expects an empty install (roles and dropdown lists from migrations only)."""
    from accounts.models import Role, User
    from core.models import ChoiceOption, FirmSettings, Note
    from core.records import apply_values, clean_values
    from core.registry import enabled_map, get_module
    from vault.files import KINDS
    from vault.models import Document

    admin_role = Role.objects.get(is_admin=True)
    member = Role.objects.get(name="Member")
    guest = User.objects.create_user(GUEST_EMAIL, None, full_name="Guest", role=admin_role)
    guest.vault_pin = make_password(GUEST_PIN)
    guest.save(update_fields=["vault_pin"])
    asha = User.objects.create_user("asha@example.com", None, full_name="Asha Rao", role=admin_role)
    vikram = User.objects.create_user("vikram@example.com", None, full_name="Vikram Shah", role=member)
    anu = User.objects.create_user("anu@example.com", None, full_name="Anu Menon", role=member)

    fs = FirmSettings.load()
    fs.firm_name = FIRM_NAME
    fs.backups_enabled = False
    fs.save()

    today = timezone.localdate()

    def day(n: int) -> str:
        return (today + dt.timedelta(days=n)).isoformat()

    def opt(lst: str, label: str) -> int:
        return ChoiceOption.objects.get(choice_list__key=lst, label=label).pk

    def opts(lst: str, labels: list[str]) -> list[int]:
        return [opt(lst, x) for x in labels]

    def money(a, cur="INR", unit="Cr"):
        return {"amount": str(a), "currency": cur, "unit": unit}

    def make(module: str, data: dict, by=guest):
        mod = get_module(module)
        values, errors = clean_values(mod, data, enabled=enabled_map(module))
        assert not errors, (module, errors)
        return apply_values(mod.model(), mod, values, by)

    def note(module: str, rec, kind: str, days_ago: int, text: str, by=guest):
        Note.objects.create(module=module, record_id=rec.pk, kind=kind, date=today - dt.timedelta(days=days_ago),
                            text=text, created_by=by)

    # ------------------------------------------------------------------ investors
    INV = [
        # name, type, city, sectors, stages, geographies, instrument, deploying, min, max, owner, strength
        ("Blume Ventures", "VC", "Mumbai", ["Consumer / D2C", "Technology / SaaS"], ["Early stage (Series A/B)", "Growth"], ["India"], "equity", "yes", 10, 60, asha, "strong"),
        ("Peak XV Partners", "VC", "Bengaluru", ["Consumer / D2C", "Fintech", "Technology / SaaS"], ["Growth", "Late stage"], ["South Asia", "Southeast Asia"], "equity", "yes", 75, 400, vikram, "warm"),
        ("Kotak Alt Credit", "NBFC / debt fund", "Mumbai", ["Consumer / D2C", "Manufacturing", "Logistics"], ["Growth", "Late stage"], ["India"], "debt", "yes", 50, 300, guest, "warm"),
        ("Motilal Oswal PE", "PE", "Mumbai", ["Consumer / D2C", "Financial services"], ["Growth", "Pre-IPO"], ["India"], "equity", "unknown", 40, 200, asha, "cold"),
        ("A91 Partners", "PE", "Mumbai", ["Consumer / D2C", "Healthcare"], ["Growth"], ["India"], "both", "yes", 25, 150, vikram, "strong"),
        ("Iron Bridge Capital", "PE", "New York", ["Industrials", "Logistics"], ["Buyout"], ["US", "Global"], "equity", "yes", 500, 2000, None, None),
        ("Sahyadri Family Office", "Family office", "Pune", ["Manufacturing", "Agritech & food", "Consumer / D2C"], ["Growth", "Late stage"], ["India"], "equity", "yes", 15, 80, guest, "warm"),
        ("Gulf Horizon Investments", "Family office", "Dubai", ["Healthcare", "Logistics", "Real estate"], ["Late stage", "Pre-IPO"], ["Middle East", "India"], "both", "yes", 100, 600, anu, "cold"),
        ("Tiny Seed Club", "VC", "Bengaluru", ["Technology / SaaS"], ["Seed"], ["India"], "equity", "no", 0.5, 2, None, None),
        ("Lighthouse Pharma Fund", "AIF", "Hyderabad", ["Pharma & life sciences", "Healthcare"], ["Growth", "Late stage"], ["India", "Global"], "equity", "yes", 30, 250, anu, "warm"),
    ]
    investors = {}
    for n, (name, typ, city, sectors, stages, geos, inst, deploying, lo, hi, owner, strength) in enumerate(INV):
        d = {
            "firm_name": name, "investor_type": opt("investor_types", typ), "hq_city": city,
            "sectors": opts("sectors", sectors), "stages": opts("investment_stages", stages),
            "geography_focus": opts("geographies", geos), "instrument": inst, "actively_deploying": deploying,
            "min_ticket": money(lo), "max_ticket": money(hi),
            "website": "https://example.com/" + name.lower().split()[0],
        }
        if owner:
            d["relationship_owner"] = owner.pk
        if strength:
            d["relationship_strength"] = strength
            # Every third one hasn't been contacted in months, so Reminders shows some going cold.
            d["last_contacted"] = day(-(100 + len(name) * 3)) if n % 3 == 1 else day(-(7 + len(name) % 20))
        investors[name] = make("investors", d)

    # ------------------------------------------------------------------ companies
    COMPANIES = [
        ("Acme Foods Pvt Ltd", "Agritech & food", "Pune", "India", 420, 46, "Packaged snacks and ready-to-cook foods sold across 18 states."),
        ("Orbit Logistics", "Logistics", "Chennai", "India", 610, 72, "Cold-chain trucking and warehousing for pharma and FMCG."),
        ("Nova Retail", "Retail", "Bengaluru", "India", 280, 21, "Neighbourhood grocery chain with 140 stores in South India."),
        ("Zeta Pharma", "Pharma & life sciences", "Hyderabad", "India", 350, 64, "Speciality generics for regulated markets."),
        ("Kite Learning", "Edtech", "Delhi", "India", 95, -8, "Test-prep platform for engineering and medical entrance exams."),
        ("Verdant Energy", "Energy & renewables", "Ahmedabad", "India", 510, 88, "Rooftop solar for factories and warehouses."),
        ("Brightpath Clinics", "Healthcare", "Mumbai", "India", 160, 19, "Chain of 22 day-care surgery clinics."),
        ("Coastline Seafoods", "Agritech & food", "Kochi", "India", 230, 25, "Frozen seafood exporter to Japan and the EU."),
        ("Halcyon Hotels", "Real estate", "Goa", "India", 140, 38, "Boutique resort chain with seven properties on the west coast."),
    ]
    companies = {}
    for name, sector, city, geo, rev, ebitda, desc in COMPANIES:
        companies[name] = make("companies", {
            "name": name, "sector": opt("sectors", sector), "city": city, "geography": opt("geographies", geo),
            "financials_fy": "FY26", "revenue": money(rev), "ebitda": money(ebitda), "description": desc,
            "website": "https://example.com/" + name.lower().split()[0],
        })

    def co(name):
        return f"companies:{companies[name].pk}"

    # ------------------------------------------------------------------ contacts
    CONTACTS = [
        ("Ravi Kulkarni", "CFO", co("Acme Foods Pvt Ltd"), "Partner / MD", True, "ravi@acmefoods.example", "+91 98200 11223"),
        ("Meera Iyer", "Founder & CEO", co("Orbit Logistics"), "Partner / MD", True, "meera@orbit.example", "+91 98410 55667"),
        ("Karan Malhotra", "Partner", f"investors:{investors['Blume Ventures'].pk}", "Partner / MD", True, "karan@blume.example", "+91 99300 44556"),
        ("Priya Nair", "Vice President", f"investors:{investors['Peak XV Partners'].pk}", "Principal / VP", True, "priya@peakxv.example", "+91 98860 77889"),
        ("Sameer Desai", "Director, Credit", f"investors:{investors['Kotak Alt Credit'].pk}", "Principal / VP", True, "sameer@kotakalt.example", "+91 98190 33445"),
        ("Fatima Al Mansoori", "Investment Director", f"investors:{investors['Gulf Horizon Investments'].pk}", "Principal / VP", True, "fatima@gulfhorizon.example", "+971 50 123 4567"),
        ("Arjun Mehta", "Head of Finance", co("Nova Retail"), "Principal / VP", True, "arjun@novaretail.example", "+91 99000 12121"),
        ("Dr. Lakshmi Rao", "Managing Director", co("Zeta Pharma"), "Partner / MD", True, "lakshmi@zetapharma.example", "+91 98480 23232"),
        ("Neha Kapoor", "Associate", f"investors:{investors['A91 Partners'].pk}", "Associate / Analyst", False, "neha@a91.example", "+91 98110 90909"),
        ("Rohit Sinha", "Partner, Khaitan & Associates (legal)", None, "Other", False, "rohit@lawfirm.example", "+91 98100 45454"),
    ]
    contacts = {}
    for i, (name, title, firm, level, primary, email, mobile) in enumerate(CONTACTS):
        d = {"full_name": name, "designation": title, "decision_level": opt("decision_maker_levels", level),
             "primary_contact": primary, "work_email": email, "mobile": mobile,
             "relationship_strength": ["warm", "strong", "cold"][i % 3], "relationship_owner": [guest, asha, vikram][i % 3].pk,
             "birthday": f"{(i % 12) + 1:02d}-{(i * 3) % 27 + 1:02d}",
             "last_contacted": day(-(70 + i * 9)) if i % 4 == 0 else day(-(3 + i * 2))}
        if firm:
            d["firm"] = firm
        if name.startswith("Rohit"):
            d["introduced_by"] = "Asha Rao"
        contacts[name] = make("contacts", d)

    # ------------------------------------------------------------------ outreach (winning mandates)
    def outreach(company, stage, **kw):
        return make("outreach", {"company": co(company), "stage": stage, **kw})

    o_acme = outreach("Acme Foods Pvt Ltd", "negotiating", transaction_type=opt("transaction_types", "Equity raise"),
                      expected_size=money(150), proposed_retainer=money(25, unit="Lakh"), proposed_success_fee="2.5",
                      lead_banker=guest.pk, source=opt("outreach_sources", "Referral"), next_follow_up=day(2),
                      expected_timeline="Close by Q2 FY27", tags=["priority"])
    note("outreach", o_acme, "meeting", 12, "Met Ravi and the promoters at their Pune office. They want growth capital for two new plants.")
    note("outreach", o_acme, "email", 5, "Sent the engagement proposal: ₹25 L retainer + 2.5% success fee.")
    note("outreach", o_acme, "call", 1, "Ravi asked for a lower retainer. Agreed to discuss on Friday.")
    o_kite = outreach("Kite Learning", "meeting", transaction_type=opt("transaction_types", "Equity raise"),
                      expected_size=money(60), lead_banker=vikram.pk, source=opt("outreach_sources", "Inbound"), next_follow_up=day(-3))
    note("outreach", o_kite, "meeting", 9, "Intro meeting with the founders. Burn is high; they need a bridge round first.", by=vikram)
    outreach("Halcyon Hotels", "proposal", transaction_type=opt("transaction_types", "Debt raise"), expected_size=money(220),
             proposed_success_fee="1.5", lead_banker=asha.pk, source=opt("outreach_sources", "Existing relationship"), next_follow_up=day(6))
    outreach("Brightpath Clinics", "contacted", transaction_type=opt("transaction_types", "Pre-IPO"), expected_size=money(300),
             lead_banker=guest.pk, source=opt("outreach_sources", "Cold outreach"), next_follow_up=day(1))
    outreach("Coastline Seafoods", "identified", transaction_type=opt("transaction_types", "M&A sell-side"), expected_size=money(25, "USD", "Mn"),
             lead_banker=anu.pk, source=opt("outreach_sources", "Event"))
    lost = outreach("Nova Retail", "proposal", transaction_type=opt("transaction_types", "Equity raise"), expected_size=money(90),
                    lead_banker=vikram.pk, status="lost", lost_reason=opt("lost_reasons", "Chose another banker"))
    note("outreach", lost, "note", 20, "They went with a bulge-bracket bank for the equity round. Keep in touch for the debt raise.", by=vikram)
    outreach("Kite Learning", "contacted", transaction_type=opt("transaction_types", "Debt raise"), expected_size=money(20),
             lead_banker=anu.pk, status="on_hold", hold_reason=opt("hold_reasons", "Awaiting financials"), revisit_date=day(21))

    # Mandates won become deals automatically (deals/signals.py), with terms carried over.
    won = []
    for company, kind, size, retainer, fee, banker in [
        ("Orbit Logistics", "Equity raise", money(200), money(40, unit="Lakh"), "2", vikram),
        ("Nova Retail", "Debt raise", money(60), money(15, unit="Lakh"), "1.25", guest),
        ("Zeta Pharma", "M&A sell-side", money(35, "USD", "Mn"), money(30, unit="Lakh"), "3", anu),
        ("Verdant Energy", "Equity raise", money(120), money(20, unit="Lakh"), "2.25", asha),
    ]:
        o = outreach(company, "won", transaction_type=opt("transaction_types", kind), expected_size=size,
                     proposed_retainer=retainer, proposed_success_fee=fee, lead_banker=banker.pk,
                     final_agreed_fees=f"₹{retainer['amount']} L retainer + {fee}% success fee")
        won.append(o)

    # ------------------------------------------------------------------ deals: move them along
    from deals.models import Deal

    deal_api = get_module("deals")

    def update(deal, data, by=guest):
        values, errors = clean_values(deal_api, data, partial=True, instance=deal, enabled=enabled_map("deals"))
        assert not errors, errors
        return apply_values(deal, deal_api, values, by)

    d_orbit, d_nova, d_zeta, d_verdant = (Deal.objects.get(outreach=o) for o in won)
    update(d_orbit, {"stage": "term_sheet", "instrument": "ccps", "investment_stage": opt("investment_stages", "Late stage"),
                     "target_close_date": day(12), "mandate_expiry": day(75), "mandate_date": day(-120), "exclusivity": "exclusive",
                     "team_vp": [vikram.pk], "team_analyst": [anu.pk], "retainer_received": True, "retainer_paid": money(40, unit="Lakh"),
                     "pre_money_valuation": money(1400), "use_of_funds": "Two new cold-chain hubs and fleet expansion."})
    update(d_nova, {"stage": "due_diligence", "instrument": "debt", "target_close_date": day(-4), "mandate_expiry": day(9),
                    "mandate_date": day(-200), "team_associate": [anu.pk]})
    update(d_zeta, {"stage": "first_draft", "mandate_expiry": day(-2), "mandate_date": day(-60), "team_md": [asha.pk]})
    update(d_verdant, {"stage": "investor_outreach", "instrument": "equity", "investment_stage": opt("investment_stages", "Growth"),
                       "target_close_date": day(45), "mandate_expiry": day(150), "team_vp": [vikram.pk], "team_analyst": [anu.pk]})
    note("deals", d_orbit, "meeting", 6, "Peak XV issued a term sheet at ₹1,400 Cr pre-money. Blume still deciding.", by=vikram)
    note("deals", d_orbit, "call", 2, "Meera wants a better liquidation preference. Going back to Peak XV.", by=vikram)
    note("deals", d_nova, "email", 3, "Shared the DD tracker with Kotak Alt Credit's legal counsel.")

    # Investor trackers
    def track(deal, investor, stage, **kw):
        return make("deal_investors", {"deal": f"deals:{deal.pk}", "investor": f"investors:{investors[investor].pk}", "stage": stage, **kw})

    track(d_orbit, "Peak XV Partners", "term_sheet_received")
    track(d_orbit, "Blume Ventures", "meeting_held")
    track(d_orbit, "A91 Partners", "nda_signed")
    track(d_orbit, "Iron Bridge Capital", "teaser_sent", status="declined", decline_reason=opt("decline_reasons", "Ticket size mismatch"))
    track(d_orbit, "Gulf Horizon Investments", "teaser_sent", status="no_response")
    track(d_nova, "Kotak Alt Credit", "term_sheet_received")
    track(d_verdant, "Sahyadri Family Office", "teaser_sent")
    track(d_verdant, "Motilal Oswal PE", "identified")
    track(d_zeta, "Lighthouse Pharma Fund", "identified")

    # A closed deal for the record (and for "Closed" on the home page)
    d_closed = make("deals", {"company": co("Brightpath Clinics"), "transaction_type": opt("transaction_types", "Equity raise"),
                              "stage": "closed", "lead_banker": asha.pk, "target_raise": money(80), "amount_raised": money(85),
                              "closing_date": day(-30), "fee_earned": money(1.9), "mandate_date": day(-260),
                              "lead_investors": [f"investors:{investors['A91 Partners'].pk}"]})
    note("deals", d_closed, "note", 30, "Closed at ₹85 Cr with A91 leading. Success fee invoiced.", by=asha)

    # ------------------------------------------------------------------ tasks (some for the guest, so reminders show)
    now = timezone.localtime()
    later = now + dt.timedelta(hours=2)
    TASKS = [
        ("Send revised teaser to Peak XV", day(-2), "high", "open", guest, f"deals:{d_orbit.pk}"),
        ("Call Ravi about the retainer", f"{today.isoformat()}T{later:%H:%M}" if later.date() == today else today.isoformat(),
         "medium", "open", guest, f"outreach:{o_acme.pk}"),
        ("Review the DD tracker with Kotak's counsel", today.isoformat(), "medium", "in_progress", guest, f"deals:{d_nova.pk}"),
        ("Book Mumbai roadshow meetings for Verdant", day(5), "medium", "open", guest, f"deals:{d_verdant.pk}"),
        ("Share the IM draft with Dr. Rao", day(3), "high", "open", anu, f"deals:{d_zeta.pk}"),
        ("Update investor list for Orbit", day(1), "low", "open", vikram, f"deals:{d_orbit.pk}"),
        ("Plan the Q4 pipeline review", None, "low", "open", guest, None),
        ("Send NDA to A91 Partners", day(-10), "medium", "done", vikram, f"investors:{investors['A91 Partners'].pk}"),
    ]
    for title, due, priority, status, who, related in TASKS:
        d = {"title": title, "priority": priority, "status": status, "assigned_to": who.pk}
        if due:
            d["due"] = due
        if related:
            d["related"] = related
        make("tasks", d, by=asha if who == guest and priority == "high" else guest)

    # ------------------------------------------------------------------ Vault documents
    vault = get_module("vault")
    kind = KINDS["pdf"]
    DOCS = [
        ("Orbit Logistics teaser", "Teaser", [f"deals:{d_orbit.pk}"], ["Cold-chain logistics leader", "FY26 revenue: Rs 610 Cr", "EBITDA margin: 11.8%", "Raising: Rs 200 Cr growth equity"]),
        ("Orbit Logistics engagement letter", "Engagement letter", [f"deals:{d_orbit.pk}"], ["Mandate: exclusive, 9 months", "Retainer: Rs 40 L", "Success fee: 2%"]),
        ("Peak XV term sheet (draft)", "Term sheet", [f"deals:{d_orbit.pk}", f"investors:{investors['Peak XV Partners'].pk}"], ["Pre-money valuation: Rs 1,400 Cr", "Instrument: CCPS", "Board seat: 1"]),
        ("Nova Retail DD tracker", "DD report", [f"deals:{d_nova.pk}"], ["Legal DD: in progress", "Financial DD: complete", "Tax DD: pending documents"]),
        ("Mutual NDA - Kotak Alt Credit", "NDA", [f"investors:{investors['Kotak Alt Credit'].pk}"], ["Parties: Northbridge Capital and Kotak Alt Credit", "Term: 2 years"]),
    ]
    for title, category, tagged, lines in DOCS:
        data = _pdf(title, lines)
        values, errors = clean_values(vault, {"title": title, "category": opt("document_categories", category), "tagged": tagged},
                                      enabled=enabled_map("vault"))
        assert not errors, errors
        name = title.replace(" ", "_") + ".pdf"
        doc = Document(file=ContentFile(data, name=name), file_name=name, file_type=kind.label, file_size=len(data),
                       content_type=kind.content_type, sha256=hashlib.sha256(data).hexdigest())
        doc._ext = "pdf"
        apply_values(doc, vault, values, guest)

    fs.sample_loaded_at = timezone.now()
    fs.save(update_fields=["sample_loaded_at"])
    return guest
