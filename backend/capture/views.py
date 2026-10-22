"""Contact capture API (decision 64)."""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core import current
from core.records import to_id

from . import service, signature


class SuggestionsView(APIView):
    def get(self, request):
        return Response({"people": service.suggestions(request.user), "count": service.count(request.user)})


class SuggestionDismissView(APIView):
    def post(self, request, pk):
        service.dismiss(request.user, to_id(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuggestionPrefillView(APIView):
    def get(self, request, pk):
        return Response(service.prefill(request.user, to_id(pk)))


class CheckView(APIView):
    def post(self, request):
        return Response(service.check(request.user, request.data))


class ParseView(APIView):
    """A pasted signature or selected text: suggested details (nothing is saved)."""

    def post(self, request):
        text = request.data.get("text")
        if not isinstance(text, str) or len(text) > 20000:
            raise ValidationError({"text": "Paste up to 20,000 characters."})
        name, email = request.data.get("name"), request.data.get("email")
        if isinstance(email, str) and "@" in email:  # an email read by the add-on: we know who sent it
            out = signature.parse(text, name if isinstance(name, str) else "", email.strip().lower()[:255])
            return Response({**out, "work_email": email.strip().lower()[:255]})
        return Response(signature.from_text(text))


class AddFirmView(APIView):
    def post(self, request):
        name = request.data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError({"name": "Enter the firm's name."})
        website = request.data.get("website") if isinstance(request.data.get("website"), str) else ""
        firm = service.add_firm(request.user, request.data.get("kind"), name.strip()[:255], website.strip()[:300])
        current.note(f"Added {firm['title']} while capturing a contact")
        return Response(firm, status=status.HTTP_201_CREATED)
