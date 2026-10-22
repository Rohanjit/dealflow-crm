"""Stuck alerts and follow-up nudges for the Reminders bell (decision 59).

Who hears about a record: its lead banker and deal team (MDs, VPs, associates, analysts) for deals,
its lead banker for outreach attempts; records with nobody assigned go to admins. Only records the
person can see (role and restricted deals). Four kinds:

- stuck:    an open record in one stage longer than the firm usually takes (core/stuck.py)
- quiet:    an open record with no activity (edits, log entries, tracker moves) for N days
            (not repeated for records already shown as stuck)
- tracker:  investors on a deal's tracker that haven't moved for N days, one line per deal
- meeting:  a meeting or call the person logged without a task added for that record since
            (after a two-hour grace period, for 14 days)

Each can be snoozed by the person for 7 days; a meeting nudge can be set aside for good
("No follow-up needed"). A stuck snooze ends early if the record changes stage.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Max, Q
from django.utils import timezone

from . import records, stuck
from .models import AlertSnooze, FirmSettings, Note
from .registry import get_module
from .records import subtitle_for

SHOWN = 20
SNOOZE_DAYS = 7
MEETING_GRACE = dt.timedelta(hours=2)
MEETING_WINDOW = dt.timedelta(days=14)
FOREVER = dt.date(9999, 12, 31)
KINDS = ("stuck", "quiet", "tracker", "meeting")
TEAM = ("team_md", "team_vp", "team_associate", "team_analyst")


def team_ids(module: str, obj) -> set[int]:
    ids = {obj.lead_banker_id} if obj.lead_banker_id else set()
    if module == "deals":
        for rel in TEAM:
            ids |= {u.pk for u in getattr(obj, rel).all()}
    return ids


def is_mine(module: str, obj, user) -> bool:
    ids = team_ids(module, obj)
    return user.pk in ids or (not ids and user.is_admin)


def _mine_q(module: str, user) -> Q:
    """In the database, the same test as is_mine: on the record's team, or an admin and the record
    has nobody on it. Keeps the bell from loading every record in the firm."""
    from deals.models import Deal

    if module == "deals":
        team = Q(lead_banker=user)
        for rel in TEAM:
            team |= Q(**{rel: user})
        cond = Q(pk__in=Deal.objects.filter(team).values("pk"))
        if user.is_admin:
            nobody = Q(lead_banker__isnull=True)
            for rel in TEAM:
                nobody &= Q(**{f"{rel}__isnull": True})
            cond |= Q(pk__in=Deal.objects.filter(nobody).values("pk"))
        return cond
    cond = Q(lead_banker=user)
    if user.is_admin:
        cond |= Q(lead_banker__isnull=True)
    return cond


def _base(module: str, user, mine: bool = False):
    mod = get_module(module)
    qs = mod.model.objects.filter(archived_at__isnull=True)
    if mine:
        qs = qs.filter(_mine_q(module, user))
    if module == "deals":
        qs = qs.prefetch_related(*TEAM).select_related("company")
    elif module == "outreach":
        qs = qs.select_related("company")
    return records.visible(qs, mod, user)


def _snoozes(user, today) -> set[tuple[str, str, int]]:
    return set(AlertSnooze.objects.filter(user=user, until__gte=today).values_list("kind", "module", "record_id"))


def _ref(module: str, obj) -> dict:
    mod = get_module(module)
    return {"module": module, "id": obj.pk, "title": mod.title_of(obj),
            "subtitle": subtitle_for(mod, obj)}


def for_user(user) -> dict:
    fs = FirmSettings.load()
    today = timezone.localdate()
    now = timezone.now()
    snoozed = _snoozes(user, today)
    out = {k: [] for k in KINDS}
    counts = dict.fromkeys(KINDS, 0)
    stuck_ids: dict[str, set[int]] = {}

    for module in stuck.PIPELINES:
        if not user.can(module, "view"):
            continue
        p = get_module(module).pipeline
        # ---- stuck
        if fs.stuck_alerts and (q := stuck.stuck_q(module, fs, today)) is not None:
            by_stage = {r["stage"]: r for r in stuck.rules(module, fs)}
            for obj in _base(module, user, mine=True).filter(q):
                if not is_mine(module, obj, user):
                    continue
                stage = getattr(obj, p.stage_field)
                stuck_ids.setdefault(module, set()).add(obj.pk)
                if (f"stuck:{stage}", module, obj.pk) in snoozed:
                    continue
                r = by_stage[stage]
                out["stuck"].append({
                    "_ref": (module, obj), "stage": stage, "stage_label": r["label"],
                    "days": stuck.days_in_stage(obj, module, today), "flag_after": r["days"],
                    "usual": r["median"], "source": r["source"],
                })
        # ---- quiet
        if fs.nudge_quiet_days:
            cut = now - dt.timedelta(days=fs.nudge_quiet_days)
            qs = (_base(module, user, mine=True).filter(**{p.status_field: p.open_value}, updated_at__lt=cut)
                  .exclude(**{p.stage_field: p.won}))
            if module == "deals":
                qs = qs.annotate(_tracker=Max("tracker__updated_at"))
            cands = [o for o in qs if o.pk not in stuck_ids.get(module, set()) and is_mine(module, o, user)]
            last_note = dict(
                Note.objects.filter(module=module, record_id__in=[o.pk for o in cands])
                .values("record_id").annotate(last=Max("created_at")).values_list("record_id", "last")
            )
            for o in cands:
                last = max(x for x in (o.updated_at, last_note.get(o.pk), getattr(o, "_tracker", None)) if x)
                if last >= cut or ("quiet", module, o.pk) in snoozed:
                    continue
                out["quiet"].append({"_ref": (module, o), "days": (now - last).days})

    # ---- tracker investors not moving
    if fs.nudge_tracker_days and user.can("deals", "view"):
        from deals.models import Deal, DealInvestor

        cut_date = today - dt.timedelta(days=fs.nudge_tracker_days)
        cut = now - dt.timedelta(days=fs.nudge_tracker_days)
        deals = {d.pk: d for d in _base("deals", user, mine=True).filter(status="open").exclude(stage="closed")}
        entries = (
            DealInvestor.objects.filter(archived_at__isnull=True, status="open", deal_id__in=deals, updated_at__lt=cut)
            .exclude(stage="selected").select_related("investor").order_by("stage_entered", "pk")
        )
        noted = set(Note.objects.filter(module="deal_investors", created_at__gte=cut,
                                        record_id__in=entries.values("pk")).values_list("record_id", flat=True))
        groups: dict[int, list] = {}
        for e in entries:
            since = e.stage_entered or timezone.localtime(e.created_at).date()
            if since >= cut_date or e.pk in noted:
                continue
            groups.setdefault(e.deal_id, []).append((e, (today - since).days))
        for deal_id, items in groups.items():
            d: Deal = deals[deal_id]
            if not is_mine("deals", d, user) or ("tracker", "deals", deal_id) in snoozed:
                continue
            out["tracker"].append({
                "_ref": ("deals", d), "count": len(items), "days": max(n for _, n in items),
                "investors": [e.investor.firm_name for e, _ in items[:3]],
            })

    # ---- meetings and calls without a follow-up task
    if fs.nudge_meeting_task and user.can("tasks", "create"):
        from tasks.models import Task

        logged = (
            Note.objects.filter(created_by=user, kind__in=("meeting", "call"),
                                module__in=("outreach", "deals", "deal_investors"),
                                created_at__gte=now - MEETING_WINDOW, created_at__lte=now - MEETING_GRACE)
            .order_by("-created_at", "-id")
        )
        latest: dict[tuple[str, int], Note] = {}
        for n in logged:
            latest.setdefault((n.module, n.record_id), n)
        latest = {k: n for k, n in latest.items() if (f"meeting:{n.pk}", k[0], k[1]) not in snoozed}
        # Load the records and the tasks added since in a few queries, not a few per meeting.
        objs: dict[tuple[str, int], object] = {}
        for module in {m for m, _ in latest}:
            if user.can(module, "view"):
                ids = [rid for m, rid in latest if m == module]
                for obj in _base(module, user).filter(pk__in=ids):
                    objs[(module, obj.pk)] = obj
        since = min((n.created_at for n in latest.values()), default=now)
        deal_ids = {o.deal_id for (m, _), o in objs.items() if m == "deal_investors"} | {
            rid for (m, rid) in objs if m == "deals"}
        outreach_ids = {rid for (m, rid) in objs if m == "outreach"}
        investor_ids = {o.investor_id for (m, _), o in objs.items() if m == "deal_investors"}
        # Newest task per thing it can be about, so each meeting is one lookup.
        newest: dict[tuple, object] = {}
        for at, deal_id, outreach_id, investor_id in Task.objects.filter(created_at__gte=since).filter(
                Q(deal_id__in=deal_ids) | Q(outreach_id__in=outreach_ids) | Q(investor_id__in=investor_ids)
        ).values_list("created_at", "deal_id", "outreach_id", "investor_id"):
            keys = [("outreach", outreach_id)] if outreach_id else []
            if deal_id:
                keys += [("deal", deal_id), ("deal+investor", deal_id, investor_id)]
            elif investor_id:
                keys.append(("investor only", investor_id))
            for k in keys:
                if k not in newest or at > newest[k]:
                    newest[k] = at

        def task_since(key, at) -> bool:
            return key in newest and newest[key] >= at

        for (module, rid), n in latest.items():
            obj = objs.get((module, rid))
            if obj is None:
                continue
            if module == "deal_investors":
                has_task = (task_since(("deal+investor", obj.deal_id, obj.investor_id), n.created_at)
                            or task_since(("deal+investor", obj.deal_id, None), n.created_at)
                            or task_since(("investor only", obj.investor_id), n.created_at))
                related = f"deals:{obj.deal_id}"
            else:
                has_task = task_since(("deal" if module == "deals" else "outreach", rid), n.created_at)
                related = f"{module}:{rid}"
            if has_task:
                continue
            out["meeting"].append({
                "_ref": (module, obj), "note": n.pk, "kind": n.kind, "date": n.date.isoformat(),
                "days": (today - timezone.localtime(n.created_at).date()).days, "related": related,
            })

    out["stuck"].sort(key=lambda x: -(x["days"] - x["flag_after"]))
    out["quiet"].sort(key=lambda x: -x["days"])
    out["tracker"].sort(key=lambda x: -x["days"])
    for k in KINDS:
        counts[k] = len(out[k])
        # Names and subtitles only for the ones shown (a few queries each).
        out[k] = [{**_ref(*item.pop("_ref")), **item} for item in out[k][:SHOWN]]
    return {**out, "counts": counts, "count": sum(counts.values()),
            "settings": {"stuck": fs.stuck_alerts, "quiet_days": fs.nudge_quiet_days,
                         "tracker_days": fs.nudge_tracker_days, "meeting": fs.nudge_meeting_task}}


def snooze(user, kind: str, module: str, record_id: int, *, forever: bool = False) -> dict:
    """Set aside one alert. kind is one of KINDS; for stuck and meeting the current stage or the
    latest logged meeting is looked up so the snooze ends when that changes."""
    if kind not in KINDS:
        raise ValueError("kind")
    mod = get_module(module)
    obj = _base(module, user).filter(pk=record_id).first() if user.can(module, "view") else None
    if obj is None:
        raise LookupError
    today = timezone.localdate()
    until = FOREVER if forever else today + dt.timedelta(days=SNOOZE_DAYS)
    key = kind
    if kind == "stuck":
        key = f"stuck:{getattr(obj, mod.pipeline.stage_field)}"
    elif kind == "meeting":
        n = (Note.objects.filter(module=module, record_id=record_id, created_by=user, kind__in=("meeting", "call"))
             .order_by("-created_at", "-id").first())
        if n is None:
            raise LookupError
        key = f"meeting:{n.pk}"
    elif forever:
        until = today + dt.timedelta(days=SNOOZE_DAYS)
    AlertSnooze.objects.update_or_create(user=user, kind=key, module=module, record_id=record_id, defaults={"until": until})
    return {"until": until.isoformat()}
