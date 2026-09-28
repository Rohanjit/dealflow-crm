from django.apps import AppConfig


class ContactsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "contacts"

    def ready(self):
        from . import fields  # noqa: F401  registers the module
