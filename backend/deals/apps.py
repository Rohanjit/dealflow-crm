from django.apps import AppConfig


class DealsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "deals"

    def ready(self):
        from . import fields  # noqa: F401  registers Active Deals and the investor tracker
        from . import signals  # noqa: F401  mandates won in Outreach become deals
