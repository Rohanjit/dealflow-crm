"""Export any module list (with its current filters) to an Excel file."""

from __future__ import annotations

import io

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import ChoiceOption
from .registry import ModuleDef

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
    date_fmt = EXCEL_DATE.get(user.date_format, "dd/mm/yyyy")
    num_fmt = EXCEL_NUMBER.get(user.number_grouping, "#,##0.##")

    wb = Workbook()
    ws = wb.active
    ws.title = mod.label[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "B2"

    tz = timezone.get_current_timezone()
    for obj in qs:
        row, fmts = [], []
        for f in fields:
            t = f.type
            if t == "money":
                amt = getattr(obj, f"{f.key}_amount")
                cur, unit = getattr(obj, f"{f.key}_currency"), getattr(obj, f"{f.key}_unit")
                row += [float(amt) if amt is not None else None, " ".join(p for p in (cur, unit) if p) if amt is not None else None]
                fmts += [num_fmt, None]
                continue
            if t == "dropdown":
                if f.multi:
                    v = ", ".join(o.label for o in getattr(obj, f.key).all())
                else:
                    pk = getattr(obj, f"{f.key}_id")
                    v = option_labels.get(pk) if pk else None
            elif t == "tags":
                v = ", ".join(tg.name for tg in getattr(obj, f.key).all())
            elif t == "user":
                u = getattr(obj, f.key)
                v = u.full_name if u else None
            elif t in ("choice", "tristate"):
                raw = getattr(obj, f.key)
                v = dict(f.choices or []).get(raw, raw) or None
            elif t == "boolean":
                v = "Yes" if getattr(obj, f.key) else "No"
            elif t in ("decimal", "percent"):
                d = getattr(obj, f.key)
                v = float(d) if d is not None else None
            else:
                v = getattr(obj, f.key)
                if v == "":
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

    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(len(h) + 2, 14), 40)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
