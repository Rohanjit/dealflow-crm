"""Custom reports (Settings > Export, decision 61): pick a module, the records (the list's own
filters), the columns and a format, and get a file. Excel and CSV write the same rows as the list's
Export button (so they import back); PDF is either a table or one page per record.

Whoever runs a report gets only what they could see in the app: their role's modules, records
(restricted deals stay out), switched-on fields, and the Vault only while it's open.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.exporter import birthday_text, export_fields, export_workbook, sheet_rows
from core.models import ChoiceOption, FirmSettings, Note
from core.modules import BUILT_MODULES
from core.records import filter_queryset, read_value, text_in, visible, with_related
from core.registry import CURRENCIES, enabled_map, get_module
from core.records import subtitle_for

FORMATS = ("xlsx", "csv", "pdf")
LAYOUTS = ("table", "pages")
LIMITS = {"xlsx": 50_000, "csv": 50_000, "table": 2_000, "pages": 200}
CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
}
SYMBOL = dict(CURRENCIES)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
RESERVED = {"page", "page_size", "columns"}


# ---------------------------------------------------------------- the request


def clean_config(data, user) -> dict:
    """Check a report's settings and return them tidied: {module, columns, filters, ordering,
    format, layout, title}. Refuses modules the person can't view."""
    if not isinstance(data, dict):
        raise ValidationError({"detail": "The report settings couldn't be read."})
    module = data.get("module")
    if not isinstance(module, str) or module not in BUILT_MODULES:
        raise ValidationError({"module": "Pick what to export."})
    if not user.can(module, "view"):
        raise PermissionDenied("Your role can't see this module.")
    mod = get_module(module)
    enabled = enabled_map(module)
    known = {f.key for f in mod.fields if enabled.get(f.key, True)}

    columns = data.get("columns")
    columns = [] if columns is None else columns
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns) or len(columns) > 200:
        raise ValidationError({"columns": "The columns couldn't be read."})
    columns = [c for c in dict.fromkeys(columns) if c in known]

    filters = data.get("filters")
    filters = {} if filters is None else filters
    if not isinstance(filters, dict) or len(filters) > 80:
        raise ValidationError({"filters": "The filters couldn't be read."})
    clean_filters = {}
    for k, v in filters.items():
        if not isinstance(k, str) or len(k) > 80 or k in RESERVED:
            continue
        if v is None or v == "":
            continue
        if not isinstance(v, (str, int, float)) or isinstance(v, bool) or len(str(v)) > 4000:
            raise ValidationError({"filters": f"The filter “{k[:40]}” couldn't be read."})
        clean_filters[k] = text_in(str(v))

    ordering = data.get("ordering")
    ordering = "" if ordering is None else ordering
    if not isinstance(ordering, str) or len(ordering) > 200:
        raise ValidationError({"ordering": "The sort order couldn't be read."})
    fmt = data.get("format") or "xlsx"
    if fmt not in FORMATS:
        raise ValidationError({"format": "Pick Excel, CSV or PDF."})
    layout = data.get("layout") or "table"
    if layout not in LAYOUTS:
        raise ValidationError({"layout": "Pick a table or one page per record."})
    title = data.get("title") or ""
    if not isinstance(title, str):
        raise ValidationError({"title": "Enter a title."})
    title = " ".join(text_in(title).split())[:120] or f"{mod.label} report"
    return {"module": module, "columns": columns, "filters": clean_filters, "ordering": ordering,
            "format": fmt, "layout": layout, "title": title}


def records_for(cfg: dict, request):
    """The records a report covers, exactly as the list would show them to this person."""
    mod = get_module(cfg["module"])
    if mod.gate:
        mod.gate(request)  # the Vault's PIN
    enabled = enabled_map(cfg["module"])
    params = QueryDict(mutable=True)
    for k, v in cfg["filters"].items():
        params[k] = v
    if cfg["ordering"]:
        params["ordering"] = cfg["ordering"]
    qs = visible(mod.model.objects.filter(archived_at__isnull=True), mod, request.user)
    qs = with_related(filter_queryset(qs, mod, params, request.user, enabled), mod)
    return mod, enabled, params, qs


def run(cfg: dict, request) -> tuple[bytes, str, str]:
    """Build the file: (bytes, content type, file name)."""
    mod, enabled, params, qs = records_for(cfg, request)
    user = request.user
    kind = cfg["layout"] if cfg["format"] == "pdf" else cfg["format"]
    count = qs.count()
    limit = LIMITS[kind]
    if count > limit:
        what = {"table": "PDF tables", "pages": "One page per record"}.get(kind, "Exports")
        raise ValidationError({"detail": f"This report covers {count:,} records; {what} take up to {limit:,}. "
                                         "Add filters, or choose Excel."})
    columns = cfg["columns"] or None
    if cfg["format"] == "xlsx":
        data = export_workbook(mod, qs, user, enabled, columns)
    elif cfg["format"] == "csv":
        data = to_csv(mod, qs, user, export_fields(mod, enabled, columns))
    else:
        ctx = Context(user)
        meta = {
            "title": cfg["title"],
            "module": mod,
            "count": count,
            "filters": describe_filters(mod, params, ctx),
            "made": f"Made by {user.full_name} on {ctx.datetime_text(timezone.localtime())}",
            "workspace": FirmSettings.load().firm_name or user.full_name,
        }
        if cfg["layout"] == "table":
            fields = export_fields(mod, enabled, columns or [f.key for f in mod.fields if f.list_default])
            data = pdf_table(mod, qs, fields, ctx, meta)
        else:
            fields = [f for f in export_fields(mod, enabled, columns) if f.key != mod.title_field]
            data = pdf_pages(mod, qs, fields, ctx, meta, user)
    stamp = timezone.localdate().isoformat()
    name = re.sub(r"[^A-Za-z0-9]+", "-", cfg["title"]).strip("-").lower()[:60] or cfg["module"]
    return data, CONTENT_TYPES[cfg["format"]], f"{name}-{stamp}.{cfg['format']}"


# ---------------------------------------------------------------- values as text


class Context:
    """Turns stored values into the words and numbers a person sees in the app, in their own date
    and number formats."""

    def __init__(self, user):
        self.user = user
        self.options = dict(ChoiceOption.objects.values_list("pk", "label"))
        self.users = dict(get_user_model().objects.values_list("pk", "full_name"))
        self.date_format = user.date_format
        self.indian = user.number_grouping == "indian"

    def date_text(self, d) -> str:
        if not d:
            return ""
        if isinstance(d, str):
            d = dt.date.fromisoformat(d[:10])
        fmt = {"DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y", "YYYY-MM-DD": "%Y-%m-%d"}.get(self.date_format, "%d/%m/%Y")
        return d.strftime(fmt)

    def datetime_text(self, when) -> str:
        return f"{self.date_text(when.date())} {when:%H:%M}"

    def number(self, value, decimals: int = 2, group: bool = True) -> str:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)
        sign = "-" if d < 0 else ""
        d = abs(d)
        text = format(d.quantize(Decimal(1) / (10 ** decimals)) if decimals else d.to_integral_value(), "f")
        whole, _, frac = text.partition(".")
        frac = frac.rstrip("0")
        if group:
            whole = _group(whole, self.indian)
        return sign + whole + (f".{frac}" if frac else "")

    def money(self, v: dict) -> str:
        if not v or v.get("amount") in (None, ""):
            return ""
        sym = SYMBOL.get(v.get("currency") or "", v.get("currency") or "")
        amount = self.number(v["amount"])
        text = f"{sym} {amount}" if sym and sym[-1].isalpha() else f"{sym}{amount}"
        return f"{text} {v['unit']}" if v.get("unit") else text

    def value(self, obj, f) -> str:
        v = read_value(obj, f)
        t = f.type
        if v is None or v == "" or v == []:
            return "" if t != "boolean" else "No"
        if t == "money":
            return self.money(v)
        if t == "dropdown":
            return ", ".join(self.options.get(pk, "") for pk in v) if f.multi else self.options.get(v, "")
        if t == "user":
            return ", ".join(self.users.get(pk, "") for pk in v) if f.multi else self.users.get(v, "")
        if t == "link":
            return ", ".join(i["title"] for i in v) if f.multi else v["title"]
        if t == "tags":
            return ", ".join(v)
        if t in ("choice", "tristate"):
            return dict(f.choices or []).get(v, str(v))
        if t == "boolean":
            return "Yes" if v else "No"
        if t == "date":
            return self.date_text(v)
        if t == "datetime":
            day, _, at = str(v).partition("T")
            return self.date_text(day) + (f" {at}" if at else "")
        if t == "birthday":
            return birthday_text(v) or ""
        if t == "percent":
            return self.number(v, 4, group=False) + "%"
        if t == "decimal":
            return self.number(v)
        if t == "integer":
            year_like = isinstance(f.max_value, int) and f.max_value < 10_000
            return self.number(v, 0, group=not year_like)
        return str(v)


def _group(whole: str, indian: bool) -> str:
    if len(whole) <= 3:
        return whole
    if not indian:
        return f"{int(whole):,}"
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def describe_filters(mod, params, ctx: Context) -> str:
    """The report's filters in plain words, for the PDF's heading."""
    out = []
    if q := (params.get("q") or "").strip():
        out.append(f"Search “{q}”")
    if mod.segment_param and (seg := params.get(mod.segment_param)):
        label = next((o["label"] for o in mod.segment_options or [] if o["value"] == seg), seg)
        out.append(f"Showing {label.lower()}")
    for qf in mod.quick_filters:
        if params.get(qf["param"]) == qf["value"]:
            out.append(qf["label"])
    for f in mod.fields:
        k = f.key
        if f.type in ("date", "datetime"):
            lo, hi = params.get(f"{k}_from"), params.get(f"{k}_to")
            if lo or hi:
                parts = [f"from {ctx.date_text(lo)}" if lo else "", f"to {ctx.date_text(hi)}" if hi else ""]
                out.append(f"{f.label} {' '.join(p for p in parts if p)}")
            continue
        if f.type == "birthday":
            if raw := params.get(f"{k}_month"):
                months = [MONTHS[int(m) - 1] for m in raw.split(",") if m.isdigit() and 1 <= int(m) <= 12]
                if months:
                    out.append(f"{f.label} in {' or '.join(months)}")
            continue
        raw = params.get(k)
        if not raw:
            continue
        vals = [p for p in raw.split(",") if p]
        if f.type == "dropdown":
            words = [ctx.options.get(int(p), p) for p in vals if p.isdigit()]
        elif f.type in ("choice", "tristate"):
            words = [dict(f.choices or []).get(p, p) for p in vals]
        elif f.type == "user":
            words = [ctx.user.full_name if p == "me" else "nobody" if p == "none"
                     else ctx.users.get(int(p), p) if p.isdigit() else p for p in vals]
        elif f.type == "link":
            words = []
            for p in vals:
                target, _, pk = p.partition(":")
                if target in (f.targets or {}) and pk.isdigit():
                    tmod = get_module(target)
                    obj = tmod.model.objects.filter(pk=int(pk)).first()
                    words.append(tmod.title_of(obj) if obj else p)
        elif f.type == "boolean":
            words = ["yes" if raw == "true" else "no"]
        else:
            words = [f"contains “{raw}”"]
            out.append(f"{f.label} {words[0]}")
            continue
        if words:
            out.append(f"{f.label}: {' or '.join(words)}")
    if order := params.get("ordering"):
        labels = []
        for key in order.split(","):
            desc = key.startswith("-")
            try:
                fl = mod.field(key.lstrip("-"))
            except KeyError:
                continue
            labels.append(f"{fl.label} ({'high to low' if desc else 'low to high'})")
        if labels:
            out.append("sorted by " + ", ".join(labels))
    return "; ".join(out)


# ---------------------------------------------------------------- CSV


def to_csv(mod, qs, user, fields) -> bytes:
    headers, rows = sheet_rows(mod, qs, user, fields)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row, _ in rows:
        out = []
        for v in row:
            if isinstance(v, dt.datetime):
                v = v.strftime("%Y-%m-%d %H:%M")
            elif isinstance(v, dt.date):
                v = v.isoformat()
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            elif isinstance(v, str) and v[:1] in ("=", "+", "-", "@") and not _numberish(v):
                v = "'" + v  # a cell that a spreadsheet would run as a formula
            out.append("" if v is None else v)
        w.writerow(out)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # the BOM lets Excel read ₹ and other symbols


def _numberish(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- PDF

INK = "#37352f"
MUTED = "#787774"
LINE = "#e9e9e7"
TINT = "#f7f7f5"
ACCENT = "#2f80e0"
_fonts_ready = False


def _fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    here = Path(__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("DF", str(here / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DF-Bold", str(here / "DejaVuSans-Bold.ttf")))
    addMapping("DF", 0, 0, "DF")
    addMapping("DF", 1, 0, "DF-Bold")
    addMapping("DF", 0, 1, "DF")
    addMapping("DF", 1, 1, "DF-Bold")
    _fonts_ready = True


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    base = dict(fontName="DF", textColor=colors.HexColor(INK))
    return {
        "title": ParagraphStyle("title", fontSize=17, leading=21, fontName="DF-Bold", textColor=colors.HexColor(INK)),
        "sub": ParagraphStyle("sub", fontSize=8.5, leading=12, fontName="DF", textColor=colors.HexColor(MUTED)),
        "head": ParagraphStyle("head", fontSize=7.5, leading=9.5, fontName="DF-Bold", textColor=colors.HexColor(MUTED)),
        "cell": ParagraphStyle("cell", fontSize=8, leading=10, **base),
        "label": ParagraphStyle("label", fontSize=8.5, leading=11, fontName="DF", textColor=colors.HexColor(MUTED)),
        "value": ParagraphStyle("value", fontSize=8.5, leading=11, **base),
        "section": ParagraphStyle("section", fontSize=9.5, leading=13, fontName="DF-Bold", textColor=colors.HexColor(INK),
                                  spaceBefore=10, spaceAfter=4),
        "rec": ParagraphStyle("rec", fontSize=19, leading=23, fontName="DF-Bold", textColor=colors.HexColor(INK)),
    }


def _p(text: str, style):
    from reportlab.platypus import Paragraph

    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _doc(buf, landscape_page: bool, meta: dict, title: str):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    size = landscape(A4) if landscape_page else A4
    return SimpleDocTemplate(buf, pagesize=size, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                             bottomMargin=16 * mm, title=title, author=meta["workspace"], creator="Dealflow")


def _numbered(meta: dict):
    """A canvas that writes "Page X of Y" and the workspace name at the foot of every page."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                w, _ = self._pagesize
                self.setFont("DF", 7)
                self.setFillColor(colors.HexColor(MUTED))
                self.drawString(14 * mm, 9 * mm, f"{meta['workspace']} · {meta['title']} · Dealflow")
                self.drawRightString(w - 14 * mm, 9 * mm, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    return NumberedCanvas


def _heading(meta: dict, st) -> list:
    from reportlab.platypus import Spacer

    bits = [f"{meta['module'].label} · {meta['count']:,} {'record' if meta['count'] == 1 else 'records'}"]
    if meta["filters"]:
        bits.append(meta["filters"][:1].upper() + meta["filters"][1:])
    bits.append(meta["made"])
    return [_p(meta["title"], st["title"]), Spacer(1, 3), _p(" · ".join(bits), st["sub"]), Spacer(1, 10)]


def pdf_table(mod, qs, fields, ctx: Context, meta: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    _fonts()
    st = _styles()
    rows = [[ctx.value(obj, f) for f in fields] for obj in qs]
    landscape_page = len(fields) > 4
    buf = io.BytesIO()
    doc = _doc(buf, landscape_page, meta, meta["title"])
    width = doc.width
    # Share the width by how much each column usually holds (headers count too), within limits.
    weights = []
    for i, f in enumerate(fields):
        sample = [len(r[i]) for r in rows[:300]] or [0]
        typical = sorted(sample)[int(len(sample) * 0.8)] if sample else 0
        longest_word = max((len(w) for r in rows[:300] for w in r[i].split()), default=0)
        weights.append(min(max(len(f.label) + 2, typical, longest_word + 1, 10), 40))
    total = sum(weights) or 1
    col_widths = [width * w / total for w in weights]
    data = [[_p(f.label, st["head"]) for f in fields]]
    data += [[_p(v, st["cell"]) for v in r] for r in rows]
    if len(data) == 1:
        data.append([_p("No records match this report.", st["cell"])] + [""] * (len(fields) - 1))
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TINT)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#c3c2bf")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if len(data) == 2 and not rows:
        style.append(("SPAN", (0, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    doc.build(_heading(meta, st) + [table], canvasmaker=_numbered(meta))
    return buf.getvalue()


def pdf_pages(mod, qs, fields, ctx: Context, meta: dict, user) -> bytes:
    """One page per record: its name, a facts line, the chosen fields grouped as on the record page
    (empty ones left out), plus a deal's investor tracker and the latest log entries."""
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, PageBreak, Spacer, Table, TableStyle

    _fonts()
    st = _styles()
    buf = io.BytesIO()
    doc = _doc(buf, False, meta, meta["title"])
    width = doc.width
    story = _heading(meta, st)
    objs = list(qs)
    if not objs:
        story.append(_p("No records match this report.", st["value"]))
    tracker_mod = get_module("deal_investors") if mod.key == "deals" else None
    for i, obj in enumerate(objs):
        if i:
            story.append(PageBreak())
        story.append(_p(mod.title_of(obj), st["rec"]))
        facts = [mod.singular]
        if sub := subtitle_for(mod, obj):
            facts.append(sub)
        if mod.pipeline:
            p = mod.pipeline
            stage, status = getattr(obj, p.stage_field), getattr(obj, p.status_field)
            where = p.stage_label(stage) + ("" if status == p.open_value else f" · {p.exit_label(status)}")
            if where not in " · ".join(facts):
                facts.append(where)
        story += [Spacer(1, 2), _p(" · ".join(facts), st["sub"]), Spacer(1, 6)]
        for group in mod.groups:
            # Stage and status are in the line under the name already.
            pairs = [(f.label, ctx.value(obj, f)) for f in fields
                     if f.group == group and f.pipeline_role not in ("stage", "status")]
            pairs = [(k, v) for k, v in pairs if v not in ("", "No")]
            if not pairs:
                continue
            t = Table([[_p(k, st["label"]), _p(v, st["value"])] for k, v in pairs], colWidths=[width * 0.32, width * 0.68])
            t.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(KeepTogether([_p(group, st["section"]), t]))
        from core import access

        if tracker_mod is not None and "investor" not in access.hidden(tracker_mod, user):
            entries = visible(tracker_mod.model.objects.filter(deal=obj, archived_at__isnull=True), tracker_mod, user) \
                .select_related("investor").order_by("investor__firm_name")
            tp = tracker_mod.pipeline
            rows = [[_p(e.investor.firm_name, st["cell"]), _p(tp.stage_label(e.stage), st["cell"]),
                     _p("Active" if e.status == tp.open_value else tp.exit_label(e.status), st["cell"])] for e in entries]
            if rows:
                t = Table([[_p("Investor", st["head"]), _p("Stage", st["head"]), _p("Status", st["head"])]] + rows,
                          colWidths=[width * 0.44, width * 0.34, width * 0.22], repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TINT)),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                story += [_p("Investor tracker", st["section"]), t]
        if mod.timeline:
            notes = Note.objects.filter(module=mod.key, record_id=obj.pk).order_by("-date", "-created_at")[:5]
            kinds = dict(Note.KINDS)
            lines = [[_p(ctx.date_text(n.date), st["label"]),
                      _p(f"{kinds.get(n.kind, n.kind)}: {(n.text or '')[:400]}", st["value"])] for n in notes]
            if lines:
                t = Table(lines, colWidths=[width * 0.18, width * 0.82])
                t.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]))
                story += [_p("Recent activity", st["section"]), t]
    doc.build(story, canvasmaker=_numbered(meta))
    return buf.getvalue()
