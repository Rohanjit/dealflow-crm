from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import FirmSettings
from core.records import read_value
from core.registry import get_module

from investors.models import Investor

from . import matching
from .models import Deal, DealInvestor


def _summary(d: Deal, mod) -> dict:
    return {
        "id": d.pk,
        "title": d.company.name,
        "subtitle": mod.subtitle(d),
        "stage": d.stage,
        "status": d.status,
        "target_close_date": d.target_close_date.isoformat() if d.target_close_date else None,
        "mandate_expiry": d.mandate_expiry.isoformat() if d.mandate_expiry else None,
        "lead_banker": {"id": d.lead_banker_id, "name": d.lead_banker.full_name} if d.lead_banker_id else None,
        "target_raise": read_value(d, mod.field("target_raise")),
    }


class DashboardView(APIView):
    """Home page snapshot of Active Deals: deals by stage, deals near their target close date,
    and mandates nearing expiry (the window is set in Settings > Firm)."""

    def get(self, request):
        if not request.user.can("deals", "view"):
            raise PermissionDenied("Your role can't see Active Deals.")
        mod = get_module("deals")
        days = FirmSettings.load().dashboard_days
        today = timezone.localdate()
        horizon = today + timedelta(days=days)
        live = Deal.objects.filter(archived_at__isnull=True)
        by_stage = dict(live.filter(status="open").values_list("stage").annotate(n=Count("pk")))
        by_status = dict(live.exclude(status="open").values_list("status").annotate(n=Count("pk")))
        p = mod.pipeline
        near_close = (
            live.filter(status="open", target_close_date__lte=horizon).exclude(stage=p.won)
            .select_related("company", "lead_banker", "transaction_type").order_by("target_close_date", "pk")
        )
        expiring = (
            live.filter(status__in=["open", "on_hold"], mandate_expiry__lte=horizon).exclude(stage=p.won)
            .select_related("company", "lead_banker", "transaction_type").order_by("mandate_expiry", "pk")
        )
        return Response({
            "days": days,
            "today": today.isoformat(),
            "stages": [{"value": v, "label": lbl, "count": by_stage.get(v, 0)} for v, lbl in p.stages],
            "groups": [{"label": lbl, "stages": members} for lbl, members in p.groups],
            "exits": [{"value": e.value, "label": e.label, "count": by_status.get(e.value, 0)} for e in p.exits],
            "active": sum(n for s, n in by_stage.items() if s != p.won),
            "near_close": [_summary(d, mod) for d in near_close[:50]],
            "expiring": [_summary(d, mod) for d in expiring[:50]],
        })


class MatchesView(APIView):
    """Investors ranked by fit for one deal, with the reasons behind each score."""

    def get(self, request, pk):
        user = request.user
        if not user.can("deals", "view"):
            raise PermissionDenied("Your role can't see Active Deals.")
        if not user.can("investors", "view"):
            raise PermissionDenied("Your role can't see Investors.")
        deal = (
            Deal.objects.filter(pk=pk, archived_at__isnull=True)
            .select_related("company__sector", "company__geography", "investment_stage", "transaction_type")
            .first()
        )
        if not deal:
            raise NotFound("This deal doesn't exist or has been archived.")
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        q = (request.query_params.get("q") or "").strip()
        return Response({
            "facts": matching.facts(deal),
            "weights": [{"factor": k, "points": full} for k, full, _ in matching.FACTORS],
            "results": matching.suggestions(deal, limit, q),
        })


class TrackerAddView(APIView):
    """Add several investors to a deal's tracker at once (at Identified). Investors already on the
    tracker are skipped and reported."""

    def post(self, request, pk):
        from core.records import apply_values, clean_values
        from core.registry import enabled_map

        user = request.user
        if not user.can("deal_investors", "create"):
            raise PermissionDenied("Your role can't add investors to deal trackers.")
        deal = Deal.objects.filter(pk=pk, archived_at__isnull=True).select_related("company").first()
        if not deal:
            raise NotFound("This deal doesn't exist or has been deleted.")
        raw = request.data.get("investor_ids")
        if not isinstance(raw, list) or not raw:
            raise ValidationError({"detail": "Select at least one investor."})
        try:
            ids = list(dict.fromkeys(int(x) for x in raw))[:500]
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Those investors couldn't be read."})
        mod = get_module("deal_investors")
        enabled = enabled_map("deal_investors")
        tracked = set(DealInvestor.objects.filter(deal=deal, archived_at__isnull=True).values_list("investor_id", flat=True))
        added, skipped = 0, []
        for inv in Investor.objects.filter(pk__in=ids, archived_at__isnull=True).order_by("firm_name"):
            if inv.pk in tracked:
                skipped.append(inv.firm_name)
                continue
            values, errors = clean_values(mod, {"deal": f"deals:{deal.pk}", "investor": f"investors:{inv.pk}",
                                                "stage": mod.pipeline.stages[0][0]}, enabled=enabled)
            if errors:
                skipped.append(inv.firm_name)
                continue
            apply_values(DealInvestor(), mod, values, user)
            added += 1
        return Response({"added": added, "skipped": skipped, "deal": {"id": deal.pk, "title": deal.company.name}})
