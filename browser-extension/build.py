"""Build the browser add-on: one zip for Chrome and Edge, one for Firefox (same code, the Firefox
manifest adds its add-on id). The zips go to frontend/public/addon/, so every Dealflow install serves
them from Settings > ... "Add people" > Browser add-on.

    python3 browser-extension/build.py
"""

import io
import json
import pathlib
import zipfile

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "frontend" / "public" / "addon"
FIXED = (2026, 10, 22, 0, 0, 0)  # the same input gives the same zip


def icon(size: int) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, size // 5)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(47, 46, 43, 255))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), "D", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), "D", font=font, fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def build(name: str, manifest: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    files = {"manifest.json": json.dumps(manifest, indent=2).encode()}
    for p in sorted((HERE / "src").iterdir()):
        files[p.name] = p.read_bytes()
    for s in (16, 32, 48, 128):
        files[f"icons/{s}.png"] = icon(s)
    with zipfile.ZipFile(OUT / name, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(path, FIXED)
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    print("wrote", OUT / name)


base = json.loads((HERE / "manifest.base.json").read_text())
build("dealflow-capture.zip", base)
build("dealflow-capture-firefox.zip", {**base, "browser_specific_settings": {
    "gecko": {"id": "capture@dealflow.app", "strict_min_version": "115.0"}}})
