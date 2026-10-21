"""Settings > Export (decision 61): run a custom report, and keep reports to run again."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.records import text_in
from core.registry import get_module

from . import build
from .models import SavedReport


def _file(data: bytes, content_type: str, name: str) -> HttpResponse:
    resp = HttpResponse(data, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def _payload(r: SavedReport, user) -> dict:
    mod = get_module(r.module)
    return {
        "id": r.pk,
        "name": r.name,
        "module": r.module,
        "module_label": mod.label,
        "shared": r.shared,
        "mine": r.owner_id == user.pk,
        "owner": r.owner.full_name,
        "config": r.config,
        "updated_at": r.updated_at.isoformat(),
    }


def _visible(user):
    from django.db.models import Q

    qs = SavedReport.objects.filter(Q(owner=user) | Q(shared=True)).select_related("owner")
    return [r for r in qs if user.can(r.module, "view")]


def _clean_meta(data, partial=False) -> dict:
    out = {}
    if "name" in data or not partial:
        name = data.get("name")
        name = " ".join(text_in(name).split())[:120] if isinstance(name, str) else ""
        if not name:
            raise ValidationError({"name": "Give the report a name."})
        out["name"] = name
    if "shared" in data:
        if not isinstance(data["shared"], bool):
            raise ValidationError({"shared": "Send true or false."})
        out["shared"] = data["shared"]
    return out


class ReportRunView(APIView):
    """Run report settings straight away: {module, columns, filters, ordering, format, layout, title}."""

    def post(self, request):
        cfg = build.clean_config(request.data.get("config", request.data), request.user)
        return _file(*build.run(cfg, request))


class SavedReportListView(APIView):
    def get(self, request):
        items = [_payload(r, request.user) for r in _visible(request.user)]
        return Response({"mine": [i for i in items if i["mine"]], "shared": [i for i in items if not i["mine"]]})

    def post(self, request):
        meta = _clean_meta(request.data)
        cfg = build.clean_config(request.data.get("config"), request.user)
        r = SavedReport.objects.create(owner=request.user, module=cfg["module"], config=cfg, **meta)
        return Response(_payload(r, request.user), status=status.HTTP_201_CREATED)


class SavedReportDetailView(APIView):
    def _own(self, request, pk) -> SavedReport:
        r = get_object_or_404(SavedReport.objects.select_related("owner"), pk=pk)
        if r.owner_id != request.user.pk and not (r.shared and request.user.is_admin):
            if r.shared and request.user.can(r.module, "view"):
                raise PermissionDenied("Only the person who saved this report (or an admin) can change it.")
            raise NotFound("This report doesn't exist.")  # not theirs to see: same as missing
        return r

    def patch(self, request, pk):
        r = self._own(request, pk)
        for k, v in _clean_meta(request.data, partial=True).items():
            setattr(r, k, v)
        if "config" in request.data:
            cfg = build.clean_config(request.data["config"], request.user)
            r.module, r.config = cfg["module"], cfg
        r.save()
        return Response(_payload(r, request.user))

    def delete(self, request, pk):
        self._own(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SavedReportRunView(APIView):
    """Run a saved report as the person asking: they only get what they can see."""

    def post(self, request, pk):
        r = next((x for x in _visible(request.user) if x.pk == pk), None)
        if r is None:
            raise NotFound("This report doesn't exist.")
        cfg = build.clean_config(r.config, request.user)
        return _file(*build.run(cfg, request))
