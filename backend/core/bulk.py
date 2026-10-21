"""Working on many records at once, and merging duplicates.

Bulk edit sets one or more fields on every selected record, each saved as if edited on its own
page (same validation, stamps, pipeline rules and log entries). For fields holding several values
(tags, multi-select dropdowns, several people or linked records) it can add to, remove from or
replace what each record has. Records that fail are reported; the rest are saved.

Merge keeps one record, takes the field values the person chose, moves everything linked to the
others onto it (contacts at a firm, outreach and deals of a company, tasks, tracker entries,
documents, lead-investor slots, notes) and sends the others to Deleted items.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import current
from .models import Note
from .records import apply_values, clean_values, read_value
from .registry import ModuleDef, enabled_map

MULTI_MODES = ("add", "remove", "replace")

REASONS = {
    "name": "Same name",
    "web": "Same website",
    "email": "Same email",
    "mobile": "Same mobile number",
    "linkedin": "Same LinkedIn profile",
    "open": "More than one open outreach for the same company",
}


def _multi(f) -> bool:
    return f.type == "tags" or bool(f.multi)


def _item_key(v):
    """Compare values in a multi field: link dicts by record, everything else as is."""
    if isinstance(v, dict) and "module" in v:
        return f"{v['module']}:{v['id']}"
    return v


def _combine(current: list, given: list, mode: str) -> list:
    keys = [_item_key(x) for x in given]
    if mode == "remove":
        return [x for x in current if _item_key(x) not in keys]
    have = {_item_key(x) for x in current}
    return [*current, *(x for x, k in zip(given, keys) if k not in have)]


def bulk_edit(mod: ModuleDef, objs, values: dict, modes: dict, user) -> dict:
    """Apply `values` to every object. Returns {"updated": n, "failed": [{id, title, errors}]}."""
    enabled = enabled_map(mod.key)
    updated, failed = 0, []
    for obj in objs:
        data = {}
        for key, given in values.items():
            f = mod.field_map.get(key)
            mode = modes.get(key, "replace")
            if f is not None and _multi(f) and mode in ("add", "remove"):
                current = read_value(obj, f) or []
                data[key] = _combine(list(current), list(given or []), mode)
            else:
                data[key] = given
        cleaned, errors = clean_values(mod, data, enabled=enabled, instance=obj, partial=True)
        if errors:
            failed.append({"id": obj.pk, "title": mod.title_of(obj), "errors": errors})
            continue
        try:
            with transaction.atomic():
                apply_values(obj, mod, cleaned, user)
        except (IntegrityError, ValueError) as e:
            failed.append({"id": obj.pk, "title": mod.title_of(obj), "errors": {"detail": str(e)}})
            continue
        updated += 1
    return {"updated": updated, "failed": failed}


def bulk_archive(objs, user, mod: ModuleDef | None = None) -> int:
    now = timezone.now()
    n = 0
    for obj in objs:
        obj.archived_at = now
        obj.archived_by = user
        obj.save(update_fields=["archived_at", "archived_by"])
        current.add_change({"record": f"{mod.key if mod else obj._meta.model_name}:{obj.pk}",
                            "title": mod.title_of(obj) if mod else str(obj), "kind": "deleted"})
        n += 1
    return n


# ---------------------------------------------------------------- merge


def _move_notes(module: str, from_id: int, to_id: int):
    Note.objects.filter(module=module, record_id=from_id).update(record_id=to_id)


def _tracker_conflicts(rel_field: str):
    """Tracker entries are one per investor per deal: when merging investors (or deals) two entries
    can end up describing the same pair. The other half of the pair, by the field being moved."""
    return {"investor": "deal", "deal": "investor"}.get(rel_field)


def merge(mod: ModuleDef, keeper, losers: list, values: dict, user) -> dict:
    """Merge `losers` into `keeper`. Returns counts of what moved."""
    from deals.models import DealInvestor

    model = mod.model
    moved: dict[str, int] = {}
    with transaction.atomic():
        if values:
            from . import access

            # Fields this person's role can't change keep the keeper's own value.
            values = {k: v for k, v in values.items() if k not in access.readonly(mod)}
            cleaned, errors = clean_values(mod, values, enabled=enabled_map(mod.key), instance=keeper, partial=True)
            if errors:
                raise MergeError(errors)
        else:
            cleaned = {}

        for loser in losers:
            for rel in model._meta.related_objects:
                other = rel.related_model
                label = str(other._meta.verbose_name_plural)
                if rel.many_to_many:
                    # Another record type lists this one (a deal's lead investors, a document's tags).
                    field = rel.field
                    through = field.remote_field.through
                    src, tgt = field.m2m_field_name(), field.m2m_reverse_field_name()
                    for row in through.objects.filter(**{tgt: loser.pk}):
                        if through.objects.filter(**{src: getattr(row, f"{src}_id"), tgt: keeper.pk}).exists():
                            row.delete()
                        else:
                            setattr(row, f"{tgt}_id", keeper.pk)
                            row.save()
                            moved[label] = moved.get(label, 0) + 1
                    continue
                if not (rel.one_to_many or rel.one_to_one):
                    continue
                field = rel.field
                manager = other._base_manager
                if rel.one_to_one:
                    # One-to-one (a deal's outreach attempt): move only if the keeper has none.
                    if manager.filter(**{field.name: keeper}).exists():
                        continue
                    n = manager.filter(**{field.name: loser}).update(**{field.name: keeper})
                elif other is DealInvestor and (pair := _tracker_conflicts(field.name)):
                    n = 0
                    for entry in manager.filter(**{field.name: loser}):
                        twin = manager.filter(**{field.name: keeper, pair: getattr(entry, f"{pair}_id")},
                                              archived_at__isnull=True).exclude(pk=entry.pk).first()
                        if twin and entry.archived_at is None:
                            # Already tracked for the keeper: keep that entry, fold this one's log into it.
                            _move_notes("deal_investors", entry.pk, twin.pk)
                            entry.archived_at = timezone.now()
                            entry.archived_by = user
                            entry.save(update_fields=["archived_at", "archived_by"])
                        else:
                            setattr(entry, field.name, keeper)
                            entry.save(update_fields=[field.name])
                            n += 1
                else:
                    n = manager.filter(**{field.name: loser}).update(**{field.name: keeper})
                if n:
                    moved[label] = moved.get(label, 0) + n

            if mod.timeline:
                _move_notes(mod.key, loser.pk, keeper.pk)
            from .custom import repoint_links

            repoint_links(mod.key, loser.pk, keeper.pk)  # custom link fields pointing at it
            loser.archived_at = timezone.now()
            loser.archived_by = user
            loser.save(update_fields=["archived_at", "archived_by"])
            current.add_change({"record": f"{mod.key}:{loser.pk}", "title": mod.title_of(loser),
                                "kind": f"merged into {mod.title_of(keeper)} and deleted"})

        if mod.key == "deals":
            from deals.access import merge_access

            merge_access(keeper, losers)
        apply_values(keeper, mod, cleaned, user)
        if mod.timeline:
            names = ", ".join(mod.title_of(x) for x in losers)
            Note.objects.create(module=mod.key, record_id=keeper.pk, kind="note", date=timezone.localdate(),
                                text=f"Merged in: {names}. Their links and notes are now here.", created_by=user)
    return moved


class MergeError(Exception):
    def __init__(self, errors: dict):
        super().__init__("Merge refused")
        self.errors = errors


# ---------------------------------------------------------------- finding duplicates


def duplicate_groups(mod: ModuleDef, user) -> list[dict]:
    """Records that look like the same thing, by the module's duplicate rules (the ones import uses).
    Records sharing any key end up in one group."""
    from .importer import _existing_keys

    index = _existing_keys(mod, user)
    parent: dict[int, int] = {}
    titles: dict[int, str] = {}
    reasons: dict[int, set[str]] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for key, recs in index.items():
        if len(recs) < 2:
            continue
        reason = REASONS.get(key.split(":", 1)[0], "Look the same")
        first = recs[0][0]
        for pk, title in recs:
            titles[pk] = title
            parent[find(pk)] = find(first)
        root = find(first)
        reasons.setdefault(root, set()).add(reason)

    groups: dict[int, list[int]] = {}
    for pk in titles:
        groups.setdefault(find(pk), []).append(pk)
    out = []
    for root, pks in groups.items():
        why = set()
        for r, rs in reasons.items():
            if find(r) == root:
                why |= rs
        out.append({"reasons": sorted(why), "records": [{"id": pk, "title": titles[pk]} for pk in sorted(pks)]})
    out.sort(key=lambda g: g["records"][0]["title"].lower())
    return out
