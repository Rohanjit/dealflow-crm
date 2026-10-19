"""Field values in plain words for the audit log ("₹ 50 Cr", "Term sheet", "Vikram Shah")."""

from __future__ import annotations

from .records import read_value
from .registry import FieldDef, ModuleDef


def display(f: FieldDef, v) -> str:
    from django.contrib.auth import get_user_model

    from .models import ChoiceOption

    if v in (None, "", [], {}):
        return ""
    t = f.type
    if t == "money":
        if not isinstance(v, dict) or v.get("amount") in (None, ""):
            return ""
        return " ".join(p for p in (v.get("currency") or "", str(v["amount"]), v.get("unit") or "") if p)
    if t == "dropdown":
        ids = v if isinstance(v, list) else [v]
        labels = dict(ChoiceOption.objects.filter(pk__in=ids).values_list("pk", "label"))
        return ", ".join(labels.get(i, f"#{i}") for i in ids)
    if t == "user":
        ids = v if isinstance(v, list) else [v]
        names = dict(get_user_model().objects.filter(pk__in=ids).values_list("pk", "full_name"))
        return ", ".join(names.get(i, f"#{i}") for i in ids)
    if t == "link":
        items = v if isinstance(v, list) else [v]
        return ", ".join(f"{i.get('title')}" for i in items if isinstance(i, dict))
    if t == "tags":
        return ", ".join(v)
    if t in ("choice", "tristate"):
        return dict(f.choices or []).get(v, str(v))
    if t == "boolean":
        return "Yes" if v else "No"
    if t == "percent":
        return f"{v}%"
    s = str(v)
    return s if len(s) <= 2000 else s[:2000] + "…"


def snapshot(obj, mod: ModuleDef, keys) -> dict:
    """Current values of some fields, in plain words (empty for a record not saved yet)."""
    if obj.pk is None:
        return {}
    out = {}
    for k in keys:
        f = mod.field_map.get(k)
        if f is not None and not f.via:
            try:
                out[k] = display(f, read_value(obj, f))
            except Exception:  # a value that can't be read shouldn't stop the save
                out[k] = ""
    return out


def diff(mod: ModuleDef, before: dict, after: dict) -> list[dict]:
    return [{"field": mod.field_map[k].label, "from": before.get(k, ""), "to": v}
            for k, v in after.items() if before.get(k, "") != v]
