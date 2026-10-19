"""Each module's live list of fields and sections: its original layout, or the firm's own layout
from Settings > Fields & layout (decision 57).

Customisable modules (all but Tasks & Reminders and the Vault) can, while "customised" is on:
  · move, rename and hide built-in fields (the primary field, e.g. Firm name, stays put),
  · add sections, and custom fields of every major type (core/custom.py),
  · show custom fields of linked records ("Shown through links"): an investor's custom fields on
    its contacts and tracker entries, a company's on its contacts, outreach and deals (editable
    there, like the built-in company details), an outreach attempt's on its deal, a deal's on its
    tracker entries.
With "customised" off the module shows its original layout; custom fields and their values are
kept, just hidden. Tasks & Reminders and the Vault only have on/off switches for their fields.

`ModuleDef.fields` / `.groups` read from here (core/registry.py). Results are cached per worker
and rebuilt whenever `FirmSettings.layout_token` changes (checked once per request).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import replace

from django.db import connection

CUSTOMISABLE = ("investors", "contacts", "companies", "outreach", "deals", "deal_investors")
SWITCH_ONLY = ("tasks", "vault")

# Linked records whose custom fields a module can show: (FK attribute on this module, module).
# `editable` ones behave like the built-in company details: saved on the company.
LINKS = {
    "contacts": [("investor", "investors", False), ("company", "companies", False)],
    "outreach": [("company", "companies", True)],
    "deals": [("company", "companies", True), ("outreach", "outreach", False)],
    "deal_investors": [("deal", "deals", False), ("investor", "investors", False)],
}

_cache: dict[str, tuple[str, tuple]] = {}
_tables_ready = False


def section_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "section"


def _ready() -> bool:
    """False until the tables exist (while migrating a new install)."""
    global _tables_ready
    if not _tables_ready:
        try:
            names = connection.introspection.table_names()
        except Exception:
            return False
        _tables_ready = "core_modulelayout" in names and "core_customfield" in names
    return _tables_ready


def token() -> str:
    from . import current

    req = current._request.get()
    if req is not None and hasattr(req, "_df_layout_token"):
        return req._df_layout_token
    from .models import FirmSettings

    t = FirmSettings.objects.filter(pk=1).values_list("layout_token", flat=True).first() or ""
    if req is not None:
        req._df_layout_token = t
    return t


def changed():
    """Call after any change to layouts or custom fields."""
    from .models import FirmSettings

    from . import current

    fs = FirmSettings.load()
    fs.layout_token = secrets.token_hex(8)
    fs.save(update_fields=["layout_token"])
    _cache.clear()
    req = current._request.get()
    if req is not None and hasattr(req, "_df_layout_token"):
        del req._df_layout_token


def layouts() -> dict:
    """{module: ModuleLayout} (cached with the field lists)."""
    from .models import ModuleLayout

    t = token()
    hit = _cache.get("__layouts__")
    if hit and hit[0] == t:
        return hit[1]
    data = {lay.module: lay for lay in ModuleLayout.objects.all()}
    _cache["__layouts__"] = (t, data)
    return data


def custom_fields() -> dict:
    """{module: [CustomField, ...]} of active custom fields."""
    from .models import CustomField

    t = token()
    hit = _cache.get("__custom__")
    if hit and hit[0] == t:
        return hit[1]
    data: dict = {}
    for cf in CustomField.objects.filter(active=True).select_related("choice_list"):
        data.setdefault(cf.module, []).append(cf)
    _cache["__custom__"] = (t, data)
    return data


def is_customised(module: str) -> bool:
    lay = layouts().get(module)
    return bool(lay and lay.customised and module in CUSTOMISABLE)


def hidden(module: str) -> set[str]:
    """Built-in fields switched off: in a customised module, or in a switch-only one."""
    lay = layouts().get(module)
    if not lay:
        return set()
    if module in SWITCH_ONLY or (module in CUSTOMISABLE and lay.customised):
        return set(lay.hidden or [])
    return set()


def renamed(module: str) -> dict[str, str]:
    lay = layouts().get(module)
    return dict(lay.labels or {}) if lay and is_customised(module) else {}


def effective(mod):
    """(fields, groups) for a module, as the firm sees it now."""
    base = (mod.__dict__["_base_fields"], mod.__dict__["_base_groups"])
    if mod.key not in CUSTOMISABLE or not _ready():
        return base
    try:
        t = token()
    except Exception:
        return base
    hit = _cache.get(mod.key)
    if hit and hit[0] == t:
        return hit[1]
    result = _build(mod, *base)
    _cache[mod.key] = (t, result)
    return result


def _build(mod, base_fields, base_groups):
    from .custom import field_def

    renames_by_module = {m: renamed(m) for m in CUSTOMISABLE}
    # Built-in fields, with renames (company details shown on outreach and deals follow Companies).
    fields = []
    for f in base_fields:
        new = renames_by_module.get(f.via_module if f.via else mod.key, {}).get(f.key)
        fields.append(replace(f, label=new) if new else f)
    groups = list(base_groups)
    if not is_customised(mod.key):
        return fields, groups

    lay = layouts()[mod.key]
    customs = custom_fields()
    by_key = {f.key: f for f in fields}
    # This module's own custom fields.
    for cf in customs.get(mod.key, []):
        by_key[cf.key] = field_def(cf, group=groups[-1])
    # Custom fields of linked records, where this layout places them.
    linked = {}
    for attr, source, editable in LINKS.get(mod.key, []):
        if not is_customised(source):
            continue
        for cf in customs.get(source, []):
            linked[cf.key] = (cf, attr, source, editable)
    placed = [it.get("field") for it in (lay.items or [])]
    for key in placed:
        if key in linked and key not in by_key:
            cf, attr, source, editable = linked[key]
            by_key[key] = field_def(cf, group=groups[-1], via=attr, via_module=source, readonly=not editable)

    # Sections and order.
    sections = lay.sections or [{"key": section_key(g), "label": g} for g in groups]
    labels = {s["key"]: s["label"] for s in sections}
    order, out = [], []
    for it in lay.items or []:
        f = by_key.get(it.get("field"))
        if f is None or f.key in order:
            continue
        sec = labels.get(it.get("section")) or labels.get(section_key(f.group)) or sections[-1]["label"]
        out.append(replace(f, group=sec))
        order.append(f.key)
    # Fields not placed yet (added in a later version of Dealflow, or new custom fields): their
    # original section if it's still there, else the last one. Linked fields only when placed.
    for f in [*fields, *(by_key[k] for k in by_key if k.startswith("cf_") and k not in linked)]:
        if f.key in order:
            continue
        out.append(replace(f, group=labels.get(section_key(f.group)) or sections[-1]["label"]))
        order.append(f.key)
    # The primary field always leads the first section.
    primary = next((f for f in out if f.key == mod.title_field), None)
    if primary:
        out.remove(primary)
        out.insert(0, replace(primary, group=sections[0]["label"]))
    return out, [s["label"] for s in sections]


def original(mod) -> dict:
    """The module's original layout, for the editor."""
    return {
        "sections": [{"key": section_key(g), "label": g} for g in mod.__dict__["_base_groups"]],
        "items": [{"field": f.key, "section": section_key(f.group)} for f in mod.__dict__["_base_fields"]],
    }
