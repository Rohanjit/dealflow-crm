"""Gunicorn logger that redacts secrets (invite links, tokens in query strings) from access-log
lines before they're written. Enabled with --logger-class in docker/entrypoint.sh."""

from gunicorn.glogging import Logger

from dealflow.redaction import redact


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in ("r", "U", "q", "f"):  # request line, path, query string, referer
            if isinstance(atoms.get(key), str):
                atoms[key] = redact(atoms[key])
        # Header atoms look like {name}i; never log cookies or auth headers.
        for key in list(atoms):
            if key.lower() in ("{cookie}i", "{authorization}i", "{x-csrftoken}i", "{set-cookie}o"):
                atoms[key] = "[redacted]"
        return atoms

    def error(self, msg, *args, **kwargs):
        super().error(redact(str(msg)), *args, **kwargs)
