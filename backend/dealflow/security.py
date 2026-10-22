"""Browser security headers on every response (decision 63).

Content-Security-Policy: scripts, connections, fonts and frames only from this install, nothing
may frame Dealflow, no plugins, forms only post here. Styles allow inline because Mantine writes
its styles at run time (no inline scripts are allowed). A response that sets its own policy keeps
it (Vault previews are stricter)."""

from django.conf import settings

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self'",
    "frame-src 'self'",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

PERMISSIONS = ", ".join([
    "camera=()", "microphone=()", "geolocation=()", "payment=()", "usb=()", "serial=()", "bluetooth=()",
    "hid=()", "midi=()", "magnetometer=()", "gyroscope=()", "accelerometer=()", "display-capture=()",
    "browsing-topics=()",
    'publickey-credentials-get=(self)', 'publickey-credentials-create=(self)',  # passkeys
])


NO_CACHE = "no-store, no-cache, must-revalidate, private"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = CSP + ("; upgrade-insecure-requests" if settings.HTTPS else "")
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS)
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        # A firm's CRM never belongs in a search engine.
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        # Deal data (and the app page) never sits in a browser or proxy cache. The app's hashed asset
        # files are served by WhiteNoise before this runs and keep their own caching.
        response.headers.setdefault("Cache-Control", NO_CACHE)
        if request.path.startswith("/api/"):
            response.headers.setdefault("Pragma", "no-cache")
        return response
