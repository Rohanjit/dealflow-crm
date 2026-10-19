"""CSV / Excel import: upload → match columns → check rows and duplicates → import.

The parsed file is kept on disk under DATA_DIR/imports/<token>.json between steps.
Cell text is turned into the same shape the API accepts, then validated with the same
rules as the forms, so an imported record is indistinguishable from a typed one.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import secrets
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from .models import ChoiceList, ChoiceOption
from .records import FieldError, apply_values, clean_values
from .registry import CURRENCIES, FieldDef, ModuleDef, active_targets, get_module, name_key

User = get_user_model()

MAX_ROWS = 20_000
TOKEN_TTL = 24 * 3600
CU_SUFFIX = ":cu"  # mapping target for a column holding a money field's currency + unit ("INR Cr")


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------- reading files


def _cell_to_str(v):
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_rows(filename: str, data: bytes) -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise ImportError_("This Excel file couldn't be opened. Save it as .xlsx and try again.")
        ws = wb.worksheets[0]
        rows = [[_cell_to_str(c) for c in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith(".csv") or name.endswith(".txt"):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        text = text.replace("\x00", "")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        try:
            rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
        except csv.Error:
            raise ImportError_("This file couldn't be read as a spreadsheet. Check it's a .csv, or save it as .xlsx.")
    elif name.endswith(".xls"):
        raise ImportError_("Old .xls files aren't supported. Save the sheet as .xlsx or .csv and try again.")
    else:
        raise ImportError_("Upload a .csv or .xlsx file.")

    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise ImportError_("The file is empty.")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # Drop columns that are empty all the way down (including the header).
    keep = [i for i in range(width) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    if len(rows) - 1 > MAX_ROWS:
        raise ImportError_(f"Files are limited to {MAX_ROWS:,} rows. Split the file and import in parts.")
    return rows


# ---------------------------------------------------------------- storing between steps


def _path(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
        raise ImportError_("This import has expired. Upload the file again.")
    return settings.IMPORT_DIR / f"{token}.json"


def save_upload(module_key: str, user, filename: str, rows) -> str:
    settings.IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in settings.IMPORT_DIR.glob("*.json"):
        if now - old.stat().st_mtime > TOKEN_TTL:
            old.unlink(missing_ok=True)
    token = secrets.token_urlsafe(24)
    _path(token).write_text(
        json.dumps({"module": module_key, "user": user.pk, "filename": filename, "rows": rows})
    )
    return token


def load_upload(token: str, module_key: str, user) -> dict:
    p = _path(token)
    if not p.exists():
        raise ImportError_("This import has expired. Upload the file again.")
    data = json.loads(p.read_text())
    if data["module"] != module_key or data["user"] != user.pk:
        raise ImportError_("This import belongs to someone else.")
    return data


def discard_upload(token: str):
    try:
        _path(token).unlink(missing_ok=True)
    except ImportError_:
        pass


# ---------------------------------------------------------------- column matching


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_CUR_WORDS = {
    "₹": "INR", "rs": "INR", "inr": "INR", "rupees": "INR",
    "$": "USD", "usd": "USD", "us$": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}
for _code, _sym in CURRENCIES:
    _CUR_WORDS.setdefault(_code.lower(), _code)
    _CUR_WORDS.setdefault(_sym.lower(), _code)

_UNIT_WORDS = {
    "k": "K", "thousand": "K", "thousands": "K", "000s": "K",
    "lakh": "Lakh", "lakhs": "Lakh", "lac": "Lakh", "lacs": "Lakh", "l": "Lakh",
    "mn": "Mn", "m": "Mn", "million": "Mn", "millions": "Mn", "mm": "Mn",
    "cr": "Cr", "crore": "Cr", "crores": "Cr", "crs": "Cr",
    "bn": "Bn", "b": "Bn", "billion": "Bn", "billions": "Bn",
}


def money_hint(text: str) -> dict:
    """Currency and unit found in text like 'Min ticket ($ Mn)' or 'INR Cr'."""
    low = (text or "").lower()
    cur = ""
    for sym in ("us$", "₹", "$", "€", "£"):
        if sym in low:
            cur = _CUR_WORDS[sym]
            break
    tokens = re.findall(r"[a-z$€£₹]+", low)
    if not cur:
        for t in tokens:
            if t in _CUR_WORDS and len(t) > 1:
                cur = _CUR_WORDS[t]
                break
    unit = ""
    for t in tokens:
        if t in _UNIT_WORDS and not (t in ("m", "b", "l") and len(tokens) > 3):
            unit = _UNIT_WORDS[t]
    return {"currency": cur, "unit": unit}


def suggest_mapping(mod: ModuleDef, headers: list[str], enabled: dict) -> list[dict]:
    """One entry per column: {field, currency, unit} (field may be None = don't import).

    A field is matched to at most one column, except text fields with part aliases
    ("First name" + "Last name" → Full name), which take every part column when no
    column holds the whole value."""
    candidates, parts = {}, {}
    for f in mod.fields:
        if f.readonly or not enabled.get(f.key, True):
            continue
        for name in [f.label, f.key, *f.aliases]:
            candidates.setdefault(_norm(name), f.key)
        for name in f.part_aliases:
            parts.setdefault(_norm(name), f.key)

    def match(h):
        n = _norm(re.sub(r"\(.*?\)", "", h)) or _norm(h)
        return n, candidates.get(n) or candidates.get(_norm(h)), parts.get(n) or parts.get(_norm(h))

    whole = {key for h in headers if (key := match(h)[1])}
    used = set()
    out = []
    for h in headers:
        n, key, part = match(h)
        if not key and part and part not in whole:
            out.append({"field": part, "currency": "", "unit": ""})
            continue
        if not key:
            # A companion "… currency" column for a money field we already matched.
            m = re.match(r"(.*?)(currency|unit|currencyunit)$", n)
            if m and candidates.get(m.group(1)) and mod.field_map[candidates[m.group(1)]].type == "money":
                cu = candidates[m.group(1)] + CU_SUFFIX
                if cu not in used:
                    used.add(cu)
                    out.append({"field": cu, "currency": "", "unit": ""})
                    continue
        if key and key not in used:
            used.add(key)
            hint = money_hint(h) if mod.field_map[key].type == "money" else {"currency": "", "unit": ""}
            out.append({"field": key, **hint})
        else:
            out.append({"field": None, "currency": "", "unit": ""})
    return out


# ---------------------------------------------------------------- cell → API value


def _split(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,\n|]", raw) if p.strip()]


def parse_money(raw: str, default_currency="", default_unit="") -> dict:
    s = raw.strip()
    if not s:
        return {"amount": None, "currency": "", "unit": ""}
    num = re.search(r"-?\d[\d,]*(\.\d+)?|-?\.\d+", s)
    if not num:
        raise FieldError(f"“{raw}” isn't an amount.")
    amount = num.group(0).replace(",", "")
    rest = (s[: num.start()] + " " + s[num.end():]).strip()
    hint = money_hint(rest)
    return {
        "amount": amount,
        "currency": hint["currency"] or default_currency,
        "unit": hint["unit"] or default_unit,
    }


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(raw: str, user_format: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
        return s[:10]
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        d, mo = (b, a) if user_format == "MM/DD/YYYY" else (a, b)
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            raise FieldError(f"“{raw}” isn't a valid date.")
    m = re.fullmatch(r"(\d{1,2})[\s-]([A-Za-z]{3,9})[\s,-]+(\d{2,4})", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        try:
            return dt.date(y, _MONTHS[m.group(2)[:3].lower()], int(m.group(1))).isoformat()
        except ValueError:
            pass
    m = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        try:
            return dt.date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))).isoformat()
        except ValueError:
            pass
    if re.fullmatch(r"\d{5}(\.\d+)?", s):  # Excel serial number that slipped through as text
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))).isoformat()
    raise FieldError(f"“{raw}” isn't a date we can read.")


def parse_time(raw: str) -> str:
    """'14:30', '2:30 pm', '9 am', '14.30' → 'HH:MM'."""
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(?::\d{2})?\s*([ap]\.?m\.?)?", raw.strip().lower())
    if not m or (m.group(2) is None and not m.group(3)):
        raise FieldError(f"“{raw}” isn't a time we can read.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if not 1 <= hour <= 12:
            raise FieldError(f"“{raw}” isn't a time we can read.")
        hour = hour % 12 + (12 if m.group(3).startswith("p") else 0)
    if hour > 23 or minute > 59:
        raise FieldError(f"“{raw}” isn't a time we can read.")
    return f"{hour:02d}:{minute:02d}"


def parse_datetime(raw: str, user_format: str) -> str:
    """A date with an optional time: '12/10/2026 14:30', '2026-10-12T14:30:00', '12 Oct 2026'."""
    s = raw.strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2})(?::\d{2}(?:\.\d+)?)?", s)
    if m:
        day, at = m.group(1), parse_time(m.group(2))
        return day if at == "00:00" else f"{day}T{at}"  # Excel dates without a time come as midnight
    try:
        return parse_date(s, user_format)
    except FieldError:
        pass
    # "<date> <time>": try splitting off a trailing time.
    m = re.fullmatch(r"(.+?)[\s,]+(\d{1,2}(?:[:.]\d{2})?(?::\d{2})?\s*(?:[ap]\.?m\.?)?)", s, re.IGNORECASE)
    if m:
        return f"{parse_date(m.group(1), user_format)}T{parse_time(m.group(2))}"
    raise FieldError(f"“{raw}” isn't a date we can read.")


def parse_birthday(raw: str, user_format: str) -> str:
    """'12 Mar', 'March 12', '12/03', '12/03/1985', '1985-03-12' → 'MM-DD' or 'YYYY-MM-DD'."""
    s = raw.strip()
    if not s:
        return ""
    year = None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            day, month = (b, a) if user_format == "MM/DD/YYYY" else (a, b)
            if m.group(3):
                year = int(m.group(3))
                year += 1900 if 30 <= year < 100 else (2000 if year < 30 else 0)
        else:
            m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})\.?(?:[\s,-]+(\d{4}))?", s) or None
            if m and m.group(2)[:3].lower() in _MONTHS:
                day, month = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()]
                year = int(m.group(3)) if m.group(3) else None
            else:
                m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", s)
                if m and m.group(1)[:3].lower() in _MONTHS:
                    day, month = int(m.group(2)), _MONTHS[m.group(1)[:3].lower()]
                    year = int(m.group(3)) if m.group(3) else None
                else:
                    raise FieldError(f"“{raw}” isn't a birthday we can read.")
    # Address books use placeholder years (1604, 1900) when the year is unknown.
    if year is not None and year <= 1900:
        year = None
    return f"{year:04d}-{month:02d}-{day:02d}" if year else f"{month:02d}-{day:02d}"


class _Pending:
    """A link to a record that doesn't exist yet but will be created when the import runs."""

    def __repr__(self):
        return "PENDING"


PENDING = _Pending()


class RowContext:
    """Lookups shared by all rows of one import."""

    def __init__(self, mod: ModuleDef, user, add_missing_options: bool, create_links: dict | None = None):
        self.mod = mod
        self.user = user
        self.add_missing = add_missing_options and user.has_flag("manage_settings")
        # {link field key: module to create unmatched names in}, only where the user may create there.
        self.create_links = {
            k: m for k, m in (create_links or {}).items()
            if k in mod.field_map and m in dict(active_targets(mod.field_map[k])) and user.can(m, "create")
        }
        self.links: dict[str, dict[str, dict[str, list[int]]]] = {}
        for f in mod.fields:
            if f.type == "link":
                for mkey, _ in active_targets(f):
                    if mkey not in self.links:
                        self.links[mkey] = self._link_index(mkey)
        self.pending_records: dict[str, set[str]] = {}
        self.pending_keys: dict[str, set[str]] = {}
        self.created_records: dict[str, int] = {}
        self.options: dict[str, dict[str, ChoiceOption]] = {}
        for f in mod.fields:
            if f.type == "dropdown" and f.choice_list not in self.options:
                self.options[f.choice_list] = {
                    o.label.strip().lower(): o
                    for o in ChoiceOption.objects.filter(choice_list__key=f.choice_list).order_by("-is_active")
                }
        self.users = {}
        for u in User.objects.all():
            self.users[u.full_name.strip().lower()] = u
            self.users[u.email.lower()] = u
        self.pending_new: dict[str, set[str]] = {}

    @staticmethod
    def _link_index(mkey: str) -> dict:
        tmod = get_module(mkey)
        exact: dict[str, list[int]] = {}
        norm: dict[str, list[int]] = {}
        titles = tmod.model.objects.filter(archived_at__isnull=True).values_list("pk", tmod.title_lookup)
        for pk, title in titles:
            exact.setdefault((title or "").strip().lower(), []).append(pk)
            if k := name_key(tmod, title):
                norm.setdefault(k, []).append(pk)
        return {"exact": exact, "norm": norm}

    def link(self, f: FieldDef, raw: str, warnings: list, commit: bool, allow_create=True) -> str | None:
        """Find the record a cell names ("Blume Ventures") → "investors:12"."""
        name = " ".join(raw.split())
        if not name:
            return None
        # Exact names first (across investors and companies), then names with generic words ignored.
        for bucket in ("exact", "norm"):
            hits = []
            for mkey, _ in active_targets(f):
                tmod = get_module(mkey)
                key = name.lower() if bucket == "exact" else name_key(tmod, name)
                hits += [(mkey, pk) for pk in self.links[mkey][bucket].get(key) or []]
            if len(hits) == 1:
                return f"{hits[0][0]}:{hits[0][1]}"
            if len(hits) > 1:
                warnings.append(f"{f.label}: “{name}” matches {len(hits)} records, left blank.")
                return None
        create_in = self.create_links.get(f.key) if allow_create else None
        if create_in:
            tmod = get_module(create_in)
            if not commit:
                # Rows naming the same new firm ("Nova Retail", "Nova Retail Pvt Ltd") create it once.
                seen = self.pending_keys.setdefault(create_in, set())
                if (k := name_key(tmod, name)) not in seen:
                    seen.add(k)
                    self.pending_records.setdefault(tmod.label, set()).add(name)
                return PENDING
            obj = apply_values(tmod.model(), tmod, {tmod.title_field: name[:255]}, self.user)
            idx = self.links[create_in]
            idx["exact"].setdefault(name.lower(), []).append(obj.pk)
            if k := name_key(tmod, name):
                idx["norm"].setdefault(k, []).append(obj.pk)
            self.created_records[tmod.label] = self.created_records.get(tmod.label, 0) + 1
            return f"{create_in}:{obj.pk}"
        labels = " or ".join(get_module(m).label.lower() for m, _ in active_targets(f))
        warnings.append(f"{f.label}: “{name}” isn't in {labels}, left blank.")
        return None

    def links(self, f: FieldDef, raw: str, warnings: list, commit: bool) -> list:
        """A cell naming several records ("Blume Ventures; Accel"). Names are split on ; | or new
        lines, and on commas only when the whole cell isn't itself one name."""
        names = [p.strip() for p in re.split(r"[;|\n]", raw) if p.strip()]
        if len(names) == 1 and "," in names[0] and self.link(f, names[0], [], False, allow_create=False) is None:
            names = [p.strip() for p in names[0].split(",") if p.strip()]
        out = []
        for name in names:
            v = self.link(f, name, warnings, commit)
            if v is not None and v is not PENDING and v not in out:
                out.append(v)
        return out

    def option(self, f: FieldDef, label: str, warnings: list, commit: bool):
        key = label.strip().lower()
        opt = self.options[f.choice_list].get(key)
        if opt and opt.is_active:
            return opt.pk
        if opt and not opt.is_active:
            warnings.append(f"{f.label}: “{label}” was removed from the list, left blank.")
            return None
        if self.add_missing:
            if commit:
                cl = ChoiceList.objects.get(key=f.choice_list)
                last = cl.options.order_by("-order").first()
                opt = ChoiceOption.objects.create(
                    choice_list=cl, label=label.strip()[:120], order=(last.order + 1) if last else 0
                )
                self.options[f.choice_list][key] = opt
                return opt.pk
            self.pending_new.setdefault(f.label, set()).add(label.strip())
            return None
        warnings.append(f"{f.label}: “{label}” isn't in the list, left blank.")
        return None


def row_to_data(row: list[str], mapping: list[dict], ctx: RowContext, commit=False):
    """Turn one row of cells into API-shaped data. Returns (data, errors, warnings)."""
    data, errors, warnings = {}, {}, []
    cu_values = {}
    for i, m in enumerate(mapping):
        target = m.get("field")
        if target and target.endswith(CU_SUFFIX) and i < len(row):
            cu_values[target[: -len(CU_SUFFIX)]] = money_hint(row[i])
    # Several columns may feed one text field ("First name" + "Last name"); join them in order.
    joined: dict[str, list[str]] = {}
    for i, m in enumerate(mapping):
        key = m.get("field")
        if key and not key.endswith(CU_SUFFIX) and i < len(row) and ctx.mod.field_map[key].type in ("text", "longtext"):
            joined.setdefault(key, []).append(row[i].strip())
    done = set()
    for i, m in enumerate(mapping):
        key = m.get("field")
        if not key or key.endswith(CU_SUFFIX) or i >= len(row) or key in done:
            continue
        raw = row[i]
        f = ctx.mod.field_map[key]
        if key in joined:
            done.add(key)
            sep = "\n" if f.type == "longtext" else " "
            raw = sep.join(p for p in joined[key] if p)
        try:
            if f.type == "money":
                # currency/unit priority: in the cell → companion column → column default
                cu = cu_values.get(key, {})
                val = parse_money(raw, cu.get("currency") or m.get("currency", ""), cu.get("unit") or m.get("unit", ""))
                if val["amount"] is not None and not val["currency"]:
                    raise FieldError("No currency. Set one for this column in the matching step.")
                data[key] = val
            elif f.type == "dropdown":
                labels = _split(raw) if f.multi else ([raw.strip()] if raw.strip() else [])
                ids = [pk for lbl in labels if (pk := ctx.option(f, lbl, warnings, commit))]
                data[key] = ids if f.multi else (ids[0] if ids else None)
            elif f.type == "user" and f.multi:
                data[key] = []
                for name in _split(raw):
                    if u := ctx.users.get(name.lower()):
                        data[key].append(u.pk)
                    else:
                        warnings.append(f"{f.label}: no user called “{name}”, left out.")
            elif f.type == "user":
                if raw.strip():
                    u = ctx.users.get(raw.strip().lower())
                    if u:
                        data[key] = u.pk
                    else:
                        warnings.append(f"{f.label}: no user called “{raw}”, left blank.")
                        data[key] = None
                else:
                    data[key] = None
            elif f.type == "date":
                data[key] = parse_date(raw, ctx.user.date_format)
            elif f.type == "datetime":
                data[key] = parse_datetime(raw, ctx.user.date_format)
            elif f.type == "birthday":
                data[key] = parse_birthday(raw, ctx.user.date_format)
            elif f.type == "link" and f.multi:
                data[key] = ctx.links(f, raw, warnings, commit)
            elif f.type == "link":
                data[key] = ctx.link(f, raw, warnings, commit)
            elif f.type == "tags":
                data[key] = _split(raw)
            elif f.type in ("integer", "decimal", "percent"):
                data[key] = raw.replace("%", "").replace(",", "").strip()
            else:
                data[key] = raw
        except FieldError as e:
            errors[key] = str(e)
    return data, errors, warnings


# ---------------------------------------------------------------- check and import


def _existing_keys(mod: ModuleDef, user=None) -> dict[str, list[tuple[int, str]]]:
    index: dict[str, list[tuple[int, str]]] = {}
    if not mod.duplicate_keys:
        return index
    own = [f for f in mod.fields if not f.via]
    text_fields = [f.key for f in own if f.type in ("text", "url", "email", "phone", "choice")]
    links = [(f.key, mkey, f"{attr}_id") for f in own if f.type == "link" and not f.multi
             for mkey, attr in active_targets(f)]
    cols = list(dict.fromkeys([*text_fields, *(c for _, _, c in links), mod.title_lookup]))
    from .records import visible

    for rec in visible(mod.model.objects.filter(archived_at__isnull=True), mod, user).values("pk", *cols):
        values = {k: rec[k] for k in text_fields}
        for key, mkey, col in links:
            if rec[col] is not None:
                values[key] = f"{mkey}:{rec[col]}"
        for k in mod.duplicate_keys(values):
            index.setdefault(k, []).append((rec["pk"], rec[mod.title_lookup]))
    return index


def _row_title(mod: ModuleDef, row: list[str], mapping: list[dict], data: dict) -> str:
    """What to call a row on the review screen: the name as written in the file."""
    f = mod.field_map[mod.title_field]
    if f.type != "link":
        return data.get(mod.title_field) or ""
    cells = [row[i] for i, m in enumerate(mapping) if m.get("field") == mod.title_field and i < len(row)]
    return " ".join(c.strip() for c in cells if c.strip())


def _clean_row(mod: ModuleDef, data: dict, errors: dict, enabled: dict):
    """Validate a row like a form would, but leave company-level details alone where the cell is
    blank (a pipeline sheet shouldn't wipe what's already known about a company)."""
    data = {k: v for k, v in data.items() if k not in errors}
    pending = {k for k, v in data.items() if v is PENDING}
    for k in list(data):
        f = mod.field_map.get(k)
        if k in pending or (f and f.via and data[k] in ("", None, [], {"amount": None, "currency": "", "unit": ""})):
            del data[k]
    values, v_errors = clean_values(mod, data, enabled=enabled, importing=True)
    # A record that will be created on import counts as filled in.
    return values, {k: msg for k, msg in v_errors.items() if k not in pending}


def check_rows(mod: ModuleDef, rows, mapping, user, enabled, add_missing_options=False, create_links=None) -> dict:
    ctx = RowContext(mod, user, add_missing_options, create_links)
    existing = _existing_keys(mod, user)
    seen_in_file: dict[str, int] = {}
    results = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        data, errors, warnings = row_to_data(row, mapping, ctx)
        _, v_errors = _clean_row(mod, data, errors, enabled)
        for k, msg in v_errors.items():
            errors.setdefault(k, msg)
        dup_of, dup_row = [], None
        if mod.duplicate_keys:
            for k in mod.duplicate_keys({key: v for key, v in data.items() if isinstance(v, str)}):
                for pk, title in existing.get(k, []):
                    if all(d["id"] != pk for d in dup_of):
                        dup_of.append({"id": pk, "title": title})
                if k in seen_in_file and dup_row is None:
                    dup_row = seen_in_file[k]
                seen_in_file.setdefault(k, n)
        results.append(
            {
                "row": n,
                "title": _row_title(mod, row, mapping, data),
                "errors": {mod.field_map[k].label if k in mod.field_map else k: v for k, v in errors.items()},
                "warnings": warnings,
                "duplicate_of": dup_of,
                "duplicate_of_row": dup_row,
            }
        )
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if not r["errors"] and not r["duplicate_of"] and not r["duplicate_of_row"]),
        "with_errors": sum(1 for r in results if r["errors"]),
        "duplicates": sum(1 for r in results if not r["errors"] and (r["duplicate_of"] or r["duplicate_of_row"])),
        "new_options": {k: sorted(v) for k, v in ctx.pending_new.items()},
        "new_records": {k: sorted(v, key=str.lower) for k, v in ctx.pending_records.items()},
    }
    return {"summary": summary, "rows": results}


def commit_rows(mod: ModuleDef, rows, mapping, user, enabled, include_rows: set[int], add_missing_options=False,
                create_links=None):
    """Create a record for every row number in include_rows that validates."""
    ctx = RowContext(mod, user, add_missing_options, create_links)
    created, failed = 0, []
    for n, row in enumerate(rows, start=2):
        if n not in include_rows:
            continue
        data, errors, _ = row_to_data(row, mapping, ctx, commit=True)
        values, v_errors = _clean_row(mod, data, errors, enabled)
        errors.update(v_errors)
        if errors:
            failed.append({"row": n, "errors": errors})
            continue
        try:
            with transaction.atomic():
                apply_values(mod.model(), mod, values, user, importing=True)
        except (IntegrityError, ValueError) as e:
            failed.append({"row": n, "errors": {"detail": f"Couldn't be saved ({e})."}})
            continue
        created += 1
    return {
        "created": created,
        "failed": failed,
        "skipped": len(rows) - created - len(failed),
        "new_records": ctx.created_records,
    }


def validate_mapping(mod: ModuleDef, mapping, width: int, enabled: dict) -> list[dict]:
    if not isinstance(mapping, list) or len(mapping) != width:
        raise ImportError_("Column matching doesn't fit this file. Upload it again.")
    clean, used = [], set()
    for m in mapping:
        if m is not None and not isinstance(m, dict):
            raise ImportError_("Column matching doesn't fit this file. Upload it again.")
        key = (m or {}).get("field") or None
        cur, unit = (m or {}).get("currency") or "", (m or {}).get("unit") or ""
        if not isinstance(key, (str, type(None))) or not isinstance(cur, str) or not isinstance(unit, str):
            raise ImportError_("Column matching doesn't fit this file. Upload it again.")
        if key:
            base = key[: -len(CU_SUFFIX)] if key.endswith(CU_SUFFIX) else key
            if base not in mod.field_map or mod.field_map[base].readonly or not enabled.get(base, True):
                raise ImportError_(f"Unknown field {key}.")
            if key.endswith(CU_SUFFIX) and mod.field_map[base].type != "money":
                raise ImportError_(f"Unknown field {key}.")
            if key in used and mod.field_map[base].type not in ("text", "longtext"):
                raise ImportError_(f"Two columns are matched to {mod.field_map[base].label}. Pick one.")
            used.add(key)
        clean.append({"field": key, "currency": cur, "unit": unit})
    if mod.title_field not in used:
        raise ImportError_(f"Match a column to {mod.field_map[mod.title_field].label}; it's required.")
    return clean


def importable_fields(mod: ModuleDef, enabled: dict) -> list[dict]:
    out = []
    for f in mod.fields:
        if f.readonly or not enabled.get(f.key, True):
            continue
        out.append({"key": f.key, "label": f.label, "type": f.type, "required": f.required})
        if f.type == "money":
            out.append({"key": f.key + CU_SUFFIX, "label": f"{f.label} – currency & unit", "type": "money_cu",
                        "required": False})
    return out

