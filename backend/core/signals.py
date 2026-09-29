"""Housekeeping shared by every module."""

from django.db.models.signals import post_delete
from django.dispatch import receiver


@receiver(post_delete)
def drop_interaction_log(sender, instance, **kwargs):
    """A record deleted for good takes its interaction log with it, including records removed along
    with another one (a deal's investor tracker when the deal is deleted)."""
    from .models import Note
    from .registry import all_modules

    for mod in all_modules():
        if mod.timeline and mod.model is sender:
            Note.objects.filter(module=mod.key, record_id=instance.pk).delete()
