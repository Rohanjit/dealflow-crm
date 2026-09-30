"""What the Vault accepts, and how an upload is checked before it is stored.

Only common deal-document formats are accepted, and a file's first bytes must match its extension,
so an HTML page or a program renamed to .pdf is refused. Files are always downloaded as
attachments except PDFs and images, which may open in the browser in a sandbox."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from django.conf import settings

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # old Office files and Outlook .msg
ZIP = b"PK\x03\x04"  # .docx/.xlsx/.pptx, OpenDocument and .zip


@dataclass(frozen=True)
class Kind:
    label: str
    content_type: str
    magic: tuple[bytes, ...] = ()  # accepted first bytes; empty = plain text
    inline: bool = False  # may be shown in the browser


KINDS: dict[str, Kind] = {
    "pdf": Kind("PDF", "application/pdf", (b"%PDF",), inline=True),
    "doc": Kind("Word", "application/msword", (OLE,)),
    "docx": Kind("Word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (ZIP,)),
    "xls": Kind("Excel", "application/vnd.ms-excel", (OLE,)),
    "xlsx": Kind("Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", (ZIP,)),
    "xlsm": Kind("Excel", "application/vnd.ms-excel.sheet.macroEnabled.12", (ZIP,)),
    "ppt": Kind("PowerPoint", "application/vnd.ms-powerpoint", (OLE,)),
    "pptx": Kind("PowerPoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", (ZIP,)),
    "odt": Kind("Document", "application/vnd.oasis.opendocument.text", (ZIP,)),
    "ods": Kind("Spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", (ZIP,)),
    "odp": Kind("Presentation", "application/vnd.oasis.opendocument.presentation", (ZIP,)),
    "csv": Kind("CSV", "text/csv"),
    "txt": Kind("Text", "text/plain"),
    "rtf": Kind("Rich text", "application/rtf", (b"{\\rtf",)),
    "msg": Kind("Email", "application/vnd.ms-outlook", (OLE,)),
    "eml": Kind("Email", "message/rfc822"),
    "png": Kind("Image", "image/png", (b"\x89PNG\r\n\x1a\n",), inline=True),
    "jpg": Kind("Image", "image/jpeg", (b"\xff\xd8\xff",), inline=True),
    "jpeg": Kind("Image", "image/jpeg", (b"\xff\xd8\xff",), inline=True),
    "gif": Kind("Image", "image/gif", (b"GIF87a", b"GIF89a"), inline=True),
    "webp": Kind("Image", "image/webp", (b"RIFF",), inline=True),
    "zip": Kind("ZIP", "application/zip", (ZIP, b"PK\x05\x06")),
}
ACCEPTED = sorted(KINDS)


class FileRejected(Exception):
    pass


def max_bytes() -> int:
    return int(getattr(settings, "VAULT_MAX_FILE_MB", 50)) * 1024 * 1024


def clean_name(name: str) -> str:
    """A display name without folders or control characters: 'C:\\x\\..\\IM v2.pdf' → 'IM v2.pdf'."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = "".join(ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C")
    name = name.strip().strip(".") or "file"
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 200 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:200]
    return name


def check(upload) -> tuple[str, Kind, str]:
    """Validate an uploaded file. Returns (extension, kind, sha256 hex) or raises FileRejected."""
    name = clean_name(upload.name)
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    kind = KINDS.get(ext)
    if not kind:
        raise FileRejected(f"{name}: .{ext or '?'} files can't go in the Vault. "
                           "Accepted: PDF, Word, Excel, PowerPoint, OpenDocument, CSV, text, email, images and ZIP.")
    if upload.size > max_bytes():
        raise FileRejected(f"{name} is larger than {max_bytes() // (1024 * 1024)} MB.")
    if upload.size == 0:
        raise FileRejected(f"{name} is empty.")
    upload.seek(0)
    head = upload.read(8192)
    if kind.magic:
        if not any(head.startswith(m) for m in kind.magic) or (ext == "webp" and head[8:12] != b"WEBP"):
            raise FileRejected(f"{name} doesn't look like a real .{ext} file.")
    elif b"\x00" in head:
        raise FileRejected(f"{name} doesn't look like a text file.")
    digest = hashlib.sha256(head)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    return ext, kind, digest.hexdigest()
