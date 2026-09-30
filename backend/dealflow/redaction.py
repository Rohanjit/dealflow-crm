"""Keep secrets out of logs: passwords, PINs, tokens, keys, cookies, CSRF values and invite links.

Used by the logging filter below (every Django log line) and by the gunicorn access log
(dealflow/gunicorn_logging.py)."""

from __future__ import annotations

import logging
import re

MASK = "[redacted]"
SENSITIVE = r"(?:password|passwd|pin|token|secret|api[_-]?key|key|authorization|cookie|csrf[\w-]*|sessionid|session)"

_PATTERNS = [
    # Invite links carry their secret in the path.
    (re.compile(r"(/invites?/(?:accept/)?)[A-Za-z0-9_\-]{16,}"), rf"\1{MASK}"),
    # "password": "x" / 'pin': '1234' (JSON and Python reprs)
    (re.compile(rf"""(["']{SENSITIVE}["']\s*:\s*)(["'])(?:\\.|(?!\2).)*\2""", re.I), rf"\1\2{MASK}\2"),
    # password=x in query strings, form bodies, key=value log text
    (re.compile(rf"(\b{SENSITIVE}=)[^&\s;,'\"]+", re.I), rf"\1{MASK}"),
    # Authorization / Cookie / X-CSRFToken headers
    (re.compile(r"((?:authorization|cookie|set-cookie|x-csrftoken)\s*:\s*)[^\r\n]+", re.I), rf"\1{MASK}"),
    # Argon2 / PBKDF2 hashes, should one ever be printed
    (re.compile(r"\b(?:argon2\$argon2id?|pbkdf2_sha256)\$[^\s'\"]+"), MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Attached to every handler: rewrites the finished message and any traceback text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string shouldn't drop the line
            message = str(record.msg)
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
