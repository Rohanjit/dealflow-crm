"""Relationships going cold: investors and contacts nobody has been in touch with for a while.

A record is going cold when its "Last contacted" date is more than N days ago, or, with no date,
when it was added more than N days ago. N is set per module in Settings > Firm (0 switches the
alerts off). Each relationship owner sees theirs in Reminders; records with no owner go to admins.
"Contacted today" sets Last contacted to today; "Snooze 30 days" hides the alert for a month.
The "Going cold" button on the Investors and Contacts lists shows every one of them, snoozed or not.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Q
from django.utils import timezone

from .models import FirmSettings
from .registry import enabled_map, get_module
from .records import subtitle_for

SETTINGS = {"investors": "stale_investor_days", "contacts": "stale_contact_days"}
SNOOZE_DAYS = 30
SHOWN = 20  # per module in Reminders; the list page has the rest

QUICK_FILTER = {
    "param": "stale", "value": "1", "label": "Going cold",
    "help": "Not contacted for a while (set the number of days in Settings > Firm).",
}


def days_for(module: str, fs: FirmSettings | None = None) -> int:
    """Days without contact before a record counts as going cold; 0 when switched off (including
    when the Last contacted field itself is switched off)."""
    if module not in SETTINGS or not enabled_map(module).get("last_contacted", True):
        return 0
    return getattr(fs or FirmSettings.load(), SETTINGS[module])


def cold_q(days: int, today: dt.date | None = None) -> Q:
    cut = (today or timezone.localdate()) - dt.timedelta(days=days)
    return Q(last_contacted__lt=cut) | Q(last_contacted__isnull=True, created_at__date__lt=cut)


def make_filter(module: str, then=None):
    """extra_filters for a module: ?stale=1 keeps only records going cold (then the module's own)."""

    def apply(qs, params, user=None):
        if params.get("stale") == "1":
            days = days_for(module)
            qs = qs.filter(cold_q(days)) if days else qs.none()
        return then(qs, params, user) if then else qs

    return apply


def _item(module: str, obj, today: dt.date) -> dict:
    mod = get_module(module)
    since = obj.last_contacted or timezone.localtime(obj.created_at).date()
    return {
        "module": module,
        "id": obj.pk,
        "title": mod.title_of(obj),
        "subtitle": subtitle_for(mod, obj),
        "last_contacted": obj.last_contacted.isoformat() if obj.last_contacted else None,
        "days": (today - since).days,
        "unowned": obj.relationship_owner_id is None,
    }


def for_user(user) -> dict:
    """Going-cold relationships to remind this person about."""
    fs = FirmSettings.load()
    today = timezone.localdate()
    out = {"items": [], "count": 0, "more": {}, "days": {}}
    for module in SETTINGS:
        days = days_for(module, fs)
        out["days"][module] = days
        from . import access

        if not days or not user.can(module, "view") or not access.can_see(module, "last_contacted", user):
            continue
        mine = Q(relationship_owner=user)
        if user.is_admin:
            mine |= Q(relationship_owner__isnull=True)
        qs = (
            get_module(module).model.objects.filter(archived_at__isnull=True)
            .filter(cold_q(days, today)).filter(mine)
            .exclude(stale_snoozed_until__gte=today)
            .order_by("last_contacted", "created_at", "pk")
        )
        total = qs.count()
        out["items"] += [_item(module, o, today) for o in qs[:SHOWN]]
        out["count"] += total
        if total > SHOWN:
            out["more"][module] = total - SHOWN
    return out
