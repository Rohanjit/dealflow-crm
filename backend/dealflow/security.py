"""HTTP-level protections that apply to every request (see MIDDLEWARE in settings)."""

from __future__ import annotations

import re

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token

MB = 1024 * 1024

# The app's own code and styles only. Mantine sets styles inline, hence 'unsafe-inline' for styles
# (never for scripts). Vault image previews set their own, stricter policy.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-src 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


class SecurityHeadersMiddleware:
    """Content Security Policy and Permissions-Policy on pages; no caching of API answers, which
    carry firm data. (HSTS, nosniff, referrer and opener policies come from Django's
    SecurityMiddleware; framing is refused by XFrameOptionsMiddleware and frame-ancestors.)"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get("Content-Type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response


class CsrfTokenHeaderMiddleware:
    """The CSRF cookie is HttpOnly, so the app can't read it from JavaScript. Instead every API
    answer carries the current token in a header, which the app sends back on changes. Only
    same-origin scripts can read response headers (there's no CORS), so this reveals nothing new.
    Must sit after CsrfViewMiddleware so the cookie is (re)set on the way out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["X-CSRFToken"] = get_token(request)
        return response


def _body_limit(path: str) -> int:
    if path == "/api/vault/upload/":
        return settings.VAULT_MAX_UPLOAD_MB * MB
    if path == "/api/backups/restore/":
        return settings.RESTORE_MAX_MB * MB
    if re.fullmatch(r"/api/m/[a-z_]+/import/upload/", path):
        return settings.IMPORT_MAX_FILE_MB * MB + MB
    return settings.DATA_UPLOAD_MAX_MEMORY_SIZE + MB


class RequestSizeMiddleware:
    """Refuse oversized request bodies before any of it is read or written to disk, and bodies
    whose size isn't declared (chunked uploads), which could otherwise fill the disk first."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            length = request.META.get("CONTENT_LENGTH") or ""
            chunked = "chunked" in request.META.get("HTTP_TRANSFER_ENCODING", "").lower()
            if chunked or (length and not length.isdigit()):
                return JsonResponse({"detail": "The request must state its size."}, status=411)
            limit = _body_limit(request.path)
            if length and int(length) > limit:
                return JsonResponse(
                    {"detail": f"This upload is too large. The limit is {limit // MB} MB."}, status=413
                )
        return self.get_response(request)
