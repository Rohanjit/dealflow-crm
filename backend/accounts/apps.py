from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        def ended(sender, request, user, **kwargs):
            # Signed out (by the person, a time-out or an admin): the device leaves the list.
            from django.utils import timezone

            from .models import UserSession

            key = getattr(getattr(request, "session", None), "session_key", None)
            if key:
                UserSession.objects.filter(session_key=key, ended_at__isnull=True).update(ended_at=timezone.now())

        user_logged_out.connect(ended, dispatch_uid="dealflow-session-ended")
