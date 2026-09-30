"""Export any module list (with its current filters) to an Excel file."""

from __future__ import annotations

import datetime as dt
import io

from django.contrib.auth import get_user_model
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import ChoiceOption
from .records import read_value
from .registry import ModuleDef

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def birthday_text(value: str | None) -> str | None:
    """'1985-03-12' → '12 Mar 1985', '03-12' → '12 Mar' (unambiguous in any date format)."""
    if not value:
        return None
    parts = value.split("-")
    year = parts[0] if len(parts) == 3 else None
    month, day = int(parts[-2]), int(parts[-1])
    return f"{day} {MONTHS[month - 1]}" + (f" {year}" if year else "")

def _datetime_cell(value: str | None):
    """'2026-10-12T14:30' → a date and time Excel understands; a date alone stays a date."""
    if not value:
        return None
    day, _, at = value.partition("T")
    d = dt.date.fromisoformat(day)
    return dt.datetime.combine(d, dt.time.fromisoformat(at)) if at else d


# Text starting with one of these could run as a formula (or a DDE command) when the sheet is opened.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def neutralise(cell):
    """Keep risky text as plain text: typed as a string and marked with Excel's quote prefix, so it
    shows exactly as stored (a '+91' phone number stays readable) and never runs, even if someone
    later edits the cell."""
    if isinstance(cell.value, str) and cell.value.startswith(FORMULA_STARTS):
        cell.data_type = "s"
        cell.quotePrefix = True


EXCEL_DATE = {"DD/MM/YYYY": "dd/mm/yyyy", "MM/DD/YYYY": "mm/dd/yyyy", "YYYY-MM-DD": "yyyy-mm-dd"}
# Excel shows grouping by the viewer's locale; the Indian format needs an explicit pattern.
EXCEL_NUMBER = {
    "indian": "[>=10000000]##\\,##\\,##\\,##0.##;[>=100000]##\\,##\\,##0.##;##,##0.##",
    "international": "#,##0.##",
}


def export_workbook(mod: ModuleDef, qs, user, enabled: dict, columns: list[str] | None = None) -> bytes:
    fields = [f for f in mod.fields if enabled.get(f.key, True)]
    if columns:
        order = {k: i for i, k in enumerate(columns)}
        fields = sorted([f for f in fields if f.key in order], key=lambda f: order[f.key])
        # Always lead with the record's name so the sheet is readable.
        if mod.title_field not in order:
            fields.insert(0, mod.field(mod.title_field))

    headers = []
    for f in fields:
        if f.type == "money":
            headers += [f.label, f"{f.label} – currency & unit"]
        else:
            headers.append(f.label)
    headers += ["Created by", "Created on", "Last edited by", "Last edited on"]

    option_labels = dict(ChoiceOption.objects.values_list("pk", "label"))
    user_names = dict(get_user_model().objects.values_list("pk", "full_name"))
    date_fmt = EXCEL_DATE.get(user.date_format, "dd/mm/yyyy")
    num_fmt = EXCEL_NUMBER.get(user.number_grouping, "#,##0.##")

    wb = Workbook()
    ws = wb.active
    ws.title = mod.label[:31]
    ws.append(headers)
    for c in ws[1]:
        neutralise(c)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "B2"

    tz = timezone.get_current_timezone()
    for obj in qs:
        row, fmts = [], []
        for f in fields:
            t = f.type
            v = read_value(obj, f)  # the API shape; also covers company-level fields
            if t == "money":
                amt = v["amount"]
                row += [float(amt) if amt is not None else None,
                        " ".join(p for p in (v["currency"], v["unit"]) if p) if amt is not None else None]
                fmts += [num_fmt, None]
                continue
            if t == "dropdown":
                v = ", ".join(option_labels.get(pk, "") for pk in v) if f.multi else option_labels.get(v)
            elif t == "tags":
                v = ", ".join(v)
            elif t == "user":
                v = (", ".join(user_names.get(pk, "") for pk in v) or None) if f.multi else user_names.get(v)
            elif t == "link":
                v = (", ".join(i["title"] for i in v) or None) if f.multi else (v["title"] if v else None)
            elif t == "datetime":
                row.append(_datetime_cell(v))
                fmts.append(date_fmt + (" hh:mm" if v and "T" in v else ""))
                continue
            elif t == "birthday":
                v = birthday_text(v)
            elif t in ("choice", "tristate"):
                v = dict(f.choices or []).get(v, v) or None
            elif t == "boolean":
                v = "Yes" if v else "No"
            elif t in ("decimal", "percent"):
                v = float(v) if v is not None else None
            elif t == "date":
                v = dt.date.fromisoformat(v) if v else None
            elif v == "":
                v = None
            row.append(v)
            fmts.append(date_fmt if t == "date" else (num_fmt if t in ("decimal",) else None))
        for u, when in ((obj.created_by, obj.created_at), (obj.updated_by, obj.updated_at)):
            row += [u.full_name if u else None, when.astimezone(tz).replace(tzinfo=None) if when else None]
            fmts += [None, date_fmt + " hh:mm"]
        ws.append(row)
        r = ws.max_row
        for i, fmt in enumerate(fmts, start=1):
            if fmt:
                ws.cell(row=r, column=i).number_format = fmt
        for c in ws[r]:
            neutralise(c)

    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(len(h) + 2, 14), 40)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
