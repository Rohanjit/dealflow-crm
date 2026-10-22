"""Bankers' side of data rooms: /api/deals/<id>/room/... and /api/rooms/... (decision 64)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core import current
from core.records import to_id

from . import service as svc
from .models import Question, RoomFile, Share


def _subject(request, deal):
    current.set_subject("deals", deal.pk, deal.company.name)


class RoomView(APIView):
    """GET the room (files, shares, activity, questions, per-entry summary); PATCH its settings."""

    def get(self, request, pk):
        deal = svc.live_deal(request.user, pk)
        _subject(request, deal)
        out = svc.payload(request, deal)
        if request.query_params.get("candidates") == "1" and out["can"]["share"]:
            out["candidates"] = svc.candidates(deal)
        return Response(out)

    def patch(self, request, pk):
        deal = svc.live_deal(request.user, pk)
        _subject(request, deal)
        svc.update_room(deal, request.user, request.data)
        return Response(svc.payload(request, deal))


class RoomFilesView(APIView):
    def post(self, request, pk):
        deal = svc.live_deal(request.user, pk)
        _subject(request, deal)
        svc.add_file(request, deal, to_id(request.data.get("document")), request.data.get("mode") or "view",
                     request.data.get("title") or "")
        return Response(svc.payload(request, deal), status=status.HTTP_201_CREATED)


class RoomFileView(APIView):
    def _get(self, request, pk):
        rf = RoomFile.objects.filter(pk=pk).select_related("room__deal").first()
        if rf is None:
            raise NotFound("This file isn't in a data room.")
        svc.live_deal(request.user, rf.room.deal_id)
        _subject(request, rf.room.deal)
        return rf

    def patch(self, request, pk):
        rf = self._get(request, pk)
        svc.change_file(rf, request.user, request.data)
        return Response(svc.payload(request, rf.room.deal))

    def delete(self, request, pk):
        rf = self._get(request, pk)
        deal = rf.room.deal
        svc.remove_file(rf, request.user)
        return Response(svc.payload(request, deal))


class RoomSharesView(APIView):
    def post(self, request, pk):
        deal = svc.live_deal(request.user, pk)
        _subject(request, deal)
        svc.create_share(request, deal, request.data)
        return Response(svc.payload(request, deal), status=status.HTTP_201_CREATED)


class RoomShareView(APIView):
    def _get(self, request, pk):
        share = Share.objects.filter(pk=pk).select_related("room__deal__company", "entry__investor").first()
        if share is None:
            raise NotFound("This link doesn't exist.")
        svc.live_deal(request.user, share.room.deal_id)
        _subject(request, share.room.deal)
        return share

    def patch(self, request, pk):
        share = self._get(request, pk)
        svc.change_share(request, share, request.data)
        return Response(svc.payload(request, share.room.deal))


class RoomShareLinkView(APIView):
    """The link itself, for "Copy link" (the person sharing must be allowed to share)."""

    def get(self, request, pk):
        share = RoomShareView()._get(request, pk)
        svc.require_share(request.user, share.room.deal)
        if not svc.working(share):
            raise ValidationError({"detail": "This link isn't working, so there's nothing to copy."})
        current.note(f"Copied the data room link for {share.name}")
        return Response({"link": svc.link_for(share, request)})


class QuestionAnswerView(APIView):
    def post(self, request, pk):
        q = Question.objects.filter(pk=pk).select_related("share__room__deal__company", "share__entry__investor").first()
        if q is None:
            raise NotFound("This question doesn't exist.")
        svc.live_deal(request.user, q.share.room.deal_id)
        _subject(request, q.share.room.deal)
        svc.answer(request, q, request.data.get("answer"))
        return Response(svc.payload(request, q.share.room.deal))


class RoomTraceView(APIView):
    """Upload a file that got out: whose copy was it?"""

    parser_classes = [MultiPartParser]

    def post(self, request, pk):
        deal = svc.live_deal(request.user, pk)
        _subject(request, deal)
        f = request.FILES.get("file")
        code = str(request.data.get("code") or "")[:200].upper()
        if f is None and not code:
            raise ValidationError({"file": "Choose the file to check, or type the trace code."})
        if f is not None and f.size > 100 * 1024 * 1024:
            raise ValidationError({"file": "Files up to 100 MB can be checked."})
        found = svc.trace(request.user, deal, f.read() if f is not None else code.encode())
        return Response({"found": found})


class RoomAlertsView(APIView):
    def get(self, request):
        return Response({"questions": svc.alerts(request.user)})
