from django.apps import AppConfig


class OutreachConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "outreach"

    def ready(self):
        from . import fields  # noqa: F401  registers Companies and Outreach
