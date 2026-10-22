"""Gunicorn's access log without investors' data room links (decision 64): the part after /r/ is a
secret, so it's replaced before the line is written. Used with --logger-class in docker/entrypoint.sh."""

from gunicorn.glogging import Logger

from .logredact import redact


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key, value in list(atoms.items()):
            if isinstance(value, str) and "/r/" in value:
                atoms[key] = redact(value)
        return atoms
