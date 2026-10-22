"""Investors' data room links are secrets (decision 64): the part after /r/ is replaced in every log
line Django writes (errors carry the request's path)."""

import logging
import re

ROOM = re.compile(r"/r/[^/?\s\"']+")


def redact(text: str) -> str:
    return ROOM.sub("/r/-", text) if "/r/" in text else text


class RedactRoomLinks(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken log call: leave it as it is
            return True
        if "/r/" in message:
            record.msg, record.args = redact(message), ()
        if record.exc_text and "/r/" in record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
