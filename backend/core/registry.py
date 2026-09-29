"""Field registry.

Each module declares its fields once (in `<app>/fields.py`). Everything generic — the
API serializer, filters, sorting, schema for the frontend, Excel export and import
mapping — is driven from these declarations, so new modules reuse the same machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    # Company-level fields shown on an outreach attempt: the value lives on the record the FK `via`
    # points to (e.g. "company"), which belongs to module `via_module`. See `proxy()`.
    via: str | None = None
    via_module: str | None = None
    # Pipeline modules: which part of the stage machinery this field is (stage, status, entered,
    # lost_reason, hold_reason, revisit). The screens show these through stage controls.
    pipeline_role: str | None = None
    allow_negative: bool = False  # money/number fields that can be below zero (EBITDA, PAT)

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

    def path(self, name: str | None = None) -> str:
        """Database lookup path for this field's column (prefixed with the FK for proxies)."""
        name = name or self.key
        return f"{self.via}__{name}" if self.via else name

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
            "via": self.via,
            "via_module": self.via_module,
            "pipeline_role": self.pipeline_role,
        }


def proxy(src: FieldDef, via: str, via_module: str, **changes) -> FieldDef:
    """A field of another module shown on this one, e.g. a company's sector on an outreach attempt.
    Same key, type and validation; the value is read from and saved to the linked record."""
    return replace(src, via=via, via_module=via_module, required=False, always_on=False, **changes)


@dataclass
class PipelineDef:
    """Fixed stages plus exits (e.g. Lost, On hold) that can happen at any stage. The stage a
    record exited at stays in `stage_field`, so the exit stage is always recorded."""

    stages: list[tuple[str, str]]
    exits: list[tuple[str, str]]  # (status value, label), e.g. ("on_hold", "On hold")
    won: str | None = None  # final stage (e.g. "won" = mandate won)
    stage_field: str = "stage"
    status_field: str = "status"
    entered_field: str = "stage_entered"
    open_value: str = "open"
    reason_fields: dict[str, str] = field(default_factory=dict)  # {status: dropdown field for the reason}
    date_fields: dict[str, str] = field(default_factory=dict)  # {status: date field, e.g. revisit date}

    def stage_label(self, value: str) -> str:
        return dict(self.stages).get(value, value)

    def exit_label(self, value: str) -> str:
        return dict(self.exits).get(value, value)

    def schema(self) -> dict:
        return {
            "stage_field": self.stage_field,
            "status_field": self.status_field,
            "entered_field": self.entered_field,
            "open_value": self.open_value,
            "won": self.won,
            "stages": [{"value": v, "label": lbl} for v, lbl in self.stages],
            "exits": [
                {"value": v, "label": lbl, "reason_field": self.reason_fields.get(v),
                 "date_field": self.date_fields.get(v)}
                for v, lbl in self.exits
            ],
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
    # Or an explicit quick switch: query parameter + options (handled by extra_filters).
    segment_param: str | None = None
    segment_options: list[dict] | None = None
    segment_default: str = ""  # option the list starts on (sent explicitly by the app)
    # When the title isn't a plain column (an outreach attempt is named after its company).
    title_path: str | None = None  # database path, e.g. "company__name"
    title_fn: Callable | None = None  # record -> title
    pipeline: PipelineDef | None = None
    timeline: bool = False  # records carry a dated interaction log (core.Note)
    # Cross-field rules: (values, instance, importing) -> {field: message}
    validate: Callable | None = None

    def title_of(self, obj) -> str:
        return self.title_fn(obj) if self.title_fn else getattr(obj, self.title_field)

    @property
    def title_lookup(self) -> str:
        return self.title_path or self.title_field

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
    """{field_key: enabled} applying the firm's switches. Required fields are always on.
    Company-level fields follow the switches of the module they belong to."""
    from .models import FieldSwitch

    mod = get_module(module_key)
    modules = {module_key} | {f.via_module for f in mod.fields if f.via_module}
    switches = {
        (m, k): on for m, k, on in FieldSwitch.objects.filter(module__in=modules).values_list("module", "field_key", "enabled")
    }
    out = {}
    for f in mod.fields:
        owner = f.via_module or module_key
        out[f.key] = True if not f.can_disable else switches.get((owner, f.key), True)
    return out


def money_value(amount, unit) -> Decimal | None:
    if amount is None:
        return None
    return Decimal(amount) * UNIT_MULTIPLIER.get(unit or "", Decimal(1))
