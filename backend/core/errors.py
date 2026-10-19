"""API error handling beyond DRF's defaults."""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


class Restricted(APIException):
    """A record this person can't see (a restricted deal): 403 with what they may know about it,
    sent as is (DRF would otherwise turn every value in the details into text)."""

    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, detail: str, placeholder: dict):
        super().__init__(detail)
        self.payload = {"detail": detail, "code": "restricted", "placeholder": placeholder}


def handler(exc, context):
    if isinstance(exc, Restricted):
        return Response(exc.payload, status=exc.status_code)
    return exception_handler(exc, context)
