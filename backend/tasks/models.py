from django.conf import settings
from django.db import models

from core.models import Record

PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High")]
STATUSES = [("open", "Open"), ("in_progress", "In progress"), ("done", "Done")]


class Task(Record):
    """Something someone has to do, optionally about an outreach attempt, a deal, an investor or a
    contact. Due dates can carry a time of day; reminders show in the app when a task falls due."""

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITIES, default="medium")
    status = models.CharField(max_length=12, choices=STATUSES, default="open")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_on = models.DateField(null=True, blank=True)

    # What it's about (one of these at most). Deleting that record for good unlinks the task.
    outreach = models.ForeignKey("outreach.Outreach", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    deal = models.ForeignKey("deals.Deal", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    investor = models.ForeignKey("investors.Investor", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")

    class Meta:
        ordering = ["due", "due_time", "-created_at"]
        indexes = [models.Index(fields=["assigned_to", "status", "due"])]

    def __str__(self):
        return self.title
