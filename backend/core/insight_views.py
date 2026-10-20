"""Insights (decision 59): stuck alerts and follow-up nudges for the Reminders bell, the rules
behind "stuck", win-rate analytics, and Settings > Alerts."""

from __future__ import annotations

from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser, flag_required

from . import analytics, nudges, stuck
from .guest import NotOnDemo
from .models import FirmSettings
from .records import to_id


class InsightAlertsView(APIView):
    def get(self, request):
        return Response(nudges.for_user(request.user))


class InsightSnoozeView(APIView):
    """{kind: stuck | quiet | tracker | meeting, module, id, forever?}: set one alert aside."""

    def post(self, request):
        d = request.data
        kind, module, rid = d.get("kind"), d.get("module"), to_id(d.get("id"))
        allowed = {"stuck": stuck.PIPELINES, "quiet": stuck.PIPELINES, "tracker": ("deals",),
                   "meeting": ("outreach", "deals", "deal_investors")}
        if kind not in allowed or module not in allowed[kind] or rid is None:
            raise ValidationError({"detail": "That reminder couldn't be read."})
        if not isinstance(d.get("forever", False), bool):
            raise ValidationError({"forever": "Send true or false."})
        try:
            nudges.snooze(request.user, kind, module, rid, forever=d.get("forever", False))
        except LookupError:
            raise NotFound("This record doesn't exist or has been deleted.")
        return Response(nudges.for_user(request.user))


class StuckRulesView(APIView):
    """When a record counts as stuck, per stage, for the pipelines this person can see."""

    def get(self, request):
        fs = FirmSettings.load()
        return Response({m: stuck.rules(m, fs) for m in stuck.PIPELINES if request.user.can(m, "view")})


class AnalyticsView(APIView):
    def get(self, request):
        if not (request.user.can("outreach", "view") or request.user.can("deals", "view")):
            raise PermissionDenied("Your role can't see Outreach or Active Deals.")
        period = request.query_params.get("period", "12")
        if period not in analytics.PERIODS:
            period = "12"
        return Response(analytics.for_user(request.user, period))


def alert_settings(fs: FirmSettings) -> dict:
    return {
        "stuck_alerts": fs.stuck_alerts,
        "stuck_overrides": fs.stuck_overrides or {},
        "nudge_meeting_task": fs.nudge_meeting_task,
        "nudge_tracker_days": fs.nudge_tracker_days,
        "nudge_quiet_days": fs.nudge_quiet_days,
        "rules": {m: stuck.rules(m, fs) for m in stuck.PIPELINES},
        "min_samples": stuck.MIN_SAMPLES,
    }


class AlertSettingsView(APIView):
    def get_permissions(self):
        return [IsActiveUser(), flag_required("manage_settings")()] + ([] if self.request.method == "GET" else [NotOnDemo()])

    def get(self, request):
        return Response(alert_settings(FirmSettings.load()))

    def put(self, request):
        fs = FirmSettings.load()
        d = request.data
        errors = {}
        for key in ("stuck_alerts", "nudge_meeting_task"):
            if key in d:
                if not isinstance(d[key], bool):
                    errors[key] = "Send true or false."
                else:
                    setattr(fs, key, d[key])
        for key in ("nudge_tracker_days", "nudge_quiet_days"):
            if key in d:
                v = d[key]
                n = 0 if v in (0, "0") else to_id(v)
                if isinstance(v, bool) or n is None or n > 365:
                    errors[key] = "Pick between 0 (off) and 365 days."
                else:
                    setattr(fs, key, n)
        if "stuck_overrides" in d:
            overrides, errs = stuck.clean_overrides(d["stuck_overrides"])
            errors.update(errs)
            fs.stuck_overrides = overrides
        if errors:
            raise ValidationError(errors)
        fs.save()
        return Response(alert_settings(fs))
