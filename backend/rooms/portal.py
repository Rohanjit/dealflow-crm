"""The investor's side of a data room (decision 64): plain server-rendered pages at /r/<token>/.

Separate from Dealflow's sign-in: an investor's open room is a random token in its own cookie
(HttpOnly, SameSite=Strict, __Host- on https, one per link), ending after 30 idle minutes or 12 hours.
Every page sends a strict policy: no outside anything, no inline scripts, no referrer (the link is in
the address), never cached, never framed. Each step is written to the audit log as the investor.
"""

from __future__ import annotations

import json
import time

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.lockout import client_ip
from core import audit

from . import marks
from . import service as svc
from .models import Question, RoomFile

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def _cookie_name(share) -> str:
    return f"{'__Host-' if settings.HTTPS else ''}dealflow_room_{share.pk}"


def _harden(resp):
    resp["Content-Security-Policy"] = CSP + ("; upgrade-insecure-requests" if settings.HTTPS else "")
    # same-origin, not no-referrer: browsers send "Origin: null" with no-referrer, which fails the CSRF
    # check on our own forms. Room pages load nothing from anywhere else, so the link never leaves.
    resp["Referrer-Policy"] = "same-origin"
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    resp["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resp["Cross-Origin-Resource-Policy"] = "same-origin"
    return resp


def _audit(request, share, action: str, status: int, started: float, extra: dict | None = None):
    entry = {
        "u": {"id": None, "name": f"Investor: {share.name} ({share.entry.investor.firm_name})", "email": share.email}
        if share else None,
        "ip": client_ip(request), "m": request.method, "path": "/r/…" + request.path[request.path.find("/", 3):],
        "q": "", "s": status, "ms": round((time.monotonic() - started) * 1000), "a": action,
    }
    if share is not None:
        entry["r"] = f"deal_investors:{share.entry_id}"
        entry["n"] = [f"Data room of deal {share.room.deal_id}, link {share.pk}"]
    if extra:
        entry["n"] = entry.get("n", []) + [f"{k}: {v}" for k, v in extra.items()]
    audit.append(entry)


def _page(request, template: str, ctx: dict, status=200):
    resp = render(request, f"rooms/{template}", {"firm": _firm_name(), **ctx}, status=status)
    return _harden(resp)


def _firm_name() -> str:
    from core.models import FirmSettings

    return FirmSettings.load().firm_name or "Dealflow"


def _gone(request):
    return _page(request, "gone.html", {}, status=404)


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return (local[:1] + "•" * max(2, len(local) - 1)) + "@" + domain


def _open(request, token):
    """(share, visitor) for this request; visitor is None when a code is needed first."""
    share = svc.find_share(token)
    if share is None:
        return None, None
    return share, svc.visitor_for(share, request.COOKIES.get(_cookie_name(share)))


def _files(share):
    return list(share.room.files.all())


@require_GET
def room(request, token):
    started = time.monotonic()
    share, visitor = _open(request, token)
    if share is None:
        return _gone(request)
    ctx = {"share": share, "room": share.room, "token": token}
    if visitor is None:
        resp = _page(request, "code.html", {**ctx, "masked": _mask(share.email), "sent": request.GET.get("sent") == "1",
                                            "locked": svc.locked(share)})
        return resp
    if not share.nda_done:
        return _page(request, "nda.html", ctx)
    files = _files(share)
    mine = list(Question.objects.filter(share=share).order_by("-asked_at")[:100])
    resp = _page(request, "room.html", {**ctx, "files": files, "questions": mine,
                                        "banker": share.created_by.full_name if share.created_by_id else ""})
    _audit(request, share, "Investor opened the data room", 200, started)
    return resp


@require_POST
def send_code(request, token):
    started = time.monotonic()
    share = svc.find_share(token)
    if share is None:
        return _gone(request)
    try:
        svc.send_code(share, client_ip(request))
    except svc.RoomError as e:
        _audit(request, share, "Investor asked for a data room code (refused)", 429, started, {"why": str(e)})
        return _page(request, "code.html", {"share": share, "room": share.room, "token": token,
                                            "masked": _mask(share.email), "error": str(e), "locked": svc.locked(share)},
                     status=429)
    _audit(request, share, "Investor asked for a data room code", 302, started)
    return _harden(HttpResponseRedirect(f"/r/{token}/?sent=1"))


@require_POST
def verify(request, token):
    started = time.monotonic()
    share = svc.find_share(token)
    if share is None:
        return _gone(request)
    try:
        value = svc.check_code(share, request.POST.get("code", ""), client_ip(request),
                               request.META.get("HTTP_USER_AGENT", ""))
    except svc.RoomError as e:
        _audit(request, share, "Investor entered a wrong data room code", 400, started)
        return _page(request, "code.html", {"share": share, "room": share.room, "token": token, "sent": True,
                                            "masked": _mask(share.email), "error": str(e), "locked": svc.locked(share)},
                     status=400)
    resp = HttpResponseRedirect(f"/r/{token}/")
    resp.set_cookie(_cookie_name(share), value, max_age=svc.MAX_HOURS * 3600, path="/", secure=settings.HTTPS,
                    httponly=True, samesite="Strict")
    _audit(request, share, "Investor opened the data room with a code", 302, started)
    return _harden(resp)


@require_POST
def nda(request, token):
    started = time.monotonic()
    share, visitor = _open(request, token)
    if share is None or visitor is None:
        return _harden(HttpResponseRedirect(f"/r/{token}/")) if share else _gone(request)
    if share.room.nda_mode != "click" or share.nda_done:
        return _harden(HttpResponseRedirect(f"/r/{token}/"))
    if request.POST.get("agree") != "yes":
        return _page(request, "nda.html", {"share": share, "room": share.room, "token": token,
                                           "error": "Tick the box to agree.", "name": request.POST.get("name", "")}, 400)
    try:
        svc.agree_nda(share, visitor, request.POST.get("name", ""), client_ip(request))
    except svc.RoomError as e:
        return _page(request, "nda.html", {"share": share, "room": share.room, "token": token, "error": str(e)}, 400)
    _audit(request, share, "Investor agreed to the data room NDA", 302, started, {"name": share.nda_name})
    return _harden(HttpResponseRedirect(f"/r/{token}/"))


def _file(share, fid) -> RoomFile:
    rf = RoomFile.objects.filter(pk=fid, room=share.room).first()
    if rf is None:
        raise Http404
    return rf


def _ready(request, token):
    share, visitor = _open(request, token)
    if share is None or visitor is None or not share.nda_done:
        return share, None
    return share, visitor


@require_GET
def viewer(request, token, fid):
    started = time.monotonic()
    share, visitor = _ready(request, token)
    if visitor is None:
        return _harden(HttpResponseRedirect(f"/r/{token}/")) if share else _gone(request)
    rf = _file(share, fid)
    if rf.ext not in marks.VIEWABLE:
        return _harden(HttpResponseRedirect(f"/r/{token}/"))
    visit = svc.start_reading(share, visitor, rf, client_ip(request))
    resp = _page(request, "viewer.html", {"share": share, "room": share.room, "token": token, "file": rf,
                                          "pages": range(1, rf.pages + 1), "visit": visit})
    _audit(request, share, "Investor opened a data room file", 200, started, {"file": rf.title, "trace": visit.trace})
    return resp


@require_GET
def page(request, token, fid, n):
    share, visitor = _ready(request, token)
    if visitor is None:
        raise Http404
    rf = _file(share, fid)
    if rf.ext not in marks.VIEWABLE:
        raise Http404
    visit = svc.start_reading(share, visitor, rf, client_ip(request))
    try:
        img = marks.view_page(svc.read_file(rf), rf.ext, int(n), svc.mark_lines(share, visit.trace))
    except marks.MarkError:
        raise Http404 from None
    resp = HttpResponse(img, content_type="image/jpeg")
    resp["X-Content-Type-Options"] = "nosniff"
    return _harden(resp)


@require_POST
def ping(request, token, fid):
    share, visitor = _ready(request, token)
    if visitor is None:
        return _harden(JsonResponse({"ok": False, "signed_out": True}, status=401))
    rf = _file(share, fid)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    visit = svc.start_reading(share, visitor, rf, client_ip(request))
    svc.reading(visit, body.get("page"))
    return _harden(JsonResponse({"ok": True}))


@require_GET
def download(request, token, fid):
    started = time.monotonic()
    share, visitor = _ready(request, token)
    if visitor is None:
        return _harden(HttpResponseRedirect(f"/r/{token}/")) if share else _gone(request)
    rf = _file(share, fid)
    if rf.mode != "download":
        raise Http404
    try:
        data, trace = svc.downloaded(share, visitor, rf, client_ip(request))
    except marks.MarkError as e:
        return _page(request, "gone.html", {"heading": "This file couldn't be prepared", "message": str(e),
                                            "back": f"/r/{token}/"}, status=500)
    ext = "jpg" if rf.ext in ("png", "gif", "webp", "jpeg") else rf.ext
    from vault.files import KINDS

    ctype = KINDS["jpg"].content_type if ext == "jpg" else KINDS[ext].content_type
    stem = rf.file_name.rsplit(".", 1)[0] if "." in rf.file_name else rf.file_name
    from django.utils.http import content_disposition_header

    resp = HttpResponse(data, content_type=ctype)
    resp["Content-Disposition"] = content_disposition_header(True, f"{stem}.{ext}")
    resp["X-Content-Type-Options"] = "nosniff"
    _audit(request, share, "Investor downloaded a data room file", 200, started, {"file": rf.title, "trace": trace})
    return _harden(resp)


@require_POST
def ask(request, token):
    started = time.monotonic()
    share, visitor = _ready(request, token)
    if visitor is None:
        return _harden(HttpResponseRedirect(f"/r/{token}/")) if share else _gone(request)
    rf = None
    if fid := request.POST.get("file"):
        try:
            rf = RoomFile.objects.filter(pk=int(fid), room=share.room).first()
        except ValueError:
            rf = None
    back = f"/r/{token}/f/{rf.pk}/" if rf and request.POST.get("from") == "viewer" else f"/r/{token}/"
    try:
        svc.ask(share, visitor, rf, request.POST.get("text", ""), request.POST.get("page"))
    except svc.RoomError as e:
        return _harden(HttpResponseRedirect(back + "?asked=0&why=" + ("busy" if "lot of" in str(e) else "empty")))
    _audit(request, share, "Investor asked a question in the data room", 302, started,
           {"file": rf.title if rf else "(general)"})
    return _harden(HttpResponseRedirect(back + "?asked=1"))


@require_http_methods(["POST"])
def leave(request, token):
    started = time.monotonic()
    share, visitor = _open(request, token)
    if share is None:
        return _gone(request)
    if visitor is not None:
        svc.end_visitor(visitor)
    resp = HttpResponseRedirect(f"/r/{token}/")
    resp.delete_cookie(_cookie_name(share), path="/", samesite="Strict")
    _audit(request, share, "Investor closed the data room", 302, started)
    return _harden(resp)


def csrf_failure(request, reason=""):
    """A form on a room page that was open too long (or from another tab's room): say so plainly.
    Everywhere else, Django's own answer."""
    if request.path.startswith("/r/"):
        token = request.path.split("/")[2] if request.path.count("/") >= 3 else ""
        return _page(request, "gone.html", {"heading": "Please try that again",
                                            "message": "This page was open for too long, so it was refused for your safety.",
                                            "back": f"/r/{token}/" if token else ""}, status=403)
    from django.views.csrf import csrf_failure as default

    return default(request, reason)
