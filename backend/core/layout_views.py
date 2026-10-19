"""Settings > Fields & layout (decision 57): read each module's layout, and save changes after the
person re-enters their password. People with "Manage settings" only."""

from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import flag_required

from . import current, layout
from .models import ChoiceList, ChoiceOption, CustomField, ModuleLayout
from .modules import BUILT_MODULES
from .records import text_in
from .registry import get_module

MAX_CUSTOM = 100
MAX_SECTIONS = 30
LINK_TARGETS = ("investors", "contacts", "companies", "outreach", "deals")
TYPES = dict(CustomField.TYPES)
MULTI_TYPES = ("dropdown", "user", "link")


def _modules():
    return [m for m in (*layout.CUSTOMISABLE, *layout.SWITCH_ONLY) if m in BUILT_MODULES]


def _field_row(mod, f, renames, hidden):
    return {
        "key": f.key,
        "label": renames.get(f.key, f.label),
        "original_label": f.label,
        "type": f.type,
        "type_label": TYPES.get(f.type, f.type.title()) if f.type in TYPES else {
            "choice": "Fixed choice", "birthday": "Birthday"}.get(f.type, f.type.title()),
        "section": layout.section_key(f.group),
        "primary": f.key == mod.title_field,
        "can_hide": f.can_disable and f.key != mod.title_field,
        "hidden": f.key in hidden,
        "from_module": get_module(f.via_module).label if f.via else None,
        "built_in": True,
    }


def _custom_row(cf, section="", from_module=None, editable=True):
    return {
        "key": cf.key,
        "label": cf.label,
        "original_label": cf.label,
        "type": cf.type,
        "type_label": TYPES[cf.type],
        "section": section,
        "primary": False,
        "can_hide": True,
        "hidden": not cf.active,
        "built_in": False,
        "multi": cf.multi,
        "targets": cf.targets,
        "required": cf.required,
        "help": cf.help,
        "options": [o.label for o in cf.choice_list.options.filter(is_active=True)] if cf.choice_list_id else [],
        "from_module": get_module(cf.module).label if from_module else None,
        "editable_here": editable,
    }


def layout_payload(module: str) -> dict:
    mod = get_module(module)
    lay = ModuleLayout.objects.filter(module=module).first()
    custom_ok = module in layout.CUSTOMISABLE
    renames = dict(lay.labels or {}) if lay else {}
    hidden = set(lay.hidden or []) if lay else set()
    original = layout.original(mod)
    out = {
        "module": module,
        "label": mod.label,
        "kind": "custom" if custom_ok else "switches",
        "customised": bool(lay and lay.customised),
        "primary": mod.title_field,
        "original": original,
        "saved": {
            "sections": (lay.sections if lay and lay.sections else original["sections"]),
            "items": (lay.items if lay and lay.items else original["items"]),
            "labels": renames,
            "hidden": sorted(hidden),
        },
        "fields": [_field_row(mod, f, renames, hidden) for f in mod.base_fields],
        "custom": [],
        "deleted": [],
        "linked": [],
    }
    if custom_ok:
        for cf in CustomField.objects.filter(module=module).select_related("choice_list"):
            (out["custom"] if cf.active else out["deleted"]).append(_custom_row(cf))
        for attr, source, editable in layout.LINKS.get(module, []):
            if source not in BUILT_MODULES:
                continue
            for cf in CustomField.objects.filter(module=source, active=True).select_related("choice_list"):
                row = _custom_row(cf, from_module=source, editable=editable)
                row["source_customised"] = layout.is_customised(source)
                out["linked"].append(row)
    return out


class LayoutListView(APIView):
    permission_classes = [flag_required("manage_settings")]

    def get(self, request):
        return Response({
            "modules": [layout_payload(m) for m in _modules()],
            "types": [{"value": v, "label": lbl} for v, lbl in CustomField.TYPES],
            "link_targets": [{"value": m, "label": get_module(m).label} for m in LINK_TARGETS if m in BUILT_MODULES],
        })


def _err(msg: str, key="detail"):
    raise ValidationError({key: msg})


class LayoutDetailView(APIView):
    """PUT the whole layout of one module (with the person's password)."""

    permission_classes = [flag_required("manage_settings")]

    def put(self, request, module):
        if module not in _modules():
            _err("This module's fields can't be changed.")
        pw = request.data.get("password")
        if not isinstance(pw, str) or not request.user.check_password(pw):
            _err("That isn't your password.", "password")
        mod = get_module(module)
        lay, _ = ModuleLayout.objects.get_or_create(module=module)
        d = request.data
        with transaction.atomic():
            if module in layout.SWITCH_ONLY:
                lay.hidden = self._hidden(mod, d.get("hidden"))
                lay.updated_by = request.user
                lay.save()
            else:
                customised = d.get("customised")
                if not isinstance(customised, bool):
                    _err("Send whether the module is customised.")
                if customised:
                    self._save_custom(request, mod, lay, d)
                lay.customised = customised
                lay.updated_by = request.user
                lay.save()
                current.note(f"{mod.label}: {'custom layout saved' if customised else 'back to the original layout'}")
            layout.changed()
        return Response(layout_payload(module))

    # ------------------------------------------------------------ pieces

    def _hidden(self, mod, raw):
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            _err("The hidden fields couldn't be read.")
        base = {f.key: f for f in mod.base_fields}
        out = []
        for k in dict.fromkeys(raw):
            f = base.get(k)
            if f is None:
                _err("Unknown field.")
            if not f.can_disable or k == mod.title_field:
                _err(f"{f.label} can't be deleted: other screens need it.")
            out.append(k)
        return out

    def _save_custom(self, request, mod, lay, d):
        module = mod.key
        sections, items, labels = d.get("sections"), d.get("items"), d.get("labels") or {}
        new_fields, deleted, restored = d.get("new_fields") or [], d.get("deleted_fields") or [], d.get("restored_fields") or []
        updates = d.get("field_updates") or {}
        if not isinstance(sections, list) or not isinstance(items, list) or not isinstance(labels, dict) \
                or not isinstance(new_fields, list) or not isinstance(deleted, list) or not isinstance(restored, list) \
                or not isinstance(updates, dict) \
                or not all(isinstance(x, str) for x in [*deleted, *restored]):
            _err("The layout couldn't be read. Reload the page and try again.")

        # Sections
        if not sections or len(sections) > MAX_SECTIONS:
            _err(f"Keep between 1 and {MAX_SECTIONS} sections.")
        clean_sections, seen_keys, seen_labels = [], set(), set()
        for s in sections:
            if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not isinstance(s.get("label"), str):
                _err("A section couldn't be read.")
            label = " ".join(text_in(s["label"]).split())[:60]
            if not label:
                _err("Give every section a name.")
            if s["key"] in seen_keys or label.lower() in seen_labels:
                _err(f"There are two sections called “{label}”.")
            seen_keys.add(s["key"])
            seen_labels.add(label.lower())
            clean_sections.append({"key": s["key"][:60], "label": label})

        hidden = self._hidden(mod, d.get("hidden") or [])

        # Custom fields: existing ones (delete, bring back, edit), then new ones.
        mine = {cf.key: cf for cf in CustomField.objects.filter(module=module).select_related("choice_list")}
        for key in deleted:
            if key in mine:
                mine[key].active = False
        for key in restored:
            if key in mine:
                mine[key].active = True
        for key, ch in updates.items():
            cf = mine.get(key)
            if cf is None or not isinstance(ch, dict):
                _err("A custom field couldn't be found. Reload the page and try again.")
            if "label" in ch:
                cf.label = self._label(ch["label"])
            if "required" in ch:
                cf.required = ch["required"] is True
            if "help" in ch:
                cf.help = text_in(ch["help"])[:200] if isinstance(ch["help"], str) else ""
            if "options" in ch and cf.type == "dropdown":
                self._add_options(cf.choice_list, ch["options"])
        if sum(1 for cf in mine.values() if cf.active) + len(new_fields) > MAX_CUSTOM:
            _err(f"A module can have up to {MAX_CUSTOM} custom fields.")

        # Names: unique within the module, compared with built-in names (as renamed) and custom ones.
        renames = {}
        for k, v in labels.items():
            f = next((x for x in mod.base_fields if x.key == k), None)
            if f is None or k == mod.title_field or not isinstance(v, str):
                continue
            new = self._label(v)
            if new != f.label:
                renames[k] = new
        names = {}

        def claim(name: str, what: str):
            low = name.lower()
            if low in names and names[low] != what:
                _err(f"Two fields would be called “{name}”. Pick another name.", "label")
            names[low] = what

        for f in mod.base_fields:
            claim(renames.get(f.key, f.label), f.key)
        for cf in mine.values():
            if cf.active:
                claim(cf.label, cf.key)
        created = {}
        for i, nf in enumerate(new_fields):
            if not isinstance(nf, dict) or not isinstance(nf.get("id"), str):
                _err("A new field couldn't be read.")
            label = self._label(nf.get("label"))
            claim(label, nf["id"])
            t = nf.get("type")
            if t not in TYPES:
                _err(f"Pick a type for “{label}”.")
            multi = nf.get("multi") is True and t in MULTI_TYPES
            targets = []
            if t == "link":
                raw = nf.get("targets")
                if not isinstance(raw, list) or not raw or not all(x in LINK_TARGETS and x in BUILT_MODULES for x in raw):
                    _err(f"Pick what “{label}” links to.")
                targets = list(dict.fromkeys(raw))
            cf = CustomField.objects.create(
                module=module, label=label, type=t, multi=multi, targets=targets, required=nf.get("required") is True,
                help=text_in(nf.get("help"))[:200] if isinstance(nf.get("help"), str) else "", created_by=request.user,
            )
            if t == "dropdown":
                cl = ChoiceList.objects.create(key=cf.key, label=label, module=module, description=f"Custom field in {mod.label}")
                cf.choice_list = cl
                cf.save(update_fields=["choice_list"])
                self._add_options(cl, nf.get("options") or [])
            created[nf["id"]] = cf.key
        for cf in mine.values():
            cf.save()
            if cf.choice_list_id and cf.choice_list.label != cf.label:
                cf.choice_list.label = cf.label
                cf.choice_list.save(update_fields=["label"])

        # Items: every field once, in a known section. The primary field leads the first section.
        known = {f.key for f in mod.base_fields} | {k for k, cf in mine.items() if cf.active} | set(created.values())
        linked = {cf.key for _, source, _ in layout.LINKS.get(module, []) for cf in CustomField.objects.filter(module=source, active=True)}
        clean_items, seen = [], set()
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("field"), str) or not isinstance(it.get("section"), str):
                _err("A field's position couldn't be read.")
            key = created.get(it["field"], it["field"])
            if key in seen:
                continue
            if key not in known and key not in linked:
                _err("A field in the layout no longer exists. Reload the page and try again.")
            if it["section"] not in seen_keys:
                _err("A field is in a section that doesn't exist.")
            seen.add(key)
            clean_items.append({"field": key, "section": it["section"]})
        clean_items = [it for it in clean_items if it["field"] != mod.title_field]
        clean_items.insert(0, {"field": mod.title_field, "section": clean_sections[0]["key"]})

        lay.sections, lay.items, lay.labels, lay.hidden = clean_sections, clean_items, renames, hidden

    def _label(self, raw) -> str:
        label = " ".join(text_in(raw).split()) if isinstance(raw, str) else ""
        if not label:
            _err("Give every field a name.", "label")
        if len(label) > 80:
            _err("Keep field names under 80 characters.", "label")
        return label

    def _add_options(self, cl, options):
        if not isinstance(options, list) or len(options) > 200:
            _err("Dropdown options couldn't be read (up to 200).")
        have = {o.label.lower() for o in cl.options.all()}
        order = cl.options.count()
        for raw in options:
            label = " ".join(text_in(raw).split())[:120] if isinstance(raw, str) else ""
            if label and label.lower() not in have:
                ChoiceOption.objects.create(choice_list=cl, label=label, order=order)
                have.add(label.lower())
                order += 1
