"""Generic read / validate / write / filter for any registered module."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, QuerySet, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ChoiceOption, Tag
from .registry import CURRENCY_CODES, UNIT_MULTIPLIER, FieldDef, ModuleDef, active_targets, get_module

User = get_user_model()

EMPTY = (None, "", [])

MAX_ID = 2**63 - 1  # the largest id the database can hold
MAX_INT = 2**31 - 1  # whole-number fields are 32-bit columns on Postgres
MONEY_MAX = Decimal(10) ** 15  # SQLite keeps 15 significant digits; amounts above that come back unreadable
LONGTEXT_MAX = 100_000


def to_id(raw) -> int | None:
    """A record id from user input ("5", 5), or None if it can't be one. Never raises."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        n = int(raw.strip())
    else:
        return None
    return n if 0 < n <= MAX_ID else None


def text_in(v) -> str:
    """Text from user input: stripped, without NUL characters (Postgres can't store them)."""
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        raise FieldError("Enter text.")
    return str(v).replace("\x00", "").strip()


# ---------------------------------------------------------------- reading


def dec_str(d: Decimal | None) -> str | None:
    if d is None:
        return None
    s = format(d.normalize(), "f")
    return s


def holder(obj, f: FieldDef):
    """The record that stores a field's value: itself, or the linked record for company-level fields."""
    if obj is None or not f.via:
        return obj
    return getattr(obj, f.via)


def empty_value(f: FieldDef):
    t = f.type
    if t == "money":
        return {"amount": None, "currency": "", "unit": ""}
    if t == "tags" or f.multi:
        return []
    if t == "boolean":
        return False
    if t == "tristate":
        return "unknown"
    if t in ("text", "longtext", "url", "email", "phone", "choice"):
        return ""
    return None


def read_value(obj, f: FieldDef):
    h = holder(obj, f)
    if h is None:
        return empty_value(f)
    return _read(h, f)


def _read(obj, f: FieldDef):
    if f.custom_key:
        from . import custom

        return custom.read(obj, f)
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
        if f.multi:
            return [u.pk for u in getattr(obj, f.key).all()]
        return getattr(obj, f"{f.key}_id")
    if t == "link":
        return _link_value(obj, f)
    if t == "datetime":
        day = getattr(obj, f.key)
        if not day:
            return None
        at = getattr(obj, f"{f.key}_time")
        return f"{day.isoformat()}T{at.strftime('%H:%M')}" if at else day.isoformat()
    if t == "birthday":
        md = getattr(obj, f.key)
        if not md:
            return None
        year = getattr(obj, f"{f.key}_year")
        return f"{year:04d}-{md}" if year else md
    v = getattr(obj, f.key)
    if t in ("decimal", "percent"):
        return dec_str(v)
    if t == "date":
        if isinstance(v, dt.datetime):  # a timestamp shown as a date (e.g. "Uploaded on"): the firm's day
            v = timezone.localtime(v).date()
        return v.isoformat() if v else None
    return v


def link_value(obj, f: FieldDef):
    """{module, id, title, archived} for whichever target the record links to (a list of them
    for fields that link to several records)."""
    h = holder(obj, f)
    if h is not None and f.custom_key:
        from . import custom

        return custom.read(h, f)
    return _link_value(h, f) if h is not None else empty_value(f)


def hidden_ids(mkey: str) -> set[int]:
    """Ids of records in a module that the person making this request can't see (restricted deals),
    worked out once per request."""
    from . import current

    tmod = get_module(mkey)
    if tmod.hide is None:
        return set()
    req = current._request.get()
    cache = getattr(req, "_df_hidden", None) if req is not None else None
    if cache is None:
        cache = {}
        if req is not None:
            req._df_hidden = cache
    if mkey not in cache:
        cond = tmod.hide(current.user())
        cache[mkey] = set(tmod.model.objects.filter(cond).values_list("pk", flat=True)) if cond is not None else set()
    return cache[mkey]


def _link_item(mkey: str, target) -> dict:
    if target.pk in hidden_ids(mkey):
        # e.g. a task linked to a restricted deal, seen by the person it's assigned to.
        tmod = get_module(mkey)
        return {"module": mkey, "id": target.pk, "title": f"Restricted {tmod.singular.lower()}", "archived": False,
                "restricted": True}
    return {
        "module": mkey,
        "id": target.pk,
        "title": get_module(mkey).title_of(target),
        "archived": target.archived_at is not None,
    }


def _link_value(obj, f: FieldDef):
    if f.multi:
        return [_link_item(mkey, t) for mkey, attr in active_targets(f) for t in getattr(obj, attr).all()]
    for mkey, attr in active_targets(f):
        if getattr(obj, f"{attr}_id") is not None:
            return _link_item(mkey, getattr(obj, attr))
    return None


def _user_ref(u):
    return {"id": u.pk, "name": u.full_name} if u else None


def serialize(obj, mod: ModuleDef) -> dict:
    from . import access

    out = {"id": obj.pk, "_title": mod.title_of(obj)}
    hidden = access.hidden(mod)
    for f in mod.fields:
        if f.key in hidden:
            continue  # this person's role can't see it (decision 63, S3)
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
        if f.via:
            select.append(f.via)
        if f.custom_key:
            continue  # values are in the record's JSON column
        if f.type == "link":
            for mkey, attr in active_targets(f):
                # Records named through a link (a deal is named after its company) bring that along.
                title = get_module(mkey).title_lookup
                path = f.path(attr) + (f"__{title.rsplit('__', 1)[0]}" if "__" in title else "")
                (prefetch if f.multi else select).append(path)
        elif f.is_m2m:
            prefetch.append(f.path())
        elif f.type in ("dropdown", "user"):
            select.append(f.path())
    return qs.select_related(*dict.fromkeys(select)).prefetch_related(*dict.fromkeys(prefetch))


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
    pk = to_id(raw)
    if pk is None:
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
        s = text_in(v)
        if len(s) > f.max_length:
            raise FieldError(f"Keep this under {f.max_length} characters.")
        return s
    if t == "longtext":
        s = text_in(v)
        if len(s) > LONGTEXT_MAX:
            raise FieldError(f"Keep this under {LONGTEXT_MAX:,} characters.")
        return s
    if t == "url":
        s = text_in(v)
        if not s:
            return ""
        if "://" not in s:
            s = "https://" + s
        if len(s) > f.max_length:
            raise FieldError(f"Keep this under {f.max_length} characters.")
        try:
            URLValidator(schemes=["http", "https"])(s)
        except ValidationError:
            raise FieldError("Enter a valid web address.")
        return s
    if t == "email":
        s = text_in(v)
        if s:
            if len(s) > f.max_length:
                raise FieldError(f"Keep this under {f.max_length} characters.")
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
        lo = f.min_value if f.min_value is not None else -MAX_INT
        hi = f.max_value if f.max_value is not None else MAX_INT
        if callable(hi):
            hi = hi()
        if not lo <= d <= hi:
            raise FieldError(f"Enter a number between {lo} and {hi}.")
        return int(d)
    if t in ("decimal", "percent"):
        d = _to_decimal(v)
        if d is not None and t == "percent" and not (0 <= d <= 100):
            raise FieldError("Enter a percentage between 0 and 100.")
        if d is not None and d < 0 and not f.allow_negative:
            raise FieldError("Enter a number that isn't negative.")
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
            current = _current_ids(instance, f)
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
        return _option(f, v, _current_ids(instance, f))
    if t == "user":
        if f.multi:
            if v in EMPTY:
                return []
            current = _current_ids(instance, f)
            seen, out = set(), []
            for raw in v if isinstance(v, (list, tuple)) else [v]:
                user = _user(raw, current)
                if user.pk not in seen:
                    seen.add(user.pk)
                    out.append(user)
            return out
        if v in EMPTY:
            return None
        return _user(v, _current_ids(instance, f))
    if t == "money":
        if v in EMPTY:
            return {"amount": None, "currency": "", "unit": ""}
        if not isinstance(v, dict):
            raise FieldError("Enter an amount, currency and unit.")
        amount = _to_decimal(v.get("amount"))
        currency, unit = v.get("currency") or "", v.get("unit") or ""
        if not isinstance(currency, str) or not isinstance(unit, str):
            raise FieldError("Pick a currency and unit from the list.")
        currency, unit = currency.strip().upper(), unit.strip()
        if amount is None:
            return {"amount": None, "currency": "", "unit": ""}
        if amount < 0 and not f.allow_negative:
            raise FieldError("Amount can't be negative.")
        if abs(amount) >= MONEY_MAX:
            raise FieldError("That amount is too large. Use a bigger unit (Cr, Mn, Bn).")
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
        if not isinstance(v, (list, tuple)):
            raise FieldError("Enter tags as a list.")
        seen, out = set(), []
        for raw in v:
            if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
                raise FieldError("Enter each tag as text.")
            name = text_in(raw)[:60]
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        return out
    if t == "link":
        return _clean_link(f, v, instance)
    if t == "datetime":
        return _clean_datetime(v)
    if t == "birthday":
        return _clean_birthday(v)
    raise FieldError("Unsupported field")


def _current_ids(instance, f: FieldDef) -> set:
    """Dropdown options or people a saved record has now (kept even if since removed)."""
    if not instance or not instance.pk:
        return set()
    if f.custom_key:
        from . import custom

        return custom.current_ids(instance, f)
    if f.multi:
        return set(getattr(instance, f.key).values_list("pk", flat=True))
    return {getattr(instance, f"{f.key}_id")}


def _user(raw, current: set):
    pk = to_id(raw)
    if pk is None:
        raise FieldError("Pick a user.")
    user = User.objects.filter(pk=pk).first()
    if not user:
        raise FieldError("Pick a user.")
    if not user.is_active and user.pk not in current:
        raise FieldError(f"{user.full_name} is deactivated.")
    return user


def _current_links(instance, f: FieldDef) -> set[tuple[str, int]]:
    """(module, id) of the records a saved record links to through `f` today."""
    if not instance or not instance.pk:
        return set()
    if f.custom_key:
        from . import custom

        return custom.current_ids(instance, f)
    out = set()
    for mkey, attr in active_targets(f):
        if f.multi:
            out |= {(mkey, pk) for pk in getattr(instance, attr).values_list("pk", flat=True)}
        elif (pk := getattr(instance, f"{attr}_id")) is not None:
            out.add((mkey, pk))
    return out


def _clean_link(f: FieldDef, v, instance):
    """Accepts {"module", "id"} or "module:id" (a list of them for multi links).
    Returns (module key, record), or a list of those, or None."""
    current = _current_links(instance, f)
    if f.multi:
        if v in EMPTY:
            return []
        if isinstance(v, str):
            v = [p.strip() for p in v.split(",") if p.strip()]
        seen, out = set(), []
        for item in v if isinstance(v, (list, tuple)) else [v]:
            mkey, target = _link_target(f, item, current)
            if (mkey, target.pk) not in seen:
                seen.add((mkey, target.pk))
                out.append((mkey, target))
        return out
    if v in EMPTY:
        return None
    return _link_target(f, v, current)


def _link_target(f: FieldDef, v, current: set):
    if isinstance(v, dict):
        mkey, raw_pk = v.get("module"), v.get("id")
    elif isinstance(v, str) and ":" in v:
        mkey, raw_pk = v.split(":", 1)
    else:
        raise FieldError("Pick a record from the list.")
    targets = dict(active_targets(f))
    if not isinstance(mkey, str) or mkey not in targets:
        raise FieldError("Pick a record from the list.")
    tmod = get_module(mkey)
    pk = to_id(raw_pk)
    if pk is None:
        raise FieldError("Pick a record from the list.")
    target = tmod.model.objects.filter(pk=pk).first()
    if target is not None and pk in hidden_ids(mkey) and (mkey, pk) not in current:
        target = None  # a restricted record this person can't see: as if it didn't exist
    if not target:
        raise FieldError(f"That {tmod.singular.lower()} doesn't exist any more.")
    # A record can keep a link to an archived record, but can't be newly linked to one.
    if target.archived_at is not None and (mkey, pk) not in current:
        raise FieldError(f"{tmod.title_of(target)} is in the Archive. Restore it first.")
    return (mkey, target)


def _clean_datetime(v):
    """'YYYY-MM-DD', optionally with a time of day ('YYYY-MM-DDTHH:MM'). Returns (date, time or None)."""
    if v in EMPTY:
        return None
    if isinstance(v, dt.datetime):
        return (v.date(), v.time().replace(second=0, microsecond=0))
    if isinstance(v, dt.date):
        return (v, None)
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?", str(v).strip())
    if not m:
        raise FieldError("Enter a date as YYYY-MM-DD, with a time as YYYY-MM-DDTHH:MM if needed.")
    try:
        day = dt.date.fromisoformat(m.group(1))
    except ValueError:
        raise FieldError("That date doesn't exist.")
    if m.group(2) is None:
        return (day, None)
    hour, minute = int(m.group(2)), int(m.group(3))
    if hour > 23 or minute > 59:
        raise FieldError("Enter a time between 00:00 and 23:59.")
    return (day, dt.time(hour, minute))


_MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _clean_birthday(v):
    """Accepts "MM-DD", "--MM-DD", "YYYY-MM-DD" or {day, month, year}. Returns {"md", "year"} or None."""
    if v in EMPTY:
        return None
    if isinstance(v, dict):
        day, month, year = v.get("day"), v.get("month"), v.get("year")
    else:
        s = str(v).strip().lstrip("-")
        parts = s.split("-")
        try:
            if len(parts) == 3:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            elif len(parts) == 2:
                year, month, day = None, int(parts[0]), int(parts[1])
            else:
                raise ValueError
        except ValueError:
            raise FieldError("Enter a birthday as MM-DD or YYYY-MM-DD.")
    try:
        day, month = int(day), int(month)
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        raise FieldError("Enter a valid birthday.")
    if not 1 <= month <= 12 or not 1 <= day <= _MONTH_DAYS[month - 1]:
        raise FieldError("That day doesn't exist.")
    if year is not None:
        if not 1900 <= year <= dt.date.today().year:
            raise FieldError("Enter a year between 1900 and this year, or leave it blank.")
        try:
            dt.date(year, month, day)
        except ValueError:
            raise FieldError(f"{year} wasn't a leap year.")
    return {"md": f"{month:02d}-{day:02d}", "year": year}


def _via_record(mod: ModuleDef, f: FieldDef, values: dict, instance):
    """The record a company-level field will be saved to: the one being linked in this same
    request if any, otherwise the one already linked."""
    for lf in mod.fields:
        if lf.type == "link" and not lf.multi and not lf.via and lf.key in values:
            for _, attr in active_targets(lf):
                if attr == f.via:
                    v = values[lf.key]
                    return v[1] if v else None
    return holder(instance, f) if instance is not None and instance.pk else None


def clean_values(mod: ModuleDef, data: dict, *, enabled: dict, instance=None, partial=False, importing=False,
                 check_access=True):
    """Validate incoming API data. Returns (values, errors). Disabled fields are ignored.
    `importing` relaxes rules meant for people working in the app (e.g. reasons for exits).
    Fields the person's role may not change are refused (`check_access=False` only for changes the
    app makes itself, like Last contacted after a confirmed email)."""
    values, errors = {}, {}
    if check_access:
        from . import access

        for key in access.readonly(mod) & set(data):
            errors[key] = "Your role can't change this field."
    # Own fields first, so company-level fields know which company they're being saved to.
    for f in sorted(mod.fields, key=lambda x: bool(x.via)):
        if f.readonly or not enabled.get(f.key, True) or f.key in errors:
            continue
        if f.key not in data:
            if f.required and not partial and not (instance and instance.pk):
                errors[f.key] = "This field is required."
            continue
        owner = _via_record(mod, f, values, instance) if f.via else instance
        try:
            v = clean_one(f, data[f.key], owner)
        except FieldError as e:
            errors[f.key] = str(e)
            continue
        if f.default is not None and _is_empty(v):
            v = f.default  # a blank choice falls back to its default (a task is Medium priority unless set)
        if f.required and _is_empty(v):
            errors[f.key] = "This field is required."
            continue
        values[f.key] = v
    if mod.pipeline:
        from . import pipeline

        for k, msg in pipeline.validate(mod, values, instance, importing).items():
            errors.setdefault(k, msg)
    if mod.validate:
        for k, msg in mod.validate(values, instance, importing).items():
            errors.setdefault(k, msg)
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
def apply_values(obj, mod: ModuleDef, values: dict, user, importing=False, log_text: str | None = None):
    """Save cleaned values. `log_text` replaces the automatic stage entry (e.g. "Created from outreach")."""
    from . import audit_text, current, pipeline

    created = obj.pk is None
    before = audit_text.snapshot(obj, mod, values.keys())
    old = None
    if mod.pipeline:
        p = mod.pipeline
        old = (getattr(obj, p.stage_field), getattr(obj, p.status_field)) if obj.pk else (None, None)
    m2m = {}
    via: dict[str, tuple[str, dict]] = {}
    for key, v in values.items():
        f = mod.field(key)
        if f.via:
            via.setdefault(f.via, (f.via_module, {}))[1][key] = v
            continue
        if f.custom_key:
            from . import custom

            custom.apply(obj, f, v)
            continue
        if f.type == "money":
            setattr(obj, f"{key}_amount", v["amount"])
            setattr(obj, f"{key}_currency", v["currency"])
            setattr(obj, f"{key}_unit", v["unit"])
        elif f.type == "link" and f.multi:
            for mkey, attr in active_targets(f):
                m2m[attr] = [target for m, target in v if m == mkey]
        elif f.type == "link":
            chosen = v[0] if v else None
            for mkey, attr in active_targets(f):
                setattr(obj, attr, v[1] if mkey == chosen else None)
        elif f.type == "datetime":
            setattr(obj, key, v[0] if v else None)
            setattr(obj, f"{key}_time", v[1] if v else None)
        elif f.type == "birthday":
            setattr(obj, key, v["md"] if v else None)
            setattr(obj, f"{key}_year", v["year"] if v else None)
        elif f.is_m2m:
            m2m[key] = tags_from_names(v) if f.type == "tags" else v
        else:
            setattr(obj, key, v)
    # Company-level fields are saved on the linked company (shared by all its outreach).
    for attr, (module_key, vals) in via.items():
        target = getattr(obj, attr)
        if target is not None:
            apply_values(target, get_module(module_key), vals, user)
    event = pipeline.before_save(obj, mod, values, old, importing) if mod.pipeline else None
    if not obj.pk:
        obj.created_by = user
    obj.updated_by = user
    if mod.before_save:
        mod.before_save(obj, values, user)
    obj.save()
    for key, items in m2m.items():
        getattr(obj, key).set(items)
    # For the audit log: what this save changed, old and new values in plain words.
    after = audit_text.snapshot(obj, mod, values.keys())
    changes = audit_text.diff(mod, before, after)
    if created or changes:
        current.add_change({"record": f"{mod.key}:{obj.pk}", "title": mod.title_of(obj),
                            "kind": "added" if created else "edited", "changes": changes})
    if event:
        if log_text:
            event["text"] = log_text
        pipeline.record_event(obj, mod, event, user)
        pipeline.stage_changed.send(sender=mod.key, record=obj, event=event, user=user, importing=importing)
    return obj


# ---------------------------------------------------------------- who can see what


def visible(qs: QuerySet, mod: ModuleDef, user) -> QuerySet:
    """Leave out records this person can't see even though their role can view the module."""
    if mod.hide is not None:
        cond = mod.hide(user)
        if cond is not None:
            qs = qs.exclude(cond)
    return qs


def can_see(obj, mod: ModuleDef, user) -> bool:
    return mod.hide is None or visible(mod.model.objects.filter(pk=obj.pk), mod, user).exists()


# ---------------------------------------------------------------- filtering


def _csv(v: str) -> list[str]:
    return [p.strip() for p in (v or "").split(",") if p.strip()]


def _ints(v: str) -> list[int]:
    return [n for p in _csv(v) if (n := to_id(p)) is not None]


def _search_condition(mod: ModuleDef, q: str, enabled: dict) -> Q:
    cond = Q()
    for f in mod.fields:
        if not f.searchable or not enabled.get(f.key, True):
            continue
        if f.custom_key:
            from . import custom

            cond |= custom.search_q(f, q)
            continue
        if f.type == "link":
            # Contacts are found by their firm's name, for example.
            for mkey, attr in active_targets(f):
                c = Q(**{f"{f.path(attr)}__{get_module(mkey).title_lookup}__icontains": q})
                cond |= _no_repeats(mod, c) if f.multi else c
        else:
            cond |= Q(**{f"{f.path()}__icontains": q})
    return cond


def _no_repeats(mod: ModuleDef, cond: Q) -> Q:
    """A condition on a many-to-many column, as a subquery so each record comes back once."""
    return Q(pk__in=mod.model.objects.filter(cond).values("pk"))


def _link_filter(qs: QuerySet, mod: ModuleDef, f: FieldDef, params) -> QuerySet:
    """?firm=investors:5,investors:9 (specific records) and ?firm_type=investors|none (which side)."""
    targets = {m: f.path(attr) for m, attr in active_targets(f)}
    wrap = (lambda c: _no_repeats(mod, c)) if f.multi else (lambda c: c)
    if raw := params.get(f.key):
        cond = Q()
        for token in _csv(raw):
            mkey, _, pk = token.partition(":")
            if mkey in targets and (n := to_id(pk)) is not None:
                cond |= Q(**{f"{targets[mkey]}__pk": n})
        qs = qs.filter(wrap(cond)) if cond else qs.none()
    typ = params.get(f"{f.key}_type")
    if typ == "none":
        qs = qs.filter(wrap(Q(**{f"{col}__isnull": True for col in targets.values()})))
    elif typ in targets:
        qs = qs.filter(wrap(Q(**{f"{targets[typ]}__isnull": False})))
    return qs


def _is_timestamp(mod: ModuleDef, key: str) -> bool:
    try:
        return mod.model._meta.get_field(key).get_internal_type() == "DateTimeField"
    except Exception:
        return False


def _order_terms(mod: ModuleDef, key: str, desc: bool) -> list:
    """Order-by expressions for one field. Empty values always sort last."""

    def direction(expr):
        return expr.desc(nulls_last=True) if desc else expr.asc(nulls_last=True)

    def blank_last(cond: dict):
        return Case(When(**cond, then=Value(1)), default=Value(0), output_field=IntegerField())

    if key in ("created_at", "updated_at"):
        return [direction(F(key))]
    f = mod.field_map.get(key)
    if not f or not f.sortable:
        return []
    if f.custom_key:
        from . import custom

        return custom.order_terms(f, desc)
    t, col = f.type, f.path()
    if t == "money":
        # Amounts only compare within a currency, so group by currency first.
        return [blank_last({f"{col}_value__isnull": True}), F(f"{col}_currency").asc(), direction(F(f"{col}_value"))]
    if t == "dropdown":
        # The list's own order (e.g. seniority for decision-maker levels), then name.
        return [direction(F(f"{col}__order")), direction(F(f"{col}__label"))]
    if t == "user":
        return [direction(F(f"{col}__full_name"))]
    if t == "link":
        cols = [F(f"{f.path(attr)}__{get_module(m).title_lookup}") for m, attr in active_targets(f)]
        if not cols:
            return []
        return [direction(cols[0] if len(cols) == 1 else Coalesce(*cols))]
    if t == "datetime":
        return [direction(F(col)), direction(F(f"{col}_time"))]
    if t in ("choice", "tristate"):
        # Fixed options sort in their own order (Cold, Warm, Strong; stages in pipeline order).
        position = Case(*[When(**{col: v}, then=Value(i)) for i, (v, _) in enumerate(f.choices or [])],
                        default=Value(None), output_field=IntegerField())
        return [direction(position)]
    if t in ("text", "url", "email", "phone"):
        return [blank_last({col: ""}), direction(F(col))]
    return [direction(F(col))]


def filter_queryset(qs: QuerySet, mod: ModuleDef, params, user, enabled: dict) -> QuerySet:
    from . import access

    hidden = access.hidden(mod, user)
    enabled = {k: (v and k not in hidden) for k, v in enabled.items()}
    if raw_ids := params.get("ids"):
        # Just these records (e.g. exporting a selection).
        qs = qs.filter(pk__in=_ints(raw_ids))
    q = (params.get("q") or "").strip()
    if q:
        cond = _search_condition(mod, q, enabled)
        qs = qs.filter(cond) if cond else qs.none()

    distinct = False
    for f in mod.fields:
        k, t, col = f.key, f.type, f.path()
        if f.custom_key:
            if f.filterable and enabled.get(f.key, True):
                from . import custom

                qs = custom.apply_filter(qs, f, params, user)
            continue
        if t == "link":
            # Applied even when switched off: other screens (e.g. an investor's contacts) rely on it.
            # A role that can't see the link can't filter by it either (nothing matches).
            if f.key in hidden and (params.get(f.key) or params.get(f"{f.key}_type")):
                return qs.none()
            qs = _link_filter(qs, mod, f, params)
            continue
        if not f.filterable or not enabled.get(f.key, True):
            continue
        if t == "dropdown" and (ids := _ints(params.get(k, ""))):
            qs = qs.filter(**{f"{col}__in": ids})
            distinct = distinct or f.multi
        elif t == "user" and (raw := params.get(k)):
            ids = [user.pk if p == "me" else p for p in _csv(raw)]
            none = "none" in ids
            ids = [i for i in ids if i != "none"]
            cond = Q(**{f"{col}__in": _ints(",".join(str(i) for i in ids))}) if ids else Q(pk__in=[])
            if none:
                cond |= Q(**{f"{col}__isnull": True})
            qs = qs.filter(_no_repeats(mod, cond) if f.multi else cond)
        elif t in ("choice", "tristate") and (vals := _csv(params.get(k, ""))):
            qs = qs.filter(**{f"{col}__in": vals})
        elif t == "boolean" and params.get(k) in ("true", "false"):
            qs = qs.filter(**{col: params.get(k) == "true"})
        elif t == "tags" and (names := _csv(params.get(k, ""))):
            cond = Q()
            for n in names:
                cond |= Q(**{f"{col}__name__iexact": n})
            qs = qs.filter(cond)
            distinct = True
        elif t == "birthday" and (months := _ints(params.get(f"{k}_month", ""))):
            cond = Q()
            for m in months:
                if 1 <= m <= 12:
                    cond |= Q(**{f"{col}__startswith": f"{m:02d}-"})
            qs = qs.filter(cond) if cond else qs
        elif t in ("text",) and (raw := params.get(k)):
            qs = qs.filter(**{f"{col}__icontains": raw})
        elif t in ("integer", "decimal", "percent", "money", "date", "datetime"):
            target = f"{col}_value" if t == "money" else col
            if t == "date" and not f.via and _is_timestamp(mod, f.key):
                target = f"{col}__date"
            dated = t in ("date", "datetime")
            lo_key, hi_key = (f"{k}_from", f"{k}_to") if dated else (f"{k}_min", f"{k}_max")
            conv = _to_date if dated else _to_decimal
            for pk, op in ((lo_key, "gte"), (hi_key, "lte")):
                if raw := params.get(pk):
                    try:
                        val = conv(raw)
                    except FieldError:
                        continue
                    if t == "money":
                        val = val * UNIT_MULTIPLIER.get(params.get(f"{k}_unit", ""), Decimal(1))
                        if cur := params.get(f"{k}_currency"):
                            qs = qs.filter(**{f"{col}_currency": cur})
                    qs = qs.filter(**{f"{target}__{op}": val})

    if mod.extra_filters:
        qs = mod.extra_filters(qs, params, user)

    # ?ordering=-primary_contact,full_name — several keys allowed, "-" for descending.
    terms = []
    for raw_key in _csv(params.get("ordering", "")):
        if raw_key.lstrip("-") in hidden:
            continue
        terms += _order_terms(mod, raw_key.lstrip("-"), raw_key.startswith("-"))
    if terms:
        qs = qs.order_by(*terms, "pk")
    if distinct:
        qs = qs.distinct()
    return qs


def subtitle_for(mod: ModuleDef, obj, user=None) -> str:
    """The short line under a record's name in search, lookups and reminders, left out when it would
    show a field this person's role can't see."""
    if not mod.subtitle:
        return ""
    from . import access

    if set(mod.subtitle_fields) & access.hidden(mod, user):
        return ""
    return mod.subtitle(obj) or ""
