"""Stage rules for pipeline modules (Outreach, Active Deals, each deal's investor tracker).

A record sits at one stage; exits (Lost, On hold, Dropped, Declined…) can happen at any stage and
keep the stage it was at, which is how the exit stage is recorded. Changing the stage reopens a
paused or closed record. Every stage or status change is written to the record's interaction log,
and announced with the `stage_changed` signal (Active Deals listens for mandates won).
"""

from __future__ import annotations

import django.dispatch
from django.utils import timezone

from .registry import ModuleDef

# Sent after a record's stage or status changed and was saved.
# sender = module key; kwargs: record, event ({text, meta}), user, importing.
stage_changed = django.dispatch.Signal()


def _state(instance, p) -> tuple[str | None, str | None]:
    if not instance or not instance.pk:
        return None, None
    return getattr(instance, p.stage_field), getattr(instance, p.status_field)


def _current(instance, name: str):
    if not instance or not instance.pk:
        return None
    return getattr(instance, name)


def resolve(mod: ModuleDef, values: dict, old_stage: str | None, old_status: str | None) -> tuple[str, str]:
    """The stage and status the record will have once `values` are saved."""
    p = mod.pipeline
    stage = values.get(p.stage_field) or old_stage or p.stages[0][0]
    status = values.get(p.status_field) or None
    if not status:
        if old_stage is not None and p.stage_field in values and values[p.stage_field] != old_stage:
            status = p.open_value  # moving a paused or lost record to a stage picks it back up
        else:
            status = old_status or p.open_value
    return stage, status


def validate(mod: ModuleDef, values: dict, instance, importing: bool) -> dict:
    p = mod.pipeline
    errors: dict[str, str] = {}
    old_stage, old_status = _state(instance, p)
    stage, status = resolve(mod, values, old_stage, old_status)
    if p.won and stage == p.won and status != p.open_value:
        errors[p.status_field] = f"{p.stage_label(p.won)} can't also be {p.exit_label(status).lower()}."
    if importing or status == old_status:
        return errors
    if rf := p.reason_fields.get(status):
        reason = values[rf] if rf in values else _current(instance, rf)
        if not reason:
            errors[rf] = "Pick a reason."
    if df := p.date_fields.get(status):
        when = values[df] if df in values else _current(instance, df)
        if not when:
            errors[df] = "Pick a date."
    return errors


def before_save(obj, mod: ModuleDef, values: dict, old: tuple, importing: bool) -> dict | None:
    """Apply stage bookkeeping to `obj` (not yet saved) and describe what happened."""
    p = mod.pipeline
    old_stage, old_status = old
    stage, status = resolve(mod, values, old_stage, old_status)
    setattr(obj, p.stage_field, stage)
    setattr(obj, p.status_field, status)
    stage_changed, status_changed = stage != old_stage, status != old_status
    today = timezone.localdate()
    if stage_changed and not values.get(p.entered_field):
        setattr(obj, p.entered_field, today)
    if status_changed:
        # Exit details that no longer apply are cleared; the log keeps them.
        for st, rf in p.reason_fields.items():
            if st != status:
                setattr(obj, rf, None)
        for st, df in p.date_fields.items():
            if st != status:
                setattr(obj, df, None)
    if old_stage is None:
        text = f"Added at {p.stage_label(stage)}"
        if status != p.open_value:
            text += f", {p.exit_label(status).lower()}"
        return {"text": text + (" (imported)" if importing else ""), "meta": {"to_stage": stage, "to_status": status}}
    if not (stage_changed or status_changed):
        return None
    meta = {"from_stage": old_stage, "to_stage": stage, "from_status": old_status, "to_status": status}
    if status != p.open_value:
        exit_ = p.exit(status)
        text = f"{exit_.done if exit_ else status} at {p.stage_label(stage)}"
        if exit_ and exit_.reason_field:
            reason = getattr(obj, exit_.reason_field)
            if reason is not None:
                text += f" · {reason.label}"
                meta["reason"] = reason.label
        if exit_ and exit_.date_field:
            when = getattr(obj, exit_.date_field)
            if when:
                meta["date"] = when.isoformat()
                meta["date_label"] = mod.field(exit_.date_field).label
    elif old_status != p.open_value:
        old_exit = p.exit(old_status)
        verb = "Resumed" if old_exit and old_exit.paused else "Reopened"
        text = f"{verb} at {p.stage_label(stage)}" if not stage_changed else f"{verb} and moved to {p.stage_label(stage)}"
    elif p.won and stage == p.won:
        text = f"{p.stage_label(stage)} (from {p.stage_label(old_stage)})"
    else:
        text = f"Moved from {p.stage_label(old_stage)} to {p.stage_label(stage)}"
    return {"text": text, "meta": meta}


def record_event(obj, mod: ModuleDef, event: dict, user):
    from .models import Note

    Note.objects.create(
        module=mod.key, record_id=obj.pk, kind="stage", date=timezone.localdate(),
        text=event["text"], meta=event["meta"], created_by=user,
    )
