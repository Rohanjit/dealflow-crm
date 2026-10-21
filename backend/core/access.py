"""Field access per role (decision 63, S3): a role can be kept from seeing a field, or from
changing it. One place answers "may this person see / change this field?", and every path that
reads or writes records asks it: record payloads, the schema, search, filters, sorting, exports,
reports, imports, bulk edits, merges, lookups and search subtitles, the dashboard, insights,
matching, emails and campaign merge fields.

Rules belong to the module that owns the value: company details shown on outreach attempts and
deals follow the Companies rules, like their on/off switches. The primary field and the pipeline's
own fields (stage, status, exit reasons and dates) can't be restricted, and admins see everything."""

from __future__ import annotations

from . import current

LEVELS = ("read", "hidden")


def _rules(user) -> dict[str, dict[str, str]]:
    """{module: {field: level}} for this person, read once per request."""
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    req = current._request.get()
    cache = req.__dict__.setdefault("_df_field_rules", {}) if req is not None else {}
    if user.pk in cache:
        return cache[user.pk]
    out: dict[str, dict[str, str]] = {}
    role = getattr(user, "role", None)
    if role is not None and not role.is_admin:
        from accounts.models import RoleFieldAccess

        for r in RoleFieldAccess.objects.filter(role_id=role.pk):
            out.setdefault(r.module, {})[r.field] = r.level
    cache[user.pk] = out
    return out


def restrictable(mod, f) -> bool:
    return f.key != mod.title_field and not f.pipeline_role


def level(mod, f, user=None) -> str:
    """"full", "read" or "hidden" for one field of a module."""
    user = user if user is not None else current.user()
    if user is None or not restrictable(mod, f):
        return "full"
    owner = f.via_module or mod.key
    return _rules(user).get(owner, {}).get(f.key, "full")


def hidden(mod, user=None) -> set[str]:
    return {f.key for f in mod.fields if level(mod, f, user) == "hidden"}


def readonly(mod, user=None) -> set[str]:
    return {f.key for f in mod.fields if level(mod, f, user) != "full"}


def can_see(mod_key: str, field_key: str, user=None) -> bool:
    from .registry import get_module

    mod = get_module(mod_key)
    f = mod.field_map.get(field_key)
    return f is None or level(mod, f, user) != "hidden"

