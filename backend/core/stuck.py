"""Stuck alerts (decision 59): an open outreach attempt or deal that has sat in one stage longer
than the firm usually takes there.

"Usually" is learned from the interaction log's stage entries (by the scheduler every few minutes
when there are new stage moves, stored in core.StageStats, so no screen reads the whole log): for every past stay in a stage that
ended with a move to another stage, the number of days it lasted (stays interrupted by On hold,
Lost and the like are left out). With at least MIN_SAMPLES stays, a record is stuck after half as
long again as the median (never sooner than FLOOR days); with fewer, after DEFAULTS. An admin can
set any stage's days in Settings > Alerts (0 = never flag that stage). The final stage (Mandate won,
Closed) is never stuck, and neither are paused or closed records.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
import threading
import time

from django.db.models import Max, Q
from django.utils import timezone

from .models import FirmSettings, Note
from .registry import get_module

PIPELINES = ("outreach", "deals")
MIN_SAMPLES = 5
FACTOR = 1.5
FLOOR = 7
MAX_DAYS = 730

# Days before a stage counts as stuck while the firm has too little history of its own.
DEFAULTS: dict[str, dict[str, int]] = {
    "outreach": {"identified": 14, "contacted": 14, "meeting": 21, "proposal": 21, "negotiating": 30},
    "deals": {
        "data_collection": 21, "first_draft": 14, "pre_finalisation": 14, "finalisation": 14,
        "investors_identified": 14, "investor_outreach": 30, "collaterals": 21, "management_meetings": 30,
        "term_sheet": 30, "due_diligence": 45, "documentation": 30,
    },
}

QUICK_FILTER = {
    "param": "stuck", "value": "1", "label": "Stuck",
    "help": "In the same stage for longer than your firm usually takes (see Settings > Alerts).",
}

# How often the scheduler re-learns a pipeline's stage times when there are new stage moves, and how
# old the figures may get before a screen re-learns them itself (if the scheduler isn't running).
REFRESH_EVERY = dt.timedelta(minutes=10)
STALE_AFTER = dt.timedelta(hours=6)
MEMO_SECONDS = 60  # each worker re-reads the stored figures at most once a minute
_memo: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def durations(module: str) -> dict[str, list[int]]:
    """Days each finished stay in a stage lasted, per stage, from the stage entries in the log.
    Reads the module's whole stage history: only refresh() calls it."""
    notes = Note.objects.filter(module=module, kind="stage")
    p = get_module(module).pipeline
    out: dict[str, list[int]] = {}
    rows = notes.order_by("record_id", "created_at", "id").values_list("record_id", "date", "meta")
    rec = cur = entered = None
    paused = False
    for rid, date, meta in rows.iterator(chunk_size=5000):
        meta = meta if isinstance(meta, dict) else {}
        to_stage, to_status = meta.get("to_stage"), meta.get("to_status")
        if rid != rec:
            rec, cur, entered, paused = rid, None, None, False
        if not to_stage:
            continue
        from_stage = meta.get("from_stage")
        if from_stage is None or cur is None:
            cur, entered = to_stage, date
            paused = bool(to_status and to_status != p.open_value)
            continue
        if to_stage != cur:
            if entered and not paused and from_stage == cur and date >= entered:
                out.setdefault(cur, []).append((date - entered).days)
            cur, entered, paused = to_stage, date, False
        if to_status and to_status != p.open_value:
            paused = True
    return out


def _latest_note_id(module: str) -> int:
    # Uses the (module, kind, id) index: one step, however long the log is.
    return Note.objects.filter(module=module, kind="stage").aggregate(m=Max("id"))["m"] or 0


def refresh(module: str) -> dict:
    """Learn the module's stage times again from the whole log and store them."""
    from .models import StageStats

    last = _latest_note_id(module)
    data = {stage: {"median": statistics.median(days), "samples": len(days)}
            for stage, days in durations(module).items()}
    StageStats.objects.update_or_create(module=module, defaults={
        "data": data, "last_note_id": last, "computed_at": timezone.now()})
    with _lock:
        _memo.pop(module, None)
    return data


def refresh_due(now=None) -> list[str]:
    """Scheduler: re-learn pipelines that have new stage moves, at most every REFRESH_EVERY."""
    from .models import StageStats

    now = now or timezone.now()
    done = []
    for module in PIPELINES:
        row = StageStats.objects.filter(module=module).first()
        if row is None or (row.computed_at <= now - REFRESH_EVERY and _latest_note_id(module) > row.last_note_id):
            refresh(module)
            done.append(module)
    return done


def stats(module: str) -> dict:
    """{stage: {"median", "samples"}} as last learned. Learned on the spot only the first time, or
    when the stored figures are over STALE_AFTER old and out of date (the scheduler isn't running)."""
    from .models import StageStats

    now = time.monotonic()
    with _lock:
        hit = _memo.get(module)
        if hit and now - hit[0] < MEMO_SECONDS:
            return hit[1]
    row = StageStats.objects.filter(module=module).first()
    if row is None or (row.computed_at <= timezone.now() - STALE_AFTER and _latest_note_id(module) > row.last_note_id):
        data = refresh(module)
    else:
        data = row.data
    with _lock:
        _memo[module] = (now, data)
    return data


def forget():
    """Drop this worker's copy (tests, and after a restore)."""
    with _lock:
        _memo.clear()


def rules(module: str, fs: FirmSettings | None = None) -> list[dict]:
    """For each stage (except the final one): what's usual and when a record counts as stuck."""
    fs = fs or FirmSettings.load()
    p = get_module(module).pipeline
    learned = stats(module)
    overrides = (fs.stuck_overrides or {}).get(module) or {}
    out = []
    for value, label in p.stages:
        if value == p.won:
            continue
        seen = learned.get(value) or {}
        n, median = seen.get("samples", 0), seen.get("median")
        default = DEFAULTS.get(module, {}).get(value, 21)
        if value in overrides:
            days, source = int(overrides[value]), "set"
        elif n >= MIN_SAMPLES:
            days, source = max(FLOOR, math.ceil(median * FACTOR)), "learned"
        else:
            days, source = default, "default"
        out.append({
            "stage": value, "label": label, "days": days, "source": source, "default": default,
            "median": median, "samples": n,
            "learned": max(FLOOR, math.ceil(median * FACTOR)) if n >= MIN_SAMPLES else None,
        })
    return out


def stuck_q(module: str, fs: FirmSettings | None = None, today: dt.date | None = None) -> Q | None:
    """Records of the module that are stuck today; None when no stage can be."""
    mod = get_module(module)
    p = mod.pipeline
    today = today or timezone.localdate()
    cond = Q()
    for r in rules(module, fs):
        if r["days"] <= 0:
            continue
        cut = today - dt.timedelta(days=r["days"])
        cond |= Q(**{p.stage_field: r["stage"]}) & (
            Q(**{f"{p.entered_field}__lt": cut})
            | Q(**{f"{p.entered_field}__isnull": True, "created_at__date__lt": cut})
        )
    if not cond:
        return None
    return Q(**{p.status_field: p.open_value}) & cond


def days_in_stage(obj, module: str, today: dt.date | None = None) -> int:
    p = get_module(module).pipeline
    since = getattr(obj, p.entered_field) or timezone.localtime(obj.created_at).date()
    return ((today or timezone.localdate()) - since).days


def make_filter(module: str, then=None):
    """extra_filters for a module: ?stuck=1 keeps only stuck records (then the module's own).
    Works whether or not stuck reminders are switched on."""

    def apply(qs, params, user=None):
        if params.get("stuck") == "1":
            q = stuck_q(module)
            qs = qs.filter(q) if q is not None else qs.none()
        return then(qs, params, user) if then else qs

    return apply


def clean_overrides(data) -> tuple[dict, dict]:
    """Validate {"outreach": {"meeting": 30 | null}} from Settings; returns (overrides, errors)."""
    out: dict[str, dict[str, int]] = {}
    errors: dict[str, str] = {}
    if not isinstance(data, dict):
        return {}, {"stuck_overrides": "The stage days couldn't be read."}
    for module, stages in data.items():
        if module not in PIPELINES or not isinstance(stages, dict):
            errors["stuck_overrides"] = "The stage days couldn't be read."
            continue
        p = get_module(module).pipeline
        known = {v for v, _ in p.stages if v != p.won}
        for stage, days in stages.items():
            if stage not in known:
                errors["stuck_overrides"] = "The stage days couldn't be read."
                continue
            if days is None or days == "":
                continue
            if isinstance(days, bool) or not isinstance(days, (int, float, str)):
                errors[f"{module}.{stage}"] = "Enter a number of days."
                continue
            try:
                n = int(days)
                if n != float(days) or not 0 <= n <= MAX_DAYS:
                    raise ValueError
            except (TypeError, ValueError):
                errors[f"{module}.{stage}"] = f"Pick between 0 (never) and {MAX_DAYS} days."
                continue
            out.setdefault(module, {})[stage] = n
    return out, errors
