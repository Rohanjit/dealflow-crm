"""Request body parsing.

Every JSON endpoint expects an object ({"field": value}). A list, number or string at the top level
is refused here with a 400, so views can rely on `request.data.get(...)`.
"""

from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class ObjectJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError("Send the details as a JSON object.")
        return data
