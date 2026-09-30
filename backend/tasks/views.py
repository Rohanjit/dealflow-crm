from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .fields import related_title
from .models import Task


def _item(t: Task) -> dict:
    link = None
    for module, attr in (("deals", "deal"), ("outreach", "outreach"), ("investors", "investor"), ("contacts", "contact")):
        if getattr(t, f"{attr}_id") is not None:
            link = {"module": module, "id": getattr(t, f"{attr}_id"), "title": related_title(t)}
            break
    return {
        "id": t.pk,
        "title": t.title,
        "due": t.due.isoformat() if t.due else None,
        "due_time": t.due_time.strftime("%H:%M") if t.due_time else None,
        "priority": t.priority,
        "status": t.status,
        "related": link,
    }


class TaskAlertsView(APIView):
    """Reminders for the signed-in person: their unfinished tasks that are overdue or due today.
    The app polls this every minute for the bell and for "due now" notices. Times are the firm's
    local time (the server's time zone), and `now` is sent so screens group tasks the same way."""

    def get(self, request):
        if not request.user.can("tasks", "view"):
            raise PermissionDenied("Your role can't see Tasks & Reminders.")
        now = timezone.localtime().replace(second=0, microsecond=0)
        today, clock = now.date(), now.time()
        mine = (
            Task.objects.filter(archived_at__isnull=True, assigned_to=request.user, due__lte=today)
            .exclude(status="done")
            .select_related("deal__company", "outreach__company", "investor", "contact")
            .order_by("due", "due_time", "pk")
        )
        overdue, due_today = [], []
        for t in mine:
            late = t.due < today or (t.due_time is not None and t.due_time < clock)
            (overdue if late else due_today).append(_item(t))
        return Response({
            "now": now.strftime("%Y-%m-%dT%H:%M"),
            "overdue": overdue,
            "today": due_today,
            "count": len(overdue) + len(due_today),
        })
