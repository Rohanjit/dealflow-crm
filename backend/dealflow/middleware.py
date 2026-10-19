"""Request clean-up shared by every view."""

from django.http import QueryDict


class StripNulMiddleware:
    """Remove NUL characters from the address's query (?q=…). Nothing legitimate contains them, and
    Postgres refuses to compare text that does, which would turn a search into an error page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        qs = request.META.get("QUERY_STRING", "")
        if "%00" in qs or "\x00" in qs:
            clean = QueryDict(mutable=True)
            for key, values in request.GET.lists():
                clean.setlist(key.replace("\x00", ""), [v.replace("\x00", "") for v in values])
            clean._mutable = False
            request.GET = clean
        return self.get_response(request)
