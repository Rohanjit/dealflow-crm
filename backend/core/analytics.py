"""Win-rate analytics for the Insights page (decision 59). No AI: counts over the firm's own records.

Cohorts: outreach attempts started in the period, deals mandated in it (or added, with no mandate
date), tracker entries added in it. A win rate only counts decided records (won ÷ won + lost;
closed ÷ closed + withdrawn + dropped), so open ones don't drag it down, and every rate comes with
how many records it rests on. "Reached" a stage = at or past it, exits included (an attempt lost at
Proposal sent did reach Proposal sent). Everything goes through records.visible, so restricted deals
(and the outreach they came from) only count for people who can see them. The per-banker table is
only sent to people with "See team performance".
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from decimal import Decimal

from django.utils import timezone

from . import records, stuck
from .models import FirmSettings
from .registry import CURRENCIES, get_module

PERIODS = {"3": 3, "6": 6, "12": 12, "24": 24, "all": 0}
TOP = 8
SYMBOL = dict(CURRENCIES)
# Size bands within one currency: INR in crore, everything else in millions.
BANDS = {"INR": ("Cr", Decimal(10_000_000), [25, 100, 500])}
DEFAULT_BANDS = ("Mn", Decimal(1_000_000), [5, 25, 100])


def since_for(period: str, today: dt.date) -> dt.date | None:
    months = PERIODS.get(period, 12)
    if not months:
        return None
    y, m = divmod(today.month - 1 - months, 12)
    return today.replace(year=today.year + y, month=m + 1, day=min(today.day, 28))


def _rate(won: int, lost: int) -> float | None:
    return round(won / (won + lost), 4) if won + lost else None


def _median(xs: list[int]) -> float | None:
    return statistics.median(xs) if xs else None


def _label(opt) -> str:
    return opt.label if opt is not None else "Not set"


def _breakdown(rows: list[tuple[str, str]]) -> list[dict]:
    """rows = [(group label, outcome)] with outcome won / lost / open / paused → one row per group."""
    groups: dict[str, Counter] = defaultdict(Counter)
    for label, outcome in rows:
        groups[label][outcome] += 1
    items = sorted(groups.items(), key=lambda kv: (-sum(kv[1].values()), kv[0] == "Not set", kv[0]))
    if len(items) > TOP + 1:
        other = Counter()
        for _, c in items[TOP:]:
            other.update(c)
        items = items[:TOP] + [(f"{len(groups) - TOP} others", other)]
    return [{"label": label, "total": sum(c.values()), "won": c["won"], "lost": c["lost"],
             "open": c["open"] + c["paused"], "rate": _rate(c["won"], c["lost"])} for label, c in items]


def _funnel(p, stages: list[str]) -> list[dict]:
    order = [v for v, _ in p.stages]
    idx = Counter(order.index(s) for s in stages if s in order)
    out, reached, prev = [], len(stages), None
    for i, (value, label) in enumerate(p.stages):
        reached -= idx.get(i - 1, 0) if i else 0
        out.append({"stage": value, "label": label, "reached": reached,
                    "from_previous": round(reached / prev, 4) if prev else None})
        prev = reached
    return out


def _size_band(currency: str, value) -> str | None:
    if not currency or value is None:
        return None
    unit, mult, edges = BANDS.get(currency, DEFAULT_BANDS)
    v = Decimal(value) / mult
    sym = SYMBOL.get(currency, currency + " ")
    lo = 0
    for e in edges:
        if v < e:
            return f"{sym}{lo}–{e} {unit}" if lo else f"Under {sym}{e} {unit}"
        lo = e
    return f"{sym}{edges[-1]} {unit} and above"


def _band_order(label: str) -> float:
    digits = "".join(ch if ch.isdigit() or ch == " " else " " for ch in label).split()
    return float(digits[0]) if digits else 0.0


def outreach_stats(user, since: dt.date | None) -> dict:
    mod = get_module("outreach")
    p = mod.pipeline
    qs = records.visible(mod.model.objects.filter(archived_at__isnull=True), mod, user)
    if since:
        qs = qs.filter(created_at__date__gte=since)
    rows = list(qs.select_related("company__sector", "source", "transaction_type", "lost_reason", "lead_banker"))

    def outcome(o) -> str:
        if o.status == "lost":
            return "lost"
        if o.status != p.open_value:
            return "paused"
        return "won" if o.stage == p.won else "open"

    outs = [outcome(o) for o in rows]
    c = Counter(outs)
    to_mandate = [
        (o.stage_entered - timezone.localtime(o.created_at).date()).days
        for o, oc in zip(rows, outs) if oc == "won" and o.stage_entered
    ]
    # Size: the firm's main currency only (amounts in different currencies aren't compared).
    currencies = Counter(o.expected_size_currency for o in rows if o.expected_size_currency and o.expected_size_value is not None)
    main = currencies.most_common(1)[0][0] if currencies else None
    size_rows = [(b, oc) for o, oc in zip(rows, outs)
                 if o.expected_size_currency == main and (b := _size_band(main, o.expected_size_value))]
    by_size = sorted(_breakdown(size_rows), key=lambda r: _band_order(r["label"]))
    return {
        "total": len(rows), "won": c["won"], "lost": c["lost"], "open": c["open"], "paused": c["paused"],
        "win_rate": _rate(c["won"], c["lost"]),
        "funnel": _funnel(p, [o.stage for o in rows]),
        "median_days_to_mandate": _median(to_mandate), "mandates_timed": len(to_mandate),
        "by": {
            "source": _breakdown([(_label(o.source), oc) for o, oc in zip(rows, outs)]),
            "sector": _breakdown([(_label(o.company.sector), oc) for o, oc in zip(rows, outs)]),
            "transaction_type": _breakdown([(_label(o.transaction_type), oc) for o, oc in zip(rows, outs)]),
            "size": by_size,
        },
        "size_currency": main,
        "size_other_currencies": sum(n for cur, n in currencies.items() if cur != main),
        "lost_reasons": [{"label": k, "count": n} for k, n in
                         Counter(_label(o.lost_reason) for o, oc in zip(rows, outs) if oc == "lost").most_common(TOP)],
        "_rows": list(zip(rows, outs)),
    }


def deal_stats(user, since: dt.date | None) -> dict:
    mod = get_module("deals")
    p = mod.pipeline
    qs = records.visible(mod.model.objects.filter(archived_at__isnull=True), mod, user)
    rows = list(qs.select_related("company__sector", "source", "transaction_type", "lead_banker"))
    if since:
        rows = [d for d in rows if (d.mandate_date or timezone.localtime(d.created_at).date()) >= since]

    def outcome(d) -> str:
        if d.status in ("withdrawn", "dropped"):
            return "lost"
        if d.status != p.open_value:
            return "paused"
        return "won" if d.stage == p.won else "open"

    outs = [outcome(d) for d in rows]
    c = Counter(outs)
    status = Counter(d.status for d in rows)
    to_close = []
    for d, oc in zip(rows, outs):
        end = d.closing_date or d.stage_entered
        if oc == "won" and d.mandate_date and end and end >= d.mandate_date:
            to_close.append((end - d.mandate_date).days)
    return {
        "total": len(rows), "closed": c["won"], "open": c["open"], "on_hold": c["paused"],
        "withdrawn": status["withdrawn"], "dropped": status["dropped"],
        "close_rate": _rate(c["won"], c["lost"]),
        "funnel": _funnel(p, [d.stage for d in rows]),
        "median_days_to_close": _median(to_close), "closes_timed": len(to_close),
        "by": {
            "transaction_type": _breakdown([(_label(d.transaction_type), oc) for d, oc in zip(rows, outs)]),
            "sector": _breakdown([(_label(d.company.sector), oc) for d, oc in zip(rows, outs)]),
            "source": _breakdown([(_label(d.source), oc) for d, oc in zip(rows, outs)]),
        },
        "_rows": list(zip(rows, outs)),
    }


def investor_stats(user, since: dt.date | None) -> dict:
    """How investors respond on deal trackers: reached NDA signed or further = engaged."""
    mod = get_module("deal_investors")
    p = mod.pipeline
    order = [v for v, _ in p.stages]
    nda, ts = order.index("nda_signed"), order.index("term_sheet_received")
    qs = records.visible(
        mod.model.objects.filter(archived_at__isnull=True, deal__archived_at__isnull=True), mod, user,
    ).select_related("investor")
    if since:
        qs = qs.filter(created_at__date__gte=since)
    per: dict[int, dict] = {}
    total = engaged_all = 0
    for e in qs:
        if e.investor.archived_at:
            continue
        i = order.index(e.stage) if e.stage in order else 0
        r = per.setdefault(e.investor_id, {"id": e.investor_id, "name": e.investor.firm_name, "deals": 0, "engaged": 0,
                                           "term_sheets": 0, "selected": 0})
        r["deals"] += 1
        total += 1
        if i >= nda:
            r["engaged"] += 1
            engaged_all += 1
        if i >= ts:
            r["term_sheets"] += 1
        if e.stage == p.won and e.status == p.open_value:
            r["selected"] += 1
    rows = [dict(r, rate=round(r["engaged"] / r["deals"], 4)) for r in per.values() if r["deals"] >= 2]
    rows.sort(key=lambda r: (-r["rate"], -r["deals"], r["name"]))
    return {"entries": total, "engaged": engaged_all, "rate": round(engaged_all / total, 4) if total else None,
            "top": rows[:10], "investors": len(per)}


def banker_stats(outreach: dict | None, deals: dict | None) -> list[dict]:
    per: dict[int | None, dict] = {}

    def row(u):
        key = u.pk if u else None
        return per.setdefault(key, {"id": key, "name": u.full_name if u else "No lead banker", "outreach": 0, "won": 0,
                                    "lost": 0, "deals": 0, "closed": 0, "deals_lost": 0})

    for o, oc in (outreach or {}).get("_rows", []):
        r = row(o.lead_banker)
        r["outreach"] += 1
        r["won"] += oc == "won"
        r["lost"] += oc == "lost"
    for d, oc in (deals or {}).get("_rows", []):
        r = row(d.lead_banker)
        r["deals"] += 1
        r["closed"] += oc == "won"
        r["deals_lost"] += oc == "lost"
    out = [dict(r, win_rate=_rate(r["won"], r["lost"]), close_rate=_rate(r["closed"], r["deals_lost"])) for r in per.values()]
    out.sort(key=lambda r: (r["id"] is None, -(r["won"] + r["closed"]), -(r["outreach"] + r["deals"]), r["name"]))
    return out


def for_user(user, period: str = "12") -> dict:
    today = timezone.localdate()
    since = since_for(period, today)
    fs = FirmSettings.load()
    see_o, see_d = user.can("outreach", "view"), user.can("deals", "view")
    o = outreach_stats(user, since) if see_o else None
    d = deal_stats(user, since) if see_d else None
    team = user.has_flag("see_team_performance")
    out = {
        "period": period if period in PERIODS else "12",
        "since": since.isoformat() if since else None,
        "today": today.isoformat(),
        "outreach": o,
        "deals": d,
        "investors": investor_stats(user, since) if see_d and user.can("investors", "view") else None,
        "stages": {m: stuck.rules(m, fs) for m, ok in (("outreach", see_o), ("deals", see_d)) if ok},
        "can_see_team": bool(team),
        "bankers": banker_stats(o, d) if team else None,
    }
    for part in (o, d):
        if part:
            part.pop("_rows", None)
    # Breakdowns by a field this person's role can't see are left out (decision 63, S3).
    from . import access

    for part, mkey, size_key in ((o, "outreach", "expected_size"), (d, "deals", "target_raise")):
        if not part:
            continue
        no = access.hidden(get_module(mkey), user)
        for by_key, field_key in (("source", "source"), ("sector", "sector"), ("transaction_type", "transaction_type"),
                                  ("size", size_key)):
            if field_key in no and by_key in part["by"]:
                part["by"][by_key] = None
        if size_key in no:
            part["size_currency"], part["size_other_currencies"] = None, 0
        if "lead_banker" in no:
            out["bankers"] = None
    return out
