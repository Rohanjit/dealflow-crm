from django.conf import settings
from django.db import models

from .modules import MODULES


class FirmSettings(models.Model):
    """Singleton row (pk=1) with firm-wide settings."""

    firm_name = models.CharField(max_length=150, blank=True)
    setup_completed_at = models.DateTimeField(null=True, blank=True)
    backup_keep = models.PositiveIntegerField(default=7, help_text="How many daily backups to keep")
    backup_hour = models.PositiveSmallIntegerField(default=2, help_text="Hour of day (server time) for the daily backup")
    backups_enabled = models.BooleanField(default=True)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    last_backup_error = models.TextField(blank=True)

    @classmethod
    def load(cls) -> "FirmSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ChoiceList(models.Model):
    """An editable dropdown list, e.g. Sectors. Fields reference lists by key."""

    key = models.SlugField(max_length=60, unique=True)
    label = models.CharField(max_length=100)
    module = models.CharField(max_length=30, blank=True, help_text="Blank = shared across modules")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return self.label


class ChoiceOption(models.Model):
    """One option in a list. Records point at options by ID so renames propagate.
    Removing an option deactivates it: existing records keep it, new ones can't pick it."""

    choice_list = models.ForeignKey(ChoiceList, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FieldSwitch(models.Model):
    """Firm-wide on/off switch for one field. Missing row = field is on."""

    module = models.CharField(max_length=30, choices=MODULES)
    field_key = models.CharField(max_length=60)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("module", "field_key")]


class Record(models.Model):
    """Abstract base for every main record: stamps and archive.

    `objects` returns every row (so backups include archived records); API querysets
    filter out archived rows explicitly."""

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Keep each money field's normalised value (amount x unit) in step.
        from .registry import money_value

        for f in self._meta.concrete_fields:
            if f.name.endswith("_value"):
                prefix = f.name[: -len("_value")]
                if hasattr(self, f"{prefix}_amount") and hasattr(self, f"{prefix}_unit"):
                    setattr(
                        self,
                        f.name,
                        money_value(getattr(self, f"{prefix}_amount"), getattr(self, f"{prefix}_unit")),
                    )
        super().save(*args, **kwargs)
