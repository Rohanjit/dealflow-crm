"""Incident response: sign everyone out on every device (INCIDENT_RESPONSE.md). Everyone signs in
again with their password and two-step login."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sign everyone out everywhere (incident response)."

    def handle(self, *args, **opts):
        from accounts import sessions
        from accounts.models import User
        from core import audit

        n = sum(sessions.end_all_for(u) for u in User.objects.all())
        audit.append({"u": {"id": None, "name": "Server command", "email": ""},
                      "a": f"Signed everyone out from the server ({n} sessions)"})
        self.stdout.write(f"Ended {n} sessions.")
