"""Custom fields (decision 57): definitions become FieldDefs, values live in each record's `custom`
JSON column under "cf_<id>". Validation is the same as for built-in fields (records.clean_one);
this module turns cleaned values into JSON and back, and builds filters and sort orders on them.

Stored as: text → str · whole number → int · decimal and percentage → number · money →
{amount, currency, unit, value (amount × unit)} · date → "YYYY-MM-DD" · date and time →
"YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" · yes/no → bool · yes/no/not known → "yes" | "no" | "unknown" ·
dropdown, person → id (or [ids]) · tags → [names] · link → "module:id" (or a list). Empty = no key.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.fields.json import KeyTextTransform, KeyTransform

from .registry import UNIT_MULTIPLIER, FieldDef, get_module

TEXT_TYPES = ("text", "longtext", "email", "phone", "url")


def field_def(cf, group: str, via: str | None = None, via_module: str | None = None, readonly=False) -> FieldDef:
    t = cf.type
    multi = cf.multi and t in ("dropdown", "user", "link")
    return FieldDef(
        cf.key, cf.label, t, group,
        required=cf.required and not via,
        choice_list=cf.choice_list.key if t == "dropdown" and cf.choice_list_id else None,
        multi=multi,
        targets={m: m for m in cf.targets} if t == "link" else None,
        help=cf.help,
        filterable=t != "longtext",
        searchable=t in TEXT_TYPES,
        sortable=not multi and t not in ("tags", "longtext", "link"),
        allow_negative=True,
        custom_key=cf.key,
        via=via,
        via_module=via_module,
        readonly=readonly,
    )


# ---------------------------------------------------------------- reading


def _raw(obj, f: FieldDef):
    return (obj.custom or {}).get(f.custom_key) if obj is not None else None


def _link_item(token: str) -> dict | None:
    from .records import _link_item as built_in_item

    mkey, _, pk = str(token).partition(":")
    try:
        tmod = get_module(mkey)
    except KeyError:
        return None
    target = _target(tmod.key, int(pk)) if pk.isdigit() else None
    return built_in_item(mkey, target) if target is not None else None


def _target(mkey: str, pk: int):
    """A linked record, looked up once per request."""
    from . import current

    req = current._request.get()
    cache = req.__dict__.setdefault("_df_targets", {}) if req is not None else {}
    if (mkey, pk) not in cache:
        cache[(mkey, pk)] = get_module(mkey).model.objects.filter(pk=pk).first()
    return cache[(mkey, pk)]


def read(obj, f: FieldDef):
    from .records import dec_str

    v = _raw(obj, f)
    t = f.type
    if t in TEXT_TYPES:
        return v or ""
    if t in ("decimal", "percent"):
        return dec_str(Decimal(str(v))) if v is not None else None
    if t == "money":
        if not isinstance(v, dict) or v.get("amount") is None:
            return {"amount": None, "currency": "", "unit": ""}
        return {"amount": dec_str(Decimal(str(v["amount"]))), "currency": v.get("currency", ""), "unit": v.get("unit", "")}
    if t == "boolean":
        return bool(v)
    if t == "tristate":
        return v or "unknown"
    if t in ("dropdown", "user"):
        return (list(v) if isinstance(v, list) else []) if f.multi else v
    if t == "tags":
        return list(v) if isinstance(v, list) else []
    if t == "link":
        if f.multi:
            return [i for i in (_link_item(x) for x in (v or [])) if i]
        return _link_item(v) if v else None
    return v


def current_ids(obj, f: FieldDef) -> set:
    """Dropdown options / people / linked records a saved record has now (they may stay even if
    since removed or deactivated)."""
    v = _raw(obj, f) if obj is not None and obj.pk else None
    if v is None:
        return set()
    items = v if isinstance(v, list) else [v]
    if f.type == "link":
        out = set()
        for x in items:
            m, _, pk = str(x).partition(":")
            if pk.isdigit():
                out.add((m, int(pk)))
        return out
    return {x for x in items if isinstance(x, int)}


# ---------------------------------------------------------------- saving


def store(f: FieldDef, v):
    """A cleaned value (records.clean_one) in its JSON form, or None for empty."""
    t = f.type
    if v in (None, "", []):
        return None
    if t in TEXT_TYPES or t == "tristate":
        return None if t == "tristate" and v == "unknown" else v
    if t == "integer":
        return int(v)
    if t in ("decimal", "percent"):
        return float(v)
    if t == "money":
        if v.get("amount") is None:
            return None
        amount = Decimal(v["amount"])
        return {"amount": float(amount), "currency": v["currency"], "unit": v["unit"],
                "value": float(amount * UNIT_MULTIPLIER.get(v["unit"] or "", Decimal(1)))}
    if t == "date":
        return v.isoformat()
    if t == "datetime":
        day, at = v
        return f"{day.isoformat()}T{at.strftime('%H:%M')}" if at else day.isoformat()
    if t == "boolean":
        return bool(v) or None
    if t in ("dropdown", "user"):
        return [x.pk for x in v] if f.multi else v.pk
    if t == "tags":
        return list(v)
    if t == "link":
        return [f"{m}:{x.pk}" for m, x in v] if f.multi else f"{v[0]}:{v[1].pk}"
    return v


def apply(obj, f: FieldDef, v):
    data = dict(obj.custom or {})
    stored = store(f, v)
    if stored is None:
        data.pop(f.custom_key, None)
    else:
        data[f.custom_key] = stored
    obj.custom = data


# ---------------------------------------------------------------- filters and sorting


def _col(f: FieldDef) -> str:
    return f"{f.via}__custom" if f.via else "custom"


def _key(f: FieldDef, sub: str | None = None):
    k = KeyTransform(f.custom_key, _col(f))
    return KeyTransform(sub, k) if sub else k


def _text(f: FieldDef):
    return KeyTextTransform(f.custom_key, _col(f))


def _in_list(f: FieldDef, item) -> Q:
    """A JSON list holding `item` (works on SQLite and Postgres: matched on the list's text)."""
    token = re.escape(json.dumps(item, ensure_ascii=False))
    return Q(**{f"{f.path()}__iregex": rf"(^|[\[, ]){token}($|[,\]])"})


def apply_filter(qs, f: FieldDef, params, user):
    from .records import _csv, _ints, _to_date, _to_decimal, FieldError

    k, t, col = f.key, f.type, f.path()
    raw = params.get(k)
    if t in TEXT_TYPES and raw:
        return qs.filter(**{f"{col}__icontains": raw})
    if t in ("dropdown", "user") and raw:
        tokens = _csv(raw)
        ids = [user.pk if p == "me" else p for p in tokens if p != "none"] if t == "user" else tokens
        ids = _ints(",".join(str(i) for i in ids))
        cond = Q(pk__in=[])
        for i in ids:
            cond |= _in_list(f, i) if f.multi else Q(**{col: i})
        if "none" in tokens:
            cond |= Q(**{f"{col}__isnull": True})
        return qs.filter(cond)
    if t == "tristate" and (vals := _csv(raw or "")):
        cond = Q(**{f"{col}__in": [v for v in vals if v != "unknown"]})
        if "unknown" in vals:
            cond |= Q(**{f"{col}__isnull": True})
        return qs.filter(cond)
    if t == "boolean" and raw in ("true", "false"):
        return qs.filter(**{col: True}) if raw == "true" else qs.exclude(**{col: True})
    if t == "tags" and (names := _csv(raw or "")):
        cond = Q(pk__in=[])
        for n in names:
            cond |= _in_list(f, n)
        return qs.filter(cond)
    if t == "link" and raw:
        cond = Q(pk__in=[])
        for token in _csv(raw):
            m, _, pk = token.partition(":")
            if f.targets and m in f.targets and pk.isdigit():
                cond |= _in_list(f, f"{m}:{int(pk)}") if f.multi else Q(**{col: f"{m}:{int(pk)}"})
        return qs.filter(cond)
    if t in ("integer", "decimal", "percent", "money", "date", "datetime"):
        dated = t in ("date", "datetime")
        lo_key, hi_key = (f"{k}_from", f"{k}_to") if dated else (f"{k}_min", f"{k}_max")
        target = f"{col}__value" if t == "money" else col
        for pk_, op in ((lo_key, "gte"), (hi_key, "lte")):
            if not (r := params.get(pk_)):
                continue
            try:
                val = (_to_date if dated else _to_decimal)(r)
            except FieldError:
                continue
            if val is None:
                continue
            if dated:
                val = val.isoformat() + ("T99" if op == "lte" and t == "datetime" else "")
            else:
                if t == "money":
                    val = val * UNIT_MULTIPLIER.get(params.get(f"{k}_unit", ""), Decimal(1))
                    if cur := params.get(f"{k}_currency"):
                        qs = qs.filter(**{f"{col}__currency": cur})
                if abs(val) >= Decimal(10) ** 18:
                    continue
                val = float(val)
            qs = qs.filter(**{f"{target}__{op}": val})
        return qs
    return qs


def order_terms(f: FieldDef, desc: bool) -> list:
    from django.contrib.auth import get_user_model

    from .models import ChoiceOption

    def direction(expr):
        return expr.desc(nulls_last=True) if desc else expr.asc(nulls_last=True)

    t = f.type
    if t == "money":
        empty = Case(When(**{f"{f.path()}__value__isnull": True}, then=Value(1)), default=Value(0), output_field=IntegerField())
        return [empty, _key(f, "currency").asc(), direction(_key(f, "value"))]
    if t in ("dropdown", "user"):
        # Dropdowns sort in the list's own order, people by name: a position for each id.
        if t == "dropdown":
            ids = ChoiceOption.objects.filter(choice_list__key=f.choice_list).order_by("order", "pk").values_list("pk", flat=True)
        else:
            ids = get_user_model().objects.order_by("full_name", "pk").values_list("pk", flat=True)
        position = Case(*[When(**{f.path(): pk}, then=Value(i)) for i, pk in enumerate(ids)],
                        default=Value(None), output_field=IntegerField())
        return [direction(position)]
    if t == "tristate":
        position = Case(*[When(**{f.path(): v}, then=Value(i)) for i, (v, _) in enumerate(f.choices or [])],
                        default=Value(None), output_field=IntegerField())
        return [direction(position)]
    return [direction(_key(f))]


def search_q(f: FieldDef, q: str) -> Q:
    return Q(**{f"{f.path()}__icontains": q})


# ---------------------------------------------------------------- links to merged or deleted records


def _link_fields_to(module: str):
    from .models import CustomField

    return [cf for cf in CustomField.objects.filter(type="link") if module in (cf.targets or [])]


def repoint_links(module: str, old_pk: int, new_pk: int | None):
    """Custom link fields pointing at `module:old_pk` now point at `new_pk` (merge), or at nothing
    (deleted for good)."""
    old, new = f"{module}:{old_pk}", (f"{module}:{new_pk}" if new_pk else None)
    for cf in _link_fields_to(module):
        try:
            mod = get_module(cf.module)
        except KeyError:
            continue
        for rec in mod.model.objects.filter(custom__icontains=old):
            v = (rec.custom or {}).get(cf.key)
            if v is None or old not in json.dumps(v):
                continue
            if isinstance(v, list):
                items = [x for x in v if x != old]
                if new and new not in items:
                    items.append(new)
                rec.custom = {**rec.custom, cf.key: items} if items else {k: x for k, x in rec.custom.items() if k != cf.key}
            elif v == old:
                rec.custom = {**rec.custom, cf.key: new} if new else {k: x for k, x in rec.custom.items() if k != cf.key}
            rec.save(update_fields=["custom"])
