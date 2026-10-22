"""Contact capture (decision 64): people someone has emailed who aren't in Dealflow yet, kept privately
for that person as suggestions. Only the address, the name in the email and when they last emailed
are kept; the signature is read from the mailbox when they choose to add the person."""

from django.conf import settings
from django.db import models


class Stranger(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    email = models.CharField(max_length=255)
    name = models.CharField(max_length=200, blank=True)
    count = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    last_subject = models.CharField(max_length=300, blank=True)
    wrote_to_them = models.BooleanField(default=False)  # this person emailed them (not just received)
    # Their latest email to this person, so the signature can be read when adding them.
    connection = models.ForeignKey("sync.Connection", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    remote_id = models.CharField(max_length=1000, blank=True)
    dismissed = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "email")]
        ordering = ["-last_seen", "-id"]
