"""A room file's encrypted copy goes when the room file does (removed, its Vault document deleted for
good, or its deal deleted for good)."""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import RoomFile


@receiver(post_delete, sender=RoomFile)
def remove_copy(sender, instance: RoomFile, **kwargs):
    if instance.stored:
        instance.stored.delete(save=False)
