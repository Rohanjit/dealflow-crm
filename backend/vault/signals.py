from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Document


@receiver(post_delete, sender=Document)
def remove_file(sender, instance, **kwargs):
    """Deleting a document for good (from the Archive) removes its file from the server too."""
    if instance.file:
        instance.file.delete(save=False)
