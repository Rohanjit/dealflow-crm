from django.utils import timezone

from core.registry import FieldDef, ModuleDef, active_targets, get_module, register

from .models import PRIORITIES, STATUSES, Task

T, W, R = "Task", "When and who", "Related to"

FIELDS = [
    FieldDef("title", "Title", "text", T, required=True, list_default=True, searchable=True,
             aliases=["task", "to do", "todo", "action", "subject"]),
    FieldDef("description", "Description", "longtext", T, searchable=True, aliases=["details", "notes"]),
    FieldDef("due", "Due", "datetime", W, list_default=True, filterable=True, always_on=True,
             help="Add a time to get a reminder at that time. A task with just a date is due that day.",
             aliases=["due date", "deadline", "by"]),
    FieldDef("priority", "Priority", "choice", W, choices=PRIORITIES, list_default=True, filterable=True,
             default="medium"),
    FieldDef("status", "Status", "choice", W, choices=STATUSES, list_default=True, filterable=True, always_on=True,
             default="open"),
    FieldDef("assigned_to", "Assigned to", "user", W, list_default=True, filterable=True, always_on=True,
             default_me=True, aliases=["owner", "assignee"]),
    FieldDef("related", "Related to", "link", R, list_default=True, filterable=True, always_on=True,
             targets={"outreach": "outreach", "deals": "deal", "investors": "investor", "contacts": "contact"},
             target_labels={"outreach": "Outreach", "deals": "Deals", "investors": "Investors", "contacts": "Contacts"},
             help="An outreach attempt, deal, investor or contact this task is about (optional)."),
    FieldDef("created_by", "Created by", "user", W, readonly=True, filterable=True),
    FieldDef("completed_on", "Completed on", "date", W, readonly=True, filterable=True),
]

VIEWS = [
    {"value": "mine", "label": "My tasks"},
    {"value": "open", "label": "All open"},
    {"value": "done", "label": "Done"},
    {"value": "all", "label": "All"},
]


def task_filters(qs, params, user=None):
    """?view=mine (the app's default: open tasks assigned to me) | open | done | all."""
    view = params.get("view")
    if view == "mine":
        qs = qs.filter(assigned_to=getattr(user, "pk", None)).exclude(status="done")
    elif view == "open":
        qs = qs.exclude(status="done")
    elif view == "done":
        qs = qs.filter(status="done")
    return qs


def related_title(t: Task) -> str:
    """The name of what the task is about (a deal and an outreach attempt are named after their company)."""
    f = next(f for f in FIELDS if f.key == "related")
    for mkey, attr in active_targets(f):
        if getattr(t, f"{attr}_id") is not None:
            return get_module(mkey).title_of(getattr(t, attr))
    return ""


def task_subtitle(t: Task) -> str:
    return " · ".join(p for p in (related_title(t), t.get_status_display()) if p)


def task_before_save(obj: Task, values: dict, user):
    """Tasks start with whoever adds them; finishing one records the day, reopening clears it."""
    if not obj.pk and "assigned_to" not in values and obj.assigned_to_id is None:
        obj.assigned_to = user
    if "status" in values:
        obj.completed_on = (obj.completed_on or timezone.localdate()) if obj.status == "done" else None


TASKS = register(
    ModuleDef(
        key="tasks",
        label="Tasks & Reminders",
        singular="Task",
        model=Task,
        fields=FIELDS,
        groups=[T, W, R],
        title_field="title",
        subtitle=task_subtitle,
        extra_filters=task_filters,
        segment_param="view",
        segment_options=VIEWS,
        segment_default="mine",
        before_save=task_before_save,
        importable=False,
    )
)

