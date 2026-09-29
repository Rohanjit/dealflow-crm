from django.dispatch import receiver

from core.pipeline import stage_changed


@receiver(stage_changed)
def mandate_won(sender, record, event, user, importing, **kwargs):
    """An outreach attempt reaching Mandate won becomes an active deal at Data collection."""
    if sender == "outreach" and record.stage == "won" and record.status == "open":
        from .conversion import deal_from_outreach

        deal_from_outreach(record, user)
