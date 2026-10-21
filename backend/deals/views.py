from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import Restricted
from core.models import FirmSettings
from core.records import read_value, to_id, visible
from core.registry import get_module

from investors.models import Investor

from django.contrib.auth import get_user_model

from . import access, matching
from .models import AccessRequest, Deal, DealInvestor
from core.records import subtitle_for

User = get_user_model()


def _visible_deal(user, pk) -> Deal:
    """A live deal this person can see (restricted ones answer with their placeholder)."""
    deal = Deal.objects.filter(pk=pk, archived_at__isnull=True).select_related("company", "lead_banker").first()
    if not deal:
        raise NotFound("This deal doesn't exist or has been deleted.")
    if not access.can_see(user, deal):
        raise Restricted("This deal is restricted.", access.placeholder(deal, user))
    return deal


def _summary(d: Deal, mod) -> dict:
    from core import access

    no = access.hidden(mod)
    return {k: (None if k in no else v) for k, v in {
        "id": d.pk,
        "title": d.company.name,
        "subtitle": subtitle_for(mod, d),
        "stage": d.stage,
        "status": d.status,
        "target_close_date": d.target_close_date.isoformat() if d.target_close_date else None,
        "mandate_expiry": d.mandate_expiry.isoformat() if d.mandate_expiry else None,
        "lead_banker": {"id": d.lead_banker_id, "name": d.lead_banker.full_name} if d.lead_banker_id else None,
        "target_raise": read_value(d, mod.field("target_raise")),
    }.items()}


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
        everything = Deal.objects.filter(archived_at__isnull=True)
        live = visible(everything, mod, request.user)
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
        from core import access

        no = access.hidden(mod)
        if "target_close_date" in no:
            near_close = near_close.none()  # the list itself would tell the dates
        if "mandate_expiry" in no:
            expiring = expiring.none()
        return Response({
            "days": days,
            "today": today.isoformat(),
            "stages": [{"value": v, "label": lbl, "count": by_stage.get(v, 0)} for v, lbl in p.stages],
            "groups": [{"label": lbl, "stages": members} for lbl, members in p.groups],
            "exits": [{"value": e.value, "label": e.label, "count": by_status.get(e.value, 0)} for e in p.exits],
            "active": sum(n for s, n in by_stage.items() if s != p.won),
            "near_close": [_summary(d, mod) for d in near_close[:50]],
            "expiring": [_summary(d, mod) for d in expiring[:50]],
            # Restricted deals this person can't see: counted, never described.
            "restricted_hidden": everything.count() - live.count(),
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
        _visible_deal(user, pk)
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        q = (request.query_params.get("q") or "").strip()
        from core import access

        # A factor drawing on a field this person's role can't see is scored but not explained.
        blocked = {factor for factor, fields in matching.FACTOR_FIELDS.items()
                   if any(not access.can_see(m, k) for m, k in fields)}
        facts = {k: (None if k in matching.FACT_FACTOR and matching.FACT_FACTOR[k] in blocked else v)
                 for k, v in matching.facts(deal).items()}
        results = matching.suggestions(deal, limit, q)
        imod = get_module("investors")
        for r in results:
            r["reasons"] = [x if x["factor"] not in blocked else {**x, "text": "Not shown to your role"}
                            for x in r["reasons"]]
        if any(k in access.hidden(imod) for k in imod.subtitle_fields):
            for r in results:
                r["investor"]["subtitle"] = ""
        return Response({
            "facts": facts,
            "weights": [{"factor": k, "points": full} for k, full, _ in matching.FACTORS],
            "results": results,
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
        deal = _visible_deal(user, pk)
        raw = request.data.get("investor_ids")
        if not isinstance(raw, list) or not raw:
            raise ValidationError({"detail": "Select at least one investor."})
        ids = list(dict.fromkeys(to_id(x) for x in raw))[:500]
        if None in ids:
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


# ---------------------------------------------------------------- restricted deals (deals/access.py)


class DealAccessView(APIView):
    """Who can see a deal, and changes to it. POST {action: restrict | unrestrict | add | remove, person}.
    Approvers' changes apply at once; anyone else on the team creates a request."""

    def get(self, request, pk):
        if not request.user.can("deals", "view"):
            raise PermissionDenied("Your role can't see Active Deals.")
        return Response(access.access_payload(_visible_deal(request.user, pk), request.user))

    def post(self, request, pk):
        user = request.user
        if not user.can("deals", "view"):
            raise PermissionDenied("Your role can't see Active Deals.")
        deal = _visible_deal(user, pk)
        action = request.data.get("action")
        person = None
        if action in ("add", "remove"):
            person = User.objects.filter(pk=to_id(request.data.get("person"))).first()
            if person is None:
                raise ValidationError({"person": "Pick someone."})
        try:
            if action == "remove":
                access.remove(deal, user, person)
                result = None
            elif action in ("restrict", "unrestrict", "add"):
                result = access.ask(deal, user, action, person)
            else:
                raise ValidationError({"action": "Pick restrict, unrestrict, add or remove."})
        except access.AccessError as e:
            raise ValidationError({"detail": str(e)})
        deal.refresh_from_db()
        out = access.access_payload(deal, user)
        out["result"] = "pending" if result is not None and result.status == "pending" else "done"
        return Response(out)


class DealAccessAskView(APIView):
    """From the "Restricted deal" placeholder: ask the deal's approvers for access."""

    def post(self, request, pk):
        user = request.user
        if not user.can("deals", "view"):
            raise PermissionDenied("Your role can't see Active Deals.")
        deal = Deal.objects.filter(pk=pk, archived_at__isnull=True).first()
        if not deal:
            raise NotFound("This deal doesn't exist or has been deleted.")
        try:
            access.ask(deal, user, "join")
        except access.AccessError as e:
            raise ValidationError({"detail": str(e)})
        return Response(access.placeholder(deal, user))


class AccessRequestsView(APIView):
    """Requests waiting for this person's decision, and their own (polled by the Reminders bell)."""

    def get(self, request):
        if not request.user.can("deals", "view"):
            return Response({"to_decide": [], "mine": [], "count": 0})
        return Response(access.requests_for(request.user))


class AccessRequestDecideView(APIView):
    """POST {decision: approve | decline | withdraw}."""

    def post(self, request, pk):
        r = AccessRequest.objects.filter(pk=pk).select_related("deal__company", "person", "requested_by").first()
        if r is None or r.deal.archived_at is not None:
            raise NotFound("This request no longer exists.")
        decision = request.data.get("decision")
        if decision not in ("approve", "decline", "withdraw"):
            raise ValidationError({"decision": "Approve, decline or withdraw."})
        try:
            access.decide(r, request.user, decision)
        except access.AccessError as e:
            raise ValidationError({"detail": str(e)})
        return Response(access.requests_for(request.user))
