from django.conf import settings
from django.db import models


class SavedReport(models.Model):
    """A custom export someone saved to run again (Settings > Export, decision 61). Private to its
    owner unless shared; whoever runs it only ever gets the records and fields they can see."""

    name = models.CharField(max_length=120)
    module = models.CharField(max_length=30)
    # {columns, filters, ordering, format, layout, title}: see reports/build.py clean_config.
    config = models.JSONField(default=dict)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports")
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name
