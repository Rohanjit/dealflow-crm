"""Generic read / validate / write / filter for any registered module."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.db import transaction
from django.db.models import Q, QuerySet

from .models import ChoiceOption, Tag
from .registry import CURRENCY_CODES, UNIT_MULTIPLIER, FieldDef, ModuleDef

User = get_user_model()

EMPTY = (None, "", [])


# ---------------------------------------------------------------- reading


def dec_str(d: Decimal | None) -> str | None:
    if d is None:
        return None
    s = format(d.normalize(), "f")
    return s


def read_value(obj, f: FieldDef):
    t = f.type
    if t == "money":
        return {
            "amount": dec_str(getattr(obj, f"{f.key}_amount")),
            "currency": getattr(obj, f"{f.key}_currency") or "",
            "unit": getattr(obj, f"{f.key}_unit") or "",
        }
    if t == "dropdown":
        if f.multi:
            return [o.pk for o in getattr(obj, f.key).all()]
        return getattr(obj, f"{f.key}_id")
    if t == "tags":
        return [tag.name for tag in getattr(obj, f.key).all()]
    if t == "user":
        return getattr(obj, f"{f.key}_id")
    v = getattr(obj, f.key)
    if t in ("decimal", "percent"):
        return dec_str(v)
    if t == "date":
        return v.isoformat() if v else None
    return v


def _user_ref(u):
    return {"id": u.pk, "name": u.full_name} if u else None


def serialize(obj, mod: ModuleDef) -> dict:
    out = {"id": obj.pk, "_title": getattr(obj, mod.title_field)}
    for f in mod.fields:
        out[f.key] = read_value(obj, f)
    out["_stamps"] = {
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "created_by": _user_ref(obj.created_by),
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        "updated_by": _user_ref(obj.updated_by),
        "archived_at": obj.archived_at.isoformat() if obj.archived_at else None,
        "archived_by": _user_ref(obj.archived_by),
    }
    return out


def with_related(qs: QuerySet, mod: ModuleDef) -> QuerySet:
    select = ["created_by", "updated_by", "archived_by"]
    prefetch = []
    for f in mod.fields:
        if f.is_m2m:
            prefetch.append(f.key)
        elif f.type in ("dropdown", "user"):
            select.append(f.key)
    return qs.select_related(*select).prefetch_related(*prefetch)


# ---------------------------------------------------------------- validating


class FieldError(Exception):
    pass


def _is_empty(v) -> bool:
    if isinstance(v, dict):
        return v.get("amount") in EMPTY
    return v in EMPTY


def _to_decimal(v) -> Decimal | None:
    if v in EMPTY:
        return None
    if isinstance(v, (int, float)):
        v = str(v)
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, TypeError):
        raise FieldError("Enter a number.")
    if not d.is_finite():
        raise FieldError("Enter a number.")
    return d


def _to_date(v) -> dt.date | None:
    if v in EMPTY:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        raise FieldError("Enter a date as YYYY-MM-DD.")


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0", ""):
        return False
    raise FieldError("Enter yes or no.")


def _option(f: FieldDef, raw, current_ids: set[int]) -> ChoiceOption:
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        raise FieldError("Pick an option from the list.")
    opt = ChoiceOption.objects.filter(pk=pk, choice_list__key=f.choice_list).first()
    if not opt:
        raise FieldError("Pick an option from the list.")
    if not opt.is_active and pk not in current_ids:
        raise FieldError(f"“{opt.label}” has been removed from the list.")
    return opt


def clean_one(f: FieldDef, v, instance=None):
    t = f.type
    if t in ("text", "phone"):
        s = "" if v is None else str(v).strip()
        if len(s) > f.max_length:
            raise FieldError(f"Keep this under {f.max_length} characters.")
        return s
    if t == "longtext":
        return "" if v is None else str(v).strip()
    if t == "url":
        s = "" if v is None else str(v).strip()
        if not s:
            return ""
        if "://" not in s:
            s = "https://" + s
        try:
            URLValidator(schemes=["http", "https"])(s)
        except ValidationError:
            raise FieldError("Enter a valid web address.")
        return s[: f.max_length]
    if t == "email":
        s = "" if v is None else str(v).strip()
        if s:
            try:
                EmailValidator()(s)
            except ValidationError:
                raise FieldError("Enter a valid email address.")
        return s
    if t == "integer":
        d = _to_decimal(v)
        if d is None:
            return None
        if d != d.to_integral_value():
            raise FieldError("Enter a whole number.")
        return int(d)
    if t in ("decimal", "percent"):
        d = _to_decimal(v)
        if d is not None and t == "percent" and not (0 <= d <= 100):
            raise FieldError("Enter a percentage between 0 and 100.")
        return d
    if t == "date":
        return _to_date(v)
    if t == "boolean":
        return _to_bool(v)
    if t in ("tristate", "choice"):
        s = "" if v is None else str(v).strip()
        if not s:
            return "unknown" if t == "tristate" else ""
        for value, label in f.choices or []:
            if s == value or s.lower() == label.lower() or s.lower() == value.lower():
                return value
        raise FieldError("Pick one of the listed options.")
    if t == "dropdown":
        if f.multi:
            current = set(getattr(instance, f.key).values_list("pk", flat=True)) if instance and instance.pk else set()
            if v in EMPTY:
                return []
            if not isinstance(v, (list, tuple)):
                v = [v]
            seen, out = set(), []
            for raw in v:
                opt = _option(f, raw, current)
                if opt.pk not in seen:
                    seen.add(opt.pk)
                    out.append(opt)
            return out
        if v in EMPTY:
            return None
        current = {getattr(instance, f"{f.key}_id")} if instance and instance.pk else set()
        return _option(f, v, current)
    if t == "user":
        if v in EMPTY:
            return None
        try:
            pk = int(v)
        except (TypeError, ValueError):
            raise FieldError("Pick a user.")
        user = User.objects.filter(pk=pk).first()
        if not user:
            raise FieldError("Pick a user.")
        current = getattr(instance, f"{f.key}_id") if instance and instance.pk else None
        if not user.is_active and user.pk != current:
            raise FieldError(f"{user.full_name} is deactivated.")
        return user
    if t == "money":
        if v in EMPTY:
            return {"amount": None, "currency": "", "unit": ""}
        if not isinstance(v, dict):
            raise FieldError("Enter an amount, currency and unit.")
        amount = _to_decimal(v.get("amount"))
        currency = (v.get("currency") or "").strip().upper()
        unit = (v.get("unit") or "").strip()
        if amount is None:
            return {"amount": None, "currency": "", "unit": ""}
        if amount < 0:
            raise FieldError("Amount can't be negative.")
        if not currency:
            raise FieldError("Pick a currency.")
        if currency not in CURRENCY_CODES:
            raise FieldError(f"Unknown currency {currency}.")
        unit_match = next((u for u in UNIT_MULTIPLIER if u.lower() == unit.lower()), None)
        if unit_match is None:
            raise FieldError(f"Unknown unit {unit}.")
        return {"amount": amount, "currency": currency, "unit": unit_match}
    if t == "tags":
        if v in EMPTY:
            return []
        if isinstance(v, str):
            v = [p for p in v.split(",")]
        seen, out = set(), []
        for raw in v:
            name = str(raw).strip()[:60]
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        return out
    raise FieldError("Unsupported field")


def clean_values(mod: ModuleDef, data: dict, *, enabled: dict, instance=None, partial=False):
    """Validate incoming API data. Returns (values, errors). Disabled fields are ignored."""
    values, errors = {}, {}
    for f in mod.fields:
        if not enabled.get(f.key, True):
            continue
        if f.key not in data:
            if f.required and not partial and not (instance and instance.pk):
                errors[f.key] = "This field is required."
            continue
        try:
            v = clean_one(f, data[f.key], instance)
        except FieldError as e:
            errors[f.key] = str(e)
            continue
        if f.required and _is_empty(v):
            errors[f.key] = "This field is required."
            continue
        values[f.key] = v
    # Cross-field rule shared by modules: min must not exceed max for paired money fields.
    for f in mod.fields:
        other = "max_" + f.key[4:]
        if f.type != "money" or not f.key.startswith("min_") or other not in mod.field_map:
            continue
        if f.key not in values and other not in values:
            continue
        lo = values[f.key] if f.key in values else _current_money(instance, f.key)
        hi = values[other] if other in values else _current_money(instance, other)
        if (
            lo and hi and lo["amount"] is not None and hi["amount"] is not None
            and lo["currency"] == hi["currency"]
            and lo["amount"] * UNIT_MULTIPLIER[lo["unit"] or ""] > hi["amount"] * UNIT_MULTIPLIER[hi["unit"] or ""]
        ):
            errors.setdefault(other, f"Must be at least the {mod.field(f.key).label.lower()}.")
    return values, errors


def _current_money(instance, key):
    if not instance or not instance.pk:
        return None
    return {
        "amount": getattr(instance, f"{key}_amount"),
        "currency": getattr(instance, f"{key}_currency"),
        "unit": getattr(instance, f"{key}_unit"),
    }


# ---------------------------------------------------------------- writing


def tags_from_names(names: list[str]) -> list[Tag]:
    out = []
    for name in names:
        tag = Tag.objects.filter(name__iexact=name).first() or Tag.objects.create(name=name)
        out.append(tag)
    return out


@transaction.atomic
def apply_values(obj, mod: ModuleDef, values: dict, user):
    m2m = {}
    for key, v in values.items():
        f = mod.field(key)
        if f.type == "money":
            setattr(obj, f"{key}_amount", v["amount"])
            setattr(obj, f"{key}_currency", v["currency"])
            setattr(obj, f"{key}_unit", v["unit"])
        elif f.is_m2m:
            m2m[key] = tags_from_names(v) if f.type == "tags" else v
        else:
            setattr(obj, key, v)
    if not obj.pk:
        obj.created_by = user
    obj.updated_by = user
    obj.save()
    for key, items in m2m.items():
        getattr(obj, key).set(items)
    return obj


# ---------------------------------------------------------------- filtering


def _csv(v: str) -> list[str]:
    return [p.strip() for p in (v or "").split(",") if p.strip()]


def _ints(v: str) -> list[int]:
    out = []
    for p in _csv(v):
        try:
            out.append(int(p))
        except ValueError:
            pass
    return out


def filter_queryset(qs: QuerySet, mod: ModuleDef, params, user, enabled: dict) -> QuerySet:
    q = (params.get("q") or "").strip()
    if q:
        cond = Q()
        for f in mod.fields:
            if f.searchable and enabled.get(f.key, True):
                cond |= Q(**{f"{f.key}__icontains": q})
        qs = qs.filter(cond)

    distinct = False
    for f in mod.fields:
        if not f.filterable or not enabled.get(f.key, True):
            continue
        k, t = f.key, f.type
        if t == "dropdown" and (ids := _ints(params.get(k, ""))):
            qs = qs.filter(**{f"{k}__in": ids})
            distinct = distinct or f.multi
        elif t == "user" and (raw := params.get(k)):
            ids = [user.pk if p == "me" else p for p in _csv(raw)]
            none = "none" in ids
            ids = [i for i in ids if i != "none"]
            cond = Q(**{f"{k}__in": _ints(",".join(str(i) for i in ids))}) if ids else Q(pk__in=[])
            if none:
                cond |= Q(**{f"{k}__isnull": True})
            qs = qs.filter(cond)
        elif t in ("choice", "tristate") and (vals := _csv(params.get(k, ""))):
            qs = qs.filter(**{f"{k}__in": vals})
        elif t == "boolean" and params.get(k) in ("true", "false"):
            qs = qs.filter(**{k: params.get(k) == "true"})
        elif t == "tags" and (names := _csv(params.get(k, ""))):
            cond = Q()
            for n in names:
                cond |= Q(**{f"{k}__name__iexact": n})
            qs = qs.filter(cond)
            distinct = True
        elif t in ("text",) and (raw := params.get(k)):
            qs = qs.filter(**{f"{k}__icontains": raw})
        elif t in ("integer", "decimal", "percent", "money", "date"):
            col = f"{k}_value" if t == "money" else k
            lo_key, hi_key = (f"{k}_from", f"{k}_to") if t == "date" else (f"{k}_min", f"{k}_max")
            conv = _to_date if t == "date" else _to_decimal
            for pk, op in ((lo_key, "gte"), (hi_key, "lte")):
                if raw := params.get(pk):
                    try:
                        val = conv(raw)
                    except FieldError:
                        continue
                    if t == "money":
                        val = val * UNIT_MULTIPLIER.get(params.get(f"{k}_unit", ""), Decimal(1))
                        if cur := params.get(f"{k}_currency"):
                            qs = qs.filter(**{f"{k}_currency": cur})
                    qs = qs.filter(**{f"{col}__{op}": val})

    if mod.extra_filters:
        qs = mod.extra_filters(qs, params)

    ordering = params.get("ordering")
    if ordering:
        desc = ordering.startswith("-")
        key = ordering.lstrip("-")
        if key in mod.field_map and mod.field_map[key].sortable:
            f = mod.field_map[key]
            col = {
                "money": f"{key}_value",
                "dropdown": f"{key}__label",
                "user": f"{key}__full_name",
            }.get(f.type, key)
            if f.type == "money":
                # Amounts only compare within a currency, so group by currency first.
                qs = qs.order_by(f"{key}_currency", f"{'-' if desc else ''}{col}", "pk")
            else:
                qs = qs.order_by(f"{'-' if desc else ''}{col}", "pk")
        elif key in ("created_at", "updated_at"):
            qs = qs.order_by(f"{'-' if desc else ''}{key}", "pk")
    if distinct:
        qs = qs.distinct()
    return qs
