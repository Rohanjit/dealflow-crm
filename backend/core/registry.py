"""Field registry.

Each module declares its fields once (in `<app>/fields.py`). Everything generic — the
API serializer, filters, sorting, schema for the frontend, Excel export and import
mapping — is driven from these declarations, so new modules reuse the same machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

FIELD_TYPES = {
    "text",
    "longtext",
    "url",
    "email",
    "phone",
    "integer",
    "decimal",
    "percent",
    "date",
    "boolean",
    "tristate",
    "choice",
    "dropdown",
    "user",
    "money",
    "tags",
    "link",  # a link to one record in another module (possibly one of several modules)
    "birthday",  # day + month, year optional
}

TRISTATE_CHOICES = [("yes", "Yes"), ("no", "No"), ("unknown", "Unknown")]

# Units scale an amount within one currency. There is no conversion between currencies.
UNITS = [
    ("", "—"),
    ("K", "Thousand"),
    ("Lakh", "Lakh"),
    ("Mn", "Million"),
    ("Cr", "Crore"),
    ("Bn", "Billion"),
]
UNIT_MULTIPLIER = {
    "": Decimal(1),
    "K": Decimal(1_000),
    "Lakh": Decimal(100_000),
    "Mn": Decimal(1_000_000),
    "Cr": Decimal(10_000_000),
    "Bn": Decimal(1_000_000_000),
}
CURRENCIES = [
    ("INR", "₹"),
    ("USD", "$"),
    ("EUR", "€"),
    ("GBP", "£"),
    ("SGD", "S$"),
    ("AED", "AED"),
    ("JPY", "¥"),
    ("HKD", "HK$"),
    ("AUD", "A$"),
    ("CAD", "C$"),
    ("CHF", "CHF"),
    ("CNY", "CN¥"),
]
CURRENCY_CODES = [c for c, _ in CURRENCIES]


@dataclass
class FieldDef:
    key: str
    label: str
    type: str
    group: str
    required: bool = False
    choice_list: str | None = None  # dropdown: key of the editable ChoiceList
    multi: bool = False  # dropdown: many options allowed
    choices: list[tuple[str, str]] | None = None  # choice: fixed options
    help: str = ""
    list_default: bool = False  # shown as a list column by default
    filterable: bool = False
    searchable: bool = False  # included in the module's text search
    sortable: bool = True
    max_length: int = 255
    aliases: list[str] = field(default_factory=list)  # extra column headers recognised on import
    # Import: headers that hold part of this field ("First name", "Last name"); joined in column order.
    part_aliases: list[str] = field(default_factory=list)
    # link: {target module key: FK attribute on this model}. Only built modules are used.
    targets: dict[str, str] | None = None
    target_labels: dict[str, str] = field(default_factory=dict)  # e.g. {"investors": "Investor side"}
    always_on: bool = False  # can't be switched off in Settings (other screens depend on it)

    def __post_init__(self):
        assert self.type in FIELD_TYPES, f"Unknown field type {self.type}"
        if self.type == "dropdown":
            assert self.choice_list, f"{self.key}: dropdown needs choice_list"
        if self.type == "link":
            assert self.targets, f"{self.key}: link needs targets"
        if self.type == "tristate":
            self.choices = TRISTATE_CHOICES
        if self.type in ("tags",) or (self.type == "dropdown" and self.multi) or self.type == "longtext":
            self.sortable = False

    @property
    def can_disable(self) -> bool:
        return not (self.required or self.always_on)

    @property
    def is_m2m(self) -> bool:
        return self.type == "tags" or (self.type == "dropdown" and self.multi)

    def schema(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "group": self.group,
            "required": self.required,
            "choice_list": self.choice_list,
            "multi": self.multi,
            "choices": [{"value": v, "label": lbl} for v, lbl in (self.choices or [])],
            "help": self.help,
            "list_default": self.list_default,
            "filterable": self.filterable,
            "sortable": self.sortable,
            "can_disable": self.can_disable,
            "targets": [m for m, _ in active_targets(self)] if self.type == "link" else [],
            "target_labels": self.target_labels,
        }


@dataclass
class ModuleDef:
    key: str
    label: str
    singular: str
    model: type
    fields: list[FieldDef]
    groups: list[str]
    title_field: str
    subtitle: Callable | None = None  # record -> short text for search results
    # Import duplicate detection: record -> list of normalised keys; rows sharing a key warn.
    duplicate_keys: Callable | None = None
    extra_filters: Callable | None = None  # (queryset, query_params) -> queryset
    # Extra sections on the detail page. Either related records from another module
    # {key, label, module, link_field, columns, order} or a placeholder {key, label, available_with}.
    sections: list[dict] = field(default_factory=list)
    # Normalises a record title for matching names on import (e.g. linking contacts to firms).
    name_key: Callable | None = None
    # A link field whose target type gets a quick switch above the list (e.g. Investor side / Company side).
    segment_field: str | None = None

    def field(self, key: str) -> FieldDef:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)

    @property
    def field_map(self) -> dict[str, FieldDef]:
        return {f.key: f for f in self.fields}


_REGISTRY: dict[str, ModuleDef] = {}


def register(module: ModuleDef) -> ModuleDef:
    keys = [f.key for f in module.fields]
    assert len(keys) == len(set(keys)), f"Duplicate field keys in {module.key}"
    for f in module.fields:
        assert f.group in module.groups, f"{module.key}.{f.key}: unknown group {f.group}"
    _REGISTRY[module.key] = module
    return module


def get_module(key: str) -> ModuleDef:
    return _REGISTRY[key]


def all_modules() -> list[ModuleDef]:
    return list(_REGISTRY.values())


def is_built(module_key: str) -> bool:
    from .modules import BUILT_MODULES

    return module_key in BUILT_MODULES and module_key in _REGISTRY


def active_targets(f: FieldDef) -> list[tuple[str, str]]:
    """(module key, FK attribute) pairs for a link field, limited to modules that exist in this build."""
    return [(m, attr) for m, attr in (f.targets or {}).items() if is_built(m)]


def default_name_key(title: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def name_key(module: ModuleDef, title: str) -> str:
    return (module.name_key or default_name_key)(title)


def enabled_map(module_key: str) -> dict[str, bool]:
    """{field_key: enabled} applying the firm's switches. Required fields are always on."""
    from .models import FieldSwitch

    mod = get_module(module_key)
    switches = dict(FieldSwitch.objects.filter(module=module_key).values_list("field_key", "enabled"))
    return {f.key: (True if not f.can_disable else switches.get(f.key, True)) for f in mod.fields}


def money_value(amount, unit) -> Decimal | None:
    if amount is None:
        return None
    return Decimal(amount) * UNIT_MULTIPLIER.get(unit or "", Decimal(1))
