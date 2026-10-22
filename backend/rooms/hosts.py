"""Keeping investors and bankers on their own addresses (decision 64).

With DEALFLOW_ROOMS_DOMAIN set (e.g. rooms.yourfirm.com), that address serves only data room pages
(/r/...), their stylesheet and script (/rooms/...), robots.txt and the health check; everything else
there is "not found", so Dealflow itself never shows on the investor address. Data room pages are not
served on the bankers' address either. Without it, rooms live at /r/ on the one address.

DEALFLOW_STAFF_NETWORKS (optional, e.g. "10.0.0.0/8,203.0.113.7/32") limits everything except the
investor address and the health check to those networks, so Dealflow can stay inside the office or
VPN while investors reach their rooms from anywhere.

This runs before WhiteNoise, so the app's own files aren't served on the investor address either.
"""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound

ROOM_PATHS = ("/r/", "/rooms/")
OPEN_PATHS = ("/robots.txt", "/api/health/")


def _networks():
    raw = getattr(settings, "STAFF_NETWORKS", "") or ""
    out = []
    for part in raw.split(","):
        if part.strip():
            out.append(ipaddress.ip_network(part.strip(), strict=False))
    return out


class RoomHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.rooms_domain = (getattr(settings, "ROOMS_DOMAIN", "") or "").lower()
        self.networks = _networks()

    def __call__(self, request):
        path = request.path
        host = request.get_host().split(":")[0].lower() if self.rooms_domain or self.networks else ""
        on_rooms = bool(self.rooms_domain) and host == self.rooms_domain
        is_room = path.startswith(ROOM_PATHS)
        if on_rooms and not (is_room or path in OPEN_PATHS):
            return HttpResponseNotFound("Not found", content_type="text/plain")
        if self.rooms_domain and not on_rooms and path.startswith("/r/"):
            return HttpResponseNotFound("Not found", content_type="text/plain")
        if self.networks and not on_rooms and not is_room and path not in OPEN_PATHS:
            from accounts.lockout import client_ip

            try:
                ip = ipaddress.ip_address(client_ip(request))
            except ValueError:
                ip = None
            if ip is None or not any(ip in n for n in self.networks):
                return HttpResponse("Dealflow is only open from the firm's network.", status=403,
                                    content_type="text/plain")
        return self.get_response(request)
