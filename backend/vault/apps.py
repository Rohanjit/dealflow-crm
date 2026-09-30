from django.apps import AppConfig


class VaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vault"

    def ready(self):
        from . import fields  # noqa: F401  registers the Vault
        from . import signals  # noqa: F401  files go when their document is deleted for good
