from django.apps import AppConfig


class InvestorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "investors"

    def ready(self):
        from . import fields  # noqa: F401  registers the module
