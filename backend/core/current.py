"""The request being handled, for code far from the view that needs to know who is asking
(e.g. a link to a restricted deal is shown as "Restricted deal" to people who can't see it).

The middleware keeps the Django request; DRF copies the signed-in user onto it after
authenticating, so `user()` is right for both normal sign-ins and test clients."""

from contextvars import ContextVar

_request: ContextVar = ContextVar("dealflow_request", default=None)


def user():
    req = _request.get()
    u = getattr(req, "user", None) if req is not None else None
    return u if u is not None and getattr(u, "is_authenticated", False) else None


class CurrentRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request.set(request)
        try:
            return self.get_response(request)
        finally:
            _request.reset(token)


# ---------------------------------------------------------------- audit details for this request


def add_change(change: dict):
    """Something this request changed, for the audit log entry (core/audit_middleware.py)."""
    req = _request.get()
    if req is not None:
        req.__dict__.setdefault("_df_changes", []).append(change)


def set_subject(module: str, pk, title: str):
    """The record this request is about ("Opened deal Zeta Pharma")."""
    req = _request.get()
    if req is not None and not hasattr(req, "_df_subject"):
        req._df_subject = {"record": f"{module}:{pk}", "title": title}


def note(text: str):
    """A plain-words detail for the audit log ("Approved: Restrict the deal")."""
    req = _request.get()
    if req is not None:
        req.__dict__.setdefault("_df_notes", []).append(text)
