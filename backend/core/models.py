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
    # Home page: how far ahead "near target close" and "mandate nearing expiry" look.
    dashboard_days = models.PositiveSmallIntegerField(default=30)
    # Signed out after this many minutes without activity (accounts/sessions.py); 15 to 480.
    idle_minutes = models.PositiveSmallIntegerField(default=30)
    # Relationships "going cold": not contacted for this many days (0 = no alerts). See core/stale.py.
    stale_investor_days = models.PositiveSmallIntegerField(default=90)
    stale_contact_days = models.PositiveSmallIntegerField(default=60)
    # "firm" or "individual" (one person on their own computer): only changes wording and hides
    # team-only prompts; permissions work the same.
    workspace_kind = models.CharField(max_length=12, default="firm", choices=[("firm", "Firm"), ("individual", "Just me")])
    # Set while the install holds the guest sample data (core/guest.py) instead of real data.
    sample_loaded_at = models.DateTimeField(null=True, blank=True)
    # Changes whenever a module's fields or layout change, so every worker rebuilds its field lists.
    layout_token = models.CharField(max_length=32, blank=True)
    # Stuck alerts and follow-up nudges (core/stuck.py, core/nudges.py). Overrides are days per
    # stage, {"outreach": {"meeting": 30}}; 0 = never flag that stage.
    stuck_alerts = models.BooleanField(default=True)
    stuck_overrides = models.JSONField(default=dict, blank=True)
    nudge_meeting_task = models.BooleanField(default=True)
    nudge_tracker_days = models.PositiveSmallIntegerField(default=10)
    nudge_quiet_days = models.PositiveSmallIntegerField(default=14)

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


class CustomField(models.Model):
    """A field a firm added to a module in Settings > Fields & layout. Its values live in each
    record's `custom` JSON column under `key` ("cf_<id>"). Deleting one only hides it (`active`)."""

    TYPES = [
        ("text", "Short text"), ("longtext", "Long text"), ("integer", "Whole number"), ("decimal", "Number with decimals"),
        ("percent", "Percentage"), ("money", "Money"), ("date", "Date"), ("datetime", "Date and time"),
        ("boolean", "Yes / no"), ("tristate", "Yes / no / not known"), ("dropdown", "Dropdown"), ("user", "Person"),
        ("email", "Email"), ("phone", "Phone"), ("url", "Web address"), ("tags", "Tags"), ("link", "Link to a record"),
    ]

    module = models.CharField(max_length=30)
    label = models.CharField(max_length=80)
    type = models.CharField(max_length=12, choices=TYPES)
    multi = models.BooleanField(default=False)  # dropdown, person, link: several values
    targets = models.JSONField(default=list, blank=True)  # link: modules it can point at
    choice_list = models.ForeignKey(ChoiceList, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    required = models.BooleanField(default=False)
    help = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["pk"]

    @property
    def key(self) -> str:
        return f"cf_{self.pk}"


class ModuleLayout(models.Model):
    """How one module's fields are laid out when the firm has customised it. With `customised`
    off the module shows its original layout (custom fields hidden, data kept). See core/layout.py.

    sections: [{"key", "label"}] in order · items: [{"field", "section"}] in order (built-in,
    custom and linked fields) · labels: {field: new label} · hidden: built-in fields switched off."""

    module = models.CharField(max_length=30, unique=True)
    customised = models.BooleanField(default=False)
    sections = models.JSONField(default=list, blank=True)
    items = models.JSONField(default=list, blank=True)
    labels = models.JSONField(default=dict, blank=True)
    hidden = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")


class FieldSwitch(models.Model):
    """Firm-wide on/off switch for one field. Missing row = field is on."""

    module = models.CharField(max_length=30, choices=MODULES)
    field_key = models.CharField(max_length=60)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("module", "field_key")]


def money_columns(verbose: str):
    """amount / currency / unit, plus a normalised value (amount x unit) for sorting and range
    filters within one currency. Use: a_amount, a_currency, a_unit, a_value = money_columns("A")."""
    from .registry import CURRENCIES, UNITS

    return (
        models.DecimalField(verbose, max_digits=20, decimal_places=4, null=True, blank=True),
        models.CharField(max_length=3, choices=CURRENCIES, blank=True),
        models.CharField(max_length=5, choices=UNITS, blank=True),
        models.DecimalField(max_digits=30, decimal_places=4, null=True, blank=True, editable=False),
    )


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
    # Values of the firm's custom fields, {"cf_12": value} (core/custom.py).
    custom = models.JSONField(default=dict, blank=True)

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


class Note(models.Model):
    """A dated entry in a record's interaction log (outreach, deals): something someone did or
    heard, or an automatic stage change. Linked by module key + record id so every module
    with a timeline shares one table."""

    KINDS = [("note", "Note"), ("call", "Call"), ("meeting", "Meeting"), ("email", "Email"), ("stage", "Stage change")]

    module = models.CharField(max_length=30)
    record_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=10, choices=KINDS, default="note")
    date = models.DateField()
    text = models.TextField(blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        indexes = [models.Index(fields=["module", "record_id"])]


class AlertSnooze(models.Model):
    """Someone set aside a stuck alert or a follow-up nudge until a date (core/nudges.py). Kinds:
    "stuck:<stage>" (ends early when the record changes stage), "quiet", "tracker" (per deal) and
    "meeting" (per log entry; "No follow-up needed" sets it for good)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=60)
    module = models.CharField(max_length=30)
    record_id = models.PositiveBigIntegerField()
    until = models.DateField()

    class Meta:
        unique_together = [("user", "kind", "module", "record_id")]
