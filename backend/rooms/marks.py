"""Watermarks and trace tags for data room files.

- View only (PDFs and images): each page is drawn as a picture on the server with the investor's name,
  email, the time and a trace tag written across it, so the file itself never reaches the browser.
- Downloads: PDFs get the same text on every page plus the tag in the file's properties; Excel files
  get it in every sheet's printed header and footer plus a hidden property; Word and PowerPoint files
  get the hidden property. "Trace a file" on the deal reads the tag back to say who it was given to.

PDFs are drawn with PDFium (pypdfium2; no JavaScript or forms are run), stamped with pypdf, pictures
with Pillow. Office files are changed as ZIP/XML text, so charts, pictures and macros stay as they were.
"""

from __future__ import annotations

import io
import math
import re
import secrets
import threading
import zipfile
from pathlib import Path
from html import escape as _html_escape

FONT = Path(__file__).resolve().parent.parent / "reports" / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = Path(__file__).resolve().parent.parent / "reports" / "fonts" / "DejaVuSans-Bold.ttf"
VIEWABLE = {"pdf", "png", "jpg", "jpeg", "gif", "webp"}
DOWNLOADABLE = VIEWABLE | {"xlsx", "xlsm", "docx", "pptx"}
MAX_UNZIPPED = 400 * 1024 * 1024  # Office files are rewritten in memory
PAGE_WIDTH = 1400  # pixels for a page picture
_pdfium_lock = threading.Lock()  # PDFium isn't thread-safe


def escape(text: str) -> str:
    """Text for an XML element (&, < and >)."""
    return _html_escape(text, quote=False)


class MarkError(Exception):
    pass


def new_trace() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "DF-" + "".join(secrets.choice(alphabet) for _ in range(10))


def unsupported_reason(ext: str) -> str | None:
    if ext in DOWNLOADABLE:
        return None
    if ext in ("doc", "xls", "ppt"):
        return "Older Office files can't be watermarked. Save it as .docx, .xlsx, .pptx or PDF first."
    return "Only PDFs, pictures and Word, Excel and PowerPoint files can go in a data room, because they can be watermarked."


# ---------------------------------------------------------------- reading


def page_count(data: bytes, ext: str) -> int:
    if ext == "pdf":
        import pypdfium2 as pdfium

        with _pdfium_lock:
            try:
                pdf = pdfium.PdfDocument(data)
            except pdfium.PdfiumError as e:
                raise MarkError("This PDF couldn't be read (it may be damaged or have a password).") from e
            try:
                return len(pdf)
            finally:
                pdf.close()
    if ext in VIEWABLE:
        return 1
    return 0


def check_office(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise MarkError("This file is too large inside to be watermarked.")
            names = set(z.namelist())
    except zipfile.BadZipFile as e:
        raise MarkError("This file couldn't be read.") from e
    if "[Content_Types].xml" not in names or "_rels/.rels" not in names:
        raise MarkError("This file couldn't be read as an Office document.")


# ---------------------------------------------------------------- pictures


def _font(size: int, bold=False):
    from PIL import ImageFont

    return ImageFont.truetype(str(FONT_BOLD if bold else FONT), size)


def _stamp(img, lines: list[str]):
    """Tile the text diagonally across the picture, faintly, and add a solid footer line."""
    from PIL import Image, ImageDraw

    img = img.convert("RGB")
    w, h = img.size
    size = max(14, w // 48)
    text = "   ·   ".join(lines[:2])
    layer = Image.new("RGBA", (int(math.hypot(w, h)) * 2, int(math.hypot(w, h)) * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(size, bold=True)
    tw = max(1, int(d.textlength(text, font=font)))
    step_y = size * 6
    for row, y in enumerate(range(0, layer.size[1], step_y)):
        offset = (row % 2) * (tw // 2)
        for x in range(-tw + offset, layer.size[0], tw + size * 4):
            d.text((x, y), text, font=font, fill=(40, 52, 80, 34))
    layer = layer.rotate(30, resample=Image.BICUBIC)
    lx, ly = (layer.size[0] - w) // 2, (layer.size[1] - h) // 2
    img.paste(layer.crop((lx, ly, lx + w, ly + h)), (0, 0), layer.crop((lx, ly, lx + w, ly + h)))
    # Footer: plain and readable, so a photo of the screen still says whose copy it is.
    foot = max(12, w // 90)
    bar = foot * 2
    d2 = ImageDraw.Draw(img)
    d2.rectangle([0, h - bar, w, h], fill=(246, 245, 242))
    d2.text((foot, h - bar + foot // 2), "  ·  ".join(lines), font=_font(foot), fill=(70, 70, 70))
    return img


def _jpeg(img) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out.getvalue()


def view_page(data: bytes, ext: str, page: int, lines: list[str]) -> bytes:
    """Page `page` (1-based) as a watermarked JPEG."""
    from PIL import Image, ImageOps

    if ext == "pdf":
        import pypdfium2 as pdfium

        with _pdfium_lock:
            pdf = pdfium.PdfDocument(data)
            try:
                if not 1 <= page <= len(pdf):
                    raise MarkError("No such page.")
                p = pdf[page - 1]
                width = p.get_width() or 595
                img = p.render(scale=min(4.0, PAGE_WIDTH / width)).to_pil()
                p.close()
            finally:
                pdf.close()
    else:
        if page != 1:
            raise MarkError("No such page.")
        Image.MAX_IMAGE_PIXELS = 80_000_000
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((2000, 2000))
    return _jpeg(_stamp(img, lines))


# ---------------------------------------------------------------- downloads


def _pdf_overlay(width: float, height: float, lines: list[str]) -> bytes:
    from reportlab.lib.colors import Color
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(FONT)))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(FONT_BOLD)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    text = "   ·   ".join(lines[:2])
    size = max(9, width / 48)
    c.saveState()
    c.setFillColor(Color(0.16, 0.2, 0.31, alpha=0.13))
    c.setFont("DejaVuSans-Bold", size)
    c.translate(width / 2, height / 2)
    c.rotate(30)
    tw = c.stringWidth(text, "DejaVuSans-Bold", size)
    span = math.hypot(width, height)
    y = -span
    row = 0
    while y < span:
        x = -span + (row % 2) * tw / 2
        while x < span:
            c.drawString(x, y, text)
            x += tw + size * 4
        y += size * 6
        row += 1
    c.restoreState()
    c.setFillColor(Color(0.3, 0.3, 0.3, alpha=0.9))
    c.setFont("DejaVuSans", 7)
    c.drawString(18, 10, "  ·  ".join(lines))
    c.save()
    return buf.getvalue()


def pdf_download(data: bytes, lines: list[str], trace: str) -> bytes:
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise MarkError("PDFs with a password can't be watermarked.")
        writer = PdfWriter(clone_from=reader)
    except MarkError:
        raise
    except Exception as e:
        raise MarkError("This PDF couldn't be watermarked.") from e
    overlays: dict[tuple, object] = {}
    for page in writer.pages:
        box = page.mediabox
        key = (round(float(box.width), 1), round(float(box.height), 1))
        if key not in overlays:
            overlays[key] = PdfReader(io.BytesIO(_pdf_overlay(key[0], key[1], lines))).pages[0]
        page.merge_page(overlays[key], over=True)
    meta = dict(reader.metadata or {})
    meta.update({"/DealflowTrace": trace, "/Keywords": f"{(meta.get('/Keywords') or '')} {trace}".strip()})
    writer.add_metadata({k: str(v) for k, v in meta.items() if isinstance(k, str)})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def image_download(data: bytes, lines: list[str]) -> bytes:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 80_000_000
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    return _jpeg(_stamp(img, lines))


CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CUSTOM_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
CUSTOM_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
# Elements that come after headerFooter in a worksheet (ECMA-376 CT_Worksheet order).
AFTER_HF = ("rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags", "drawing",
            "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects", "controls", "webPublishItems",
            "tableParts", "extLst")


def _custom_xml(existing: str | None, trace: str) -> str:
    prop = f'<property fmtid="{FMTID}" pid="PID" name="DealflowTrace"><vt:lpwstr>{escape(trace)}</vt:lpwstr></property>'
    if existing and "</Properties>" in existing:
        existing = re.sub(r'<property[^>]*name="DealflowTrace"[^>]*>.*?</property>', "", existing, flags=re.S)
        pids = [int(p) for p in re.findall(r'pid="(\d+)"', existing)] or [1]
        return existing.replace("</Properties>", prop.replace("PID", str(max(pids) + 1)) + "</Properties>", 1)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Properties xmlns="{CUSTOM_NS}" xmlns:vt="{VT_NS}">{prop.replace("PID", "2")}</Properties>')


def _excel_hf(text: str) -> str:
    return escape(text.replace("&", "&&"))


def _sheet_with_header(xml: str, lines: list[str]) -> str:
    if not re.search(r"<worksheet[\s>]", xml):
        return xml  # prefixed or unusual XML: the hidden tag still goes in
    xml = re.sub(r"<headerFooter\b[^>]*/>", "", xml)
    xml = re.sub(r"<headerFooter\b.*?</headerFooter>", "", xml, flags=re.S)
    hf = (f'<headerFooter><oddHeader>&amp;L&amp;8{_excel_hf(lines[0])}&amp;R&amp;8Confidential</oddHeader>'
          f'<oddFooter>&amp;L&amp;8{_excel_hf("  ·  ".join(lines[1:]))}&amp;R&amp;8Page &amp;P of &amp;N</oddFooter>'
          '</headerFooter>')
    m = re.search(r"<(%s)[\s/>]" % "|".join(AFTER_HF), xml)
    if m:
        return xml[: m.start()] + hf + xml[m.start():]
    return xml.replace("</worksheet>", hf + "</worksheet>", 1)


def office_download(data: bytes, ext: str, lines: list[str], trace: str) -> bytes:
    check_office(data)
    src = zipfile.ZipFile(io.BytesIO(data))
    names = src.namelist()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            name = info.filename
            if name == "docProps/custom.xml":
                raw = _custom_xml(raw.decode("utf-8", "replace"), trace).encode()
            elif name == "[Content_Types].xml":
                ct = raw.decode("utf-8")
                if "/docProps/custom.xml" not in ct:
                    ct = ct.replace("</Types>", f'<Override PartName="/docProps/custom.xml" ContentType="{CUSTOM_CT}"/></Types>', 1)
                raw = ct.encode()
            elif name == "_rels/.rels":
                rels = raw.decode("utf-8")
                if CUSTOM_REL not in rels:
                    ids = set(re.findall(r'Id="([^"]+)"', rels))
                    n = 1
                    while f"rIdDf{n}" in ids:
                        n += 1
                    rels = rels.replace("</Relationships>", f'<Relationship Id="rIdDf{n}" Type="{CUSTOM_REL}" '
                                                            'Target="docProps/custom.xml"/></Relationships>', 1)
                raw = rels.encode()
            elif ext in ("xlsx", "xlsm") and re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                raw = _sheet_with_header(raw.decode("utf-8"), lines).encode()
            dst.writestr(info, raw)
        if "docProps/custom.xml" not in names:
            dst.writestr("docProps/custom.xml", _custom_xml(None, trace))
    return out.getvalue()


def download(data: bytes, ext: str, lines: list[str], trace: str) -> bytes:
    if ext == "pdf":
        return pdf_download(data, lines, trace)
    if ext in ("xlsx", "xlsm", "docx", "pptx"):
        return office_download(data, ext, lines, trace)
    if ext in VIEWABLE:
        return image_download(data, lines)
    raise MarkError(unsupported_reason(ext) or "This file can't be downloaded.")


# ---------------------------------------------------------------- tracing a file that got out


TRACE_RE = re.compile(r"DF-[A-HJ-NP-Z2-9]{10}")


def find_trace(data: bytes) -> list[str]:
    """Trace tags in a file: in PDF properties, Office custom properties, or anywhere in the bytes."""
    found: list[str] = []
    if data[:4] == b"%PDF":
        try:
            from pypdf import PdfReader

            meta = PdfReader(io.BytesIO(data)).metadata or {}
            found += TRACE_RE.findall(str(meta.get("/DealflowTrace", "")) + " " + str(meta.get("/Keywords", "")))
        except Exception:  # nosec B110 - a damaged file still gets the plain byte search below
            found += []
    elif data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "docProps/custom.xml" in z.namelist() and z.getinfo("docProps/custom.xml").file_size < 1_000_000:
                    found += TRACE_RE.findall(z.read("docProps/custom.xml").decode("utf-8", "replace"))
        except zipfile.BadZipFile:
            found += []
    found += TRACE_RE.findall(data[:20_000_000].decode("latin-1"))
    return list(dict.fromkeys(found))
