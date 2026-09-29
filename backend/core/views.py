import datetime as dt

from django.db import transaction
from django.db.models import Max, ProtectedError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsActiveUser, flag_required

from . import backups, importer
from .exporter import export_workbook
from .models import ChoiceList, ChoiceOption, FieldSwitch, FirmSettings, Note
from .modules import BUILT_MODULES
from .pagination import StandardPagination
from .records import apply_values, clean_values, filter_queryset, serialize, with_related
from .registry import CURRENCIES, UNITS, active_targets, all_modules, enabled_map, get_module


def _module(key: str):
    if key not in BUILT_MODULES:
        raise NotFound("Unknown module.")
    return get_module(key)


def _require(user, module: str, action: str):
    if not user.can(module, action):
        verb = {"view": "see", "create": "add to", "edit": "edit", "delete": "delete from"}[action]
        raise PermissionDenied(f"Your role can't {verb} {get_module(module).label}.")


# ---------------------------------------------------------------- schema


def choice_lists_payload(keys=None):
    qs = ChoiceList.objects.prefetch_related("options")
    if keys is not None:
        qs = qs.filter(key__in=keys)
    return {
        cl.key: {
            "key": cl.key,
            "label": cl.label,
            "module": cl.module,
            "description": cl.description,
            "options": [{"id": o.pk, "label": o.label, "is_active": o.is_active, "order": o.order} for o in cl.options.all()],
        }
        for cl in qs
    }


def _section(s: dict, perms: dict) -> dict:
    """ready: show the related records · coming: that module isn't built yet · hidden: no access."""
    target = s.get("module") or s.get("available_with")
    if target not in BUILT_MODULES:
        status_ = "coming"
    elif not perms.get(target, {}).get("view"):
        status_ = "hidden"
    else:
        status_ = "ready"
    return {**s, "status": status_, "available": status_ == "ready"}


def _segments(mod) -> dict | None:
    """Quick switch above a list, e.g. All · Investor side · Company side · Not linked."""
    if mod.segment_param:
        return {"param": mod.segment_param, "options": mod.segment_options or [], "default": mod.segment_default}
    if not mod.segment_field:
        return None
    f = mod.field(mod.segment_field)
    targets = active_targets(f)
    options = [{"value": "", "label": "All"}]
    options += [{"value": m, "label": f.target_labels.get(m, get_module(m).label)} for m, _ in targets]
    options.append({"value": "none", "label": "Not linked"})
    return {"param": f"{f.key}_type", "options": options, "default": ""}


def _can_lookup(user, module: str) -> bool:
    """Names of records in `module` are visible to people who can view it, and to people who can
    add or edit records in a module that links to it (they need the names to pick one)."""
    if user.can(module, "view"):
        return True
    for src in all_modules():
        if src.key not in BUILT_MODULES:
            continue
        links_here = any(f.type == "link" and module in dict(active_targets(f)) for f in src.fields)
        if links_here and (user.can(src.key, "create") or user.can(src.key, "edit")):
            return True
    return False


class LookupView(APIView):
    """Short list of {id, title} for link pickers: ?q=blu, or ?ids=3,7 to label chosen records."""

    def get(self, request, module):
        mod = _module(module)
        if not _can_lookup(request.user, module):
            raise PermissionDenied(f"Your role can't see {mod.label}.")
        ids = [int(p) for p in (request.query_params.get("ids") or "").split(",") if p.strip().isdigit()]
        if ids:
            qs = mod.model.objects.filter(pk__in=ids[:100])
        else:
            qs = mod.model.objects.filter(archived_at__isnull=True)
            if q := (request.query_params.get("q") or "").strip():
                qs = qs.filter(**{f"{mod.title_lookup}__icontains": q})
            qs = qs.order_by(mod.title_lookup, "pk")[:20]
        qs = with_related(qs, mod)
        return Response(
            [
                {
                    "module": module,
                    "id": o.pk,
                    "title": mod.title_of(o),
                    "subtitle": mod.subtitle(o) if mod.subtitle else "",
                    "archived": o.archived_at is not None,
                }
                for o in qs
            ]
        )


class SchemaView(APIView):
    """Everything the frontend needs to render the modules this user can see."""

    def get(self, request):
        user = request.user
        perms = user.role.module_perms()
        modules = []
        list_keys = set()
        for mod in all_modules():
            if mod.key not in BUILT_MODULES or not perms[mod.key]["view"]:
                continue
            enabled = enabled_map(mod.key)
            fields = []
            for f in mod.fields:
                d = f.schema()
                d["enabled"] = enabled[f.key]
                fields.append(d)
                if f.choice_list:
                    list_keys.add(f.choice_list)
            modules.append(
                {
                    "key": mod.key,
                    "label": mod.label,
                    "singular": mod.singular,
                    "title_field": mod.title_field,
                    "groups": mod.groups,
                    "fields": fields,
                    "sections": [_section(s, perms) for s in mod.sections],
                    "segments": _segments(mod),
                    "pipeline": mod.pipeline.schema() if mod.pipeline else None,
                    "timeline": mod.timeline,
                    "importable": mod.importable,
                    "perms": perms[mod.key],
                }
            )
        return Response(
            {
                "modules": modules,
                "choice_lists": choice_lists_payload(list_keys),
                "currencies": [{"code": c, "symbol": s} for c, s in CURRENCIES],
                "units": [{"value": v, "label": lbl} for v, lbl in UNITS],
            }
        )


# ---------------------------------------------------------------- records


class RecordListView(APIView):
    def get(self, request, module):
        mod = _module(module)
        _require(request.user, module, "view")
        enabled = enabled_map(module)
        qs = mod.model.objects.filter(archived_at__isnull=True)
        qs = with_related(filter_queryset(qs, mod, request.query_params, request.user, enabled), mod)
        pager = StandardPagination()
        page = pager.paginate_queryset(qs, request, view=self)
        return pager.get_paginated_response([serialize(o, mod) for o in page])

    def post(self, request, module):
        mod = _module(module)
        _require(request.user, module, "create")
        values, errors = clean_values(mod, request.data, enabled=enabled_map(module))
        if errors:
            raise ValidationError(errors)
        obj = apply_values(mod.model(), mod, values, request.user)
        obj = with_related(mod.model.objects.filter(pk=obj.pk), mod).get()
        return Response(serialize(obj, mod), status=status.HTTP_201_CREATED)


class RecordDetailView(APIView):
    def _get(self, mod, pk):
        obj = with_related(mod.model.objects.filter(pk=pk, archived_at__isnull=True), mod).first()
        if not obj:
            raise NotFound(f"This {mod.singular.lower()} doesn't exist or has been archived.")
        return obj

    def get(self, request, module, pk):
        mod = _module(module)
        _require(request.user, module, "view")
        return Response(serialize(self._get(mod, pk), mod))

    def patch(self, request, module, pk):
        mod = _module(module)
        _require(request.user, module, "edit")
        obj = self._get(mod, pk)
        values, errors = clean_values(mod, request.data, enabled=enabled_map(module), instance=obj, partial=True)
        if errors:
            raise ValidationError(errors)
        apply_values(obj, mod, values, request.user)
        return Response(serialize(self._get(mod, pk), mod))

    def delete(self, request, module, pk):
        """Deleting archives the record. Only admins can remove it for good (from the Archive)."""
        mod = _module(module)
        _require(request.user, module, "delete")
        obj = self._get(mod, pk)
        obj.archived_at = timezone.now()
        obj.archived_by = request.user
        obj.save(update_fields=["archived_at", "archived_by"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecordExportView(APIView):
    def get(self, request, module):
        mod = _module(module)
        _require(request.user, module, "view")
        enabled = enabled_map(module)
        qs = mod.model.objects.filter(archived_at__isnull=True)
        qs = with_related(filter_queryset(qs, mod, request.query_params, request.user, enabled), mod)
        cols = [c for c in (request.query_params.get("columns") or "").split(",") if c]
        data = export_workbook(mod, qs, request.user, enabled, cols or None)
        stamp = timezone.localtime().strftime("%Y-%m-%d")
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="dealflow-{module}-{stamp}.xlsx"'
        return resp


# ---------------------------------------------------------------- interaction log


NOTE_KINDS = {k for k, _ in Note.KINDS} - {"stage"}


def note_payload(n: Note, user) -> dict:
    return {
        "id": n.pk,
        "kind": n.kind,
        "date": n.date.isoformat(),
        "text": n.text,
        "meta": n.meta,
        "created_by": {"id": n.created_by_id, "name": n.created_by.full_name} if n.created_by else None,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
        "can_edit": n.kind != "stage" and (n.created_by_id == user.pk or user.is_admin),
    }


def _clean_note(data: dict, partial=False) -> dict:
    out, errors = {}, {}
    if "kind" in data or not partial:
        kind = data.get("kind") or "note"
        if kind not in NOTE_KINDS:
            errors["kind"] = "Pick note, call, meeting or email."
        out["kind"] = kind
    if "date" in data or not partial:
        raw = data.get("date")
        try:
            out["date"] = dt.date.fromisoformat(str(raw)[:10]) if raw else timezone.localdate()
        except ValueError:
            errors["date"] = "Enter a date as YYYY-MM-DD."
    if "text" in data or not partial:
        text = str(data.get("text") or "").strip()
        if not text:
            errors["text"] = "Write what happened."
        out["text"] = text[:20000]
    if errors:
        raise ValidationError(errors)
    return out


class NotesView(APIView):
    """A record's interaction log: dated notes plus automatic stage changes."""

    def _record(self, request, module, pk, action):
        mod = _module(module)
        if not mod.timeline:
            raise NotFound("This module has no interaction log.")
        _require(request.user, module, action)
        if not mod.model.objects.filter(pk=pk, archived_at__isnull=True).exists():
            raise NotFound(f"This {mod.singular.lower()} doesn't exist or has been archived.")
        return mod

    def get(self, request, module, pk):
        mod = self._record(request, module, pk, "view")
        notes = Note.objects.filter(module=mod.key, record_id=pk).select_related("created_by")
        return Response([note_payload(n, request.user) for n in notes])

    def post(self, request, module, pk):
        mod = self._record(request, module, pk, "edit")
        n = Note.objects.create(module=mod.key, record_id=pk, created_by=request.user, **_clean_note(request.data))
        return Response(note_payload(n, request.user), status=status.HTTP_201_CREATED)


class NoteDetailView(APIView):
    def _note(self, request, pk) -> Note:
        n = get_object_or_404(Note.objects.select_related("created_by"), pk=pk)
        if not request.user.can(n.module, "view"):
            raise NotFound
        if n.kind == "stage":
            raise PermissionDenied("Stage changes are recorded automatically and can't be changed.")
        if n.created_by_id != request.user.pk and not request.user.is_admin:
            raise PermissionDenied("Only the person who wrote this note (or an admin) can change it.")
        return n

    def patch(self, request, pk):
        n = self._note(request, pk)
        for k, v in _clean_note(request.data, partial=True).items():
            setattr(n, k, v)
        n.save()
        return Response(note_payload(n, request.user))

    def delete(self, request, pk):
        self._note(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- import


def _importable(key: str):
    mod = _module(key)
    if not mod.importable:
        raise NotFound(f"{mod.label} can't be imported from a file.")
    return mod


class ImportUploadView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request, module):
        mod = _importable(module)
        _require(request.user, module, "create")
        f = request.FILES.get("file")
        if not f:
            raise ValidationError({"file": "Choose a file to upload."})
        if f.size > 20 * 1024 * 1024:
            raise ValidationError({"file": "Files are limited to 20 MB."})
        try:
            rows = importer.read_rows(f.name, f.read())
        except importer.ImportError_ as e:
            raise ValidationError({"file": str(e)})
        if len(rows) < 2:
            raise ValidationError({"file": "The file has a header row but no data."})
        enabled = enabled_map(module)
        token = importer.save_upload(module, request.user, f.name, rows)
        headers = rows[0]
        return Response(
            {
                "token": token,
                "filename": f.name,
                "headers": headers,
                "sample": rows[1:6],
                "row_count": len(rows) - 1,
                "mapping": importer.suggest_mapping(mod, headers, enabled),
                "fields": importer.importable_fields(mod, enabled),
                "can_add_options": request.user.has_flag("manage_settings"),
                # Link fields whose unmatched names can be created as new records: {field: [modules]}
                "can_create_links": {
                    f.key: [m for m, _ in active_targets(f) if request.user.can(m, "create")]
                    for f in mod.fields if f.type == "link" and enabled.get(f.key, True)
                },
            }
        )


class _ImportStep(APIView):
    def _prepare(self, request, module):
        mod = _importable(module)
        _require(request.user, module, "create")
        enabled = enabled_map(module)
        try:
            upload = importer.load_upload(request.data.get("token"), module, request.user)
            rows = upload["rows"]
            mapping = importer.validate_mapping(mod, request.data.get("mapping"), len(rows[0]), enabled)
        except importer.ImportError_ as e:
            raise ValidationError({"detail": str(e)})
        create_links = request.data.get("create_links")
        create_links = {str(k): str(v) for k, v in create_links.items()} if isinstance(create_links, dict) else {}
        return mod, enabled, rows[1:], mapping, bool(request.data.get("add_missing_options")), create_links


class ImportCheckView(_ImportStep):
    def post(self, request, module):
        mod, enabled, rows, mapping, add_missing, create_links = self._prepare(request, module)
        return Response(importer.check_rows(mod, rows, mapping, request.user, enabled, add_missing, create_links))


class ImportCommitView(_ImportStep):
    def post(self, request, module):
        mod, enabled, rows, mapping, add_missing, create_links = self._prepare(request, module)
        include = request.data.get("include_rows")
        if not isinstance(include, list):
            raise ValidationError({"include_rows": "Pick the rows to import."})
        include = {int(n) for n in include if str(n).isdigit()}
        with transaction.atomic():
            result = importer.commit_rows(mod, rows, mapping, request.user, enabled, include, add_missing, create_links)
        importer.discard_upload(request.data.get("token"))
        return Response(result)


# ---------------------------------------------------------------- global search


class SearchView(APIView):
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"results": []})
        out = []
        for mod in all_modules():
            if mod.key not in BUILT_MODULES or not mod.in_search or not request.user.can(mod.key, "view"):
                continue
            enabled = enabled_map(mod.key)
            qs = filter_queryset(
                mod.model.objects.filter(archived_at__isnull=True), mod, {"q": q}, request.user, enabled
            )
            qs = with_related(qs, mod)
            total = qs.count()
            for obj in qs[:6]:
                out.append(
                    {
                        "module": mod.key,
                        "module_label": mod.label,
                        "id": obj.pk,
                        "title": mod.title_of(obj),
                        "subtitle": mod.subtitle(obj) if mod.subtitle else "",
                    }
                )
            if total > 6:
                out.append({"module": mod.key, "module_label": mod.label, "more": total - 6})
        return Response({"results": out})


# ---------------------------------------------------------------- archive


class ArchiveListView(APIView):
    permission_classes = [flag_required("restore_archive")]

    def get(self, request):
        module = request.query_params.get("module")
        q = (request.query_params.get("q") or "").strip()
        out = []
        for mod in all_modules():
            if mod.key not in BUILT_MODULES or (module and module != mod.key):
                continue
            qs = with_related(mod.model.objects.filter(archived_at__isnull=False), mod)
            if q:
                qs = qs.filter(**{f"{mod.title_lookup}__icontains": q})
            for obj in qs.order_by("-archived_at")[:500]:
                out.append(
                    {
                        "module": mod.key,
                        "module_label": mod.label,
                        "id": obj.pk,
                        "title": mod.title_of(obj),
                        "subtitle": mod.subtitle(obj) if mod.subtitle else "",
                        "archived_at": obj.archived_at.isoformat(),
                        "archived_by": obj.archived_by.full_name if obj.archived_by else None,
                    }
                )
        out.sort(key=lambda r: r["archived_at"], reverse=True)
        return Response({"results": out})


class ArchiveItemView(APIView):
    permission_classes = [flag_required("restore_archive")]

    def _get(self, module, pk):
        mod = _module(module)
        return mod, get_object_or_404(mod.model, pk=pk, archived_at__isnull=False)

    def post(self, request, module, pk):
        """Restore."""
        mod, obj = self._get(module, pk)
        if mod.validate:
            # e.g. the same investor may have been added to that deal's tracker again since.
            links = {f.key: (m, getattr(obj, attr)) for f in mod.fields if f.type == "link" and not f.multi and not f.via
                     for m, attr in active_targets(f) if getattr(obj, f"{attr}_id") is not None}
            if errors := mod.validate(links, obj, False):
                raise ValidationError({"detail": " ".join(errors.values())})
        obj.archived_at = None
        obj.archived_by = None
        obj.updated_by = request.user
        obj.save()
        return Response({"id": obj.pk, "module": module})

    def delete(self, request, module, pk):
        """Delete permanently. Admins only."""
        if not request.user.is_admin:
            raise PermissionDenied("Only admins can delete records permanently.")
        mod, obj = self._get(module, pk)
        try:
            with transaction.atomic():
                obj.delete()  # its interaction log goes with it (core.signals)
        except ProtectedError as e:
            kinds = sorted({str(type(o)._meta.verbose_name_plural) for o in e.protected_objects})
            raise ValidationError({"detail": f"{mod.title_of(obj)} still has {' and '.join(kinds)}. "
                                             "Delete those permanently first."})
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- settings: firm


def firm_payload(fs: FirmSettings):
    return {
        "firm_name": fs.firm_name,
        "backup_keep": fs.backup_keep,
        "backup_hour": fs.backup_hour,
        "backups_enabled": fs.backups_enabled,
        "last_backup_at": fs.last_backup_at.isoformat() if fs.last_backup_at else None,
        "last_backup_error": fs.last_backup_error,
        "dashboard_days": fs.dashboard_days,
    }


class FirmSettingsView(APIView):
    def get_permissions(self):
        return [IsActiveUser()] if self.request.method == "GET" else [flag_required("manage_settings")()]

    def get(self, request):
        return Response(firm_payload(FirmSettings.load()))

    def patch(self, request):
        fs = FirmSettings.load()
        errors = {}
        if "firm_name" in request.data:
            name = str(request.data["firm_name"] or "").strip()
            if not name:
                errors["firm_name"] = "Enter the firm name."
            fs.firm_name = name[:150]
        if "backup_keep" in request.data or "backup_hour" in request.data or "backups_enabled" in request.data:
            if not request.user.is_admin:
                raise PermissionDenied("Only admins can change backup settings.")
        if "backup_keep" in request.data:
            try:
                fs.backup_keep = int(request.data["backup_keep"])
                if not 1 <= fs.backup_keep <= 365:
                    raise ValueError
            except (TypeError, ValueError):
                errors["backup_keep"] = "Keep between 1 and 365 backups."
        if "backup_hour" in request.data:
            try:
                fs.backup_hour = int(request.data["backup_hour"])
                if not 0 <= fs.backup_hour <= 23:
                    raise ValueError
            except (TypeError, ValueError):
                errors["backup_hour"] = "Pick an hour between 0 and 23."
        if "backups_enabled" in request.data:
            fs.backups_enabled = bool(request.data["backups_enabled"])
        if "dashboard_days" in request.data:
            try:
                fs.dashboard_days = int(request.data["dashboard_days"])
                if not 1 <= fs.dashboard_days <= 365:
                    raise ValueError
            except (TypeError, ValueError):
                errors["dashboard_days"] = "Pick between 1 and 365 days."
        if errors:
            raise ValidationError(errors)
        fs.save()
        return Response(firm_payload(fs))


# ---------------------------------------------------------------- settings: field switches


class FieldSwitchView(APIView):
    permission_classes = [flag_required("manage_settings")]

    def get(self, request):
        out = []
        for mod in all_modules():
            if mod.key not in BUILT_MODULES:
                continue
            enabled = enabled_map(mod.key)
            out.append(
                {
                    "module": mod.key,
                    "label": mod.label,
                    "groups": mod.groups,
                    "fields": [
                        {"key": f.key, "label": f.label, "group": f.group, "type": f.type,
                         "enabled": enabled[f.key], "can_disable": f.can_disable, "required": f.required}
                        for f in mod.fields if not f.via
                    ],
                }
            )
        return Response({"modules": out})

    def patch(self, request):
        module = request.data.get("module")
        key = request.data.get("field_key")
        mod = _module(module)
        try:
            f = mod.field(key)
        except KeyError:
            raise ValidationError({"field_key": "Unknown field."})
        if f.via:
            owner = get_module(f.via_module)
            raise ValidationError({"field_key": f"{f.label} belongs to {owner.label}; switch it there."})
        enabled = bool(request.data.get("enabled"))
        if not enabled and not f.can_disable:
            why = "is required" if f.required else "is used by other screens"
            raise ValidationError({"field_key": f"{f.label} {why} and can't be switched off."})
        FieldSwitch.objects.update_or_create(module=module, field_key=key, defaults={"enabled": enabled})
        return Response({"module": module, "field_key": key, "enabled": enabled})


# ---------------------------------------------------------------- settings: dropdown lists


class ChoiceListsView(APIView):
    def get(self, request):
        used = {f.choice_list for m in all_modules() if m.key in BUILT_MODULES for f in m.fields if f.choice_list}
        lists = choice_lists_payload(used)
        return Response({"lists": sorted(lists.values(), key=lambda cl: cl["label"])})


def _label(value, choice_list, exclude_pk=None):
    label = " ".join(str(value or "").split())[:120]
    if not label:
        raise ValidationError({"label": "Enter a name for the option."})
    clash = choice_list.options.filter(label__iexact=label)
    if exclude_pk:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        o = clash.first()
        msg = f"“{label}” is already in this list." + ("" if o.is_active else " It was removed; restore it instead.")
        raise ValidationError({"label": msg})
    return label


class ChoiceOptionCreateView(APIView):
    permission_classes = [flag_required("manage_settings")]

    def post(self, request, key):
        cl = get_object_or_404(ChoiceList, key=key)
        label = _label(request.data.get("label"), cl)
        order = (cl.options.aggregate(m=Max("order"))["m"] or 0) + 1
        o = ChoiceOption.objects.create(choice_list=cl, label=label, order=order)
        return Response({"id": o.pk, "label": o.label, "is_active": True, "order": o.order}, status=201)


class ChoiceListReorderView(APIView):
    permission_classes = [flag_required("manage_settings")]

    def post(self, request, key):
        cl = get_object_or_404(ChoiceList, key=key)
        ids = request.data.get("ids") or []
        opts = {o.pk: o for o in cl.options.all()}
        if sorted(ids) != sorted(opts):
            raise ValidationError({"ids": "Send every option in the list."})
        for i, pk in enumerate(ids):
            ChoiceOption.objects.filter(pk=pk).update(order=i)
        return Response({"ok": True})


class ChoiceOptionView(APIView):
    permission_classes = [flag_required("manage_settings")]

    def patch(self, request, pk):
        o = get_object_or_404(ChoiceOption, pk=pk)
        if "label" in request.data:
            o.label = _label(request.data["label"], o.choice_list, exclude_pk=o.pk)
        if "is_active" in request.data:
            o.is_active = bool(request.data["is_active"])
        o.save()
        return Response({"id": o.pk, "label": o.label, "is_active": o.is_active, "order": o.order})


# ---------------------------------------------------------------- backups (admin only)


class AdminOnly(IsActiveUser):
    message = "Only admins can manage backups."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin


class BackupListView(APIView):
    permission_classes = [AdminOnly]

    def get(self, request):
        from django.conf import settings as dj

        return Response(
            {"backups": backups.list_backups(), "folder": str(dj.BACKUP_DIR), **firm_payload(FirmSettings.load())}
        )

    def post(self, request):
        try:
            p = backups.create_backup("manual")
        except backups.BackupError as e:
            raise ValidationError({"detail": str(e)})
        return Response({"name": p.name}, status=201)


class BackupDownloadView(APIView):
    permission_classes = [AdminOnly]

    def get(self, request, name):
        try:
            p = backups.backup_path(name)
        except backups.BackupError:
            raise Http404
        return FileResponse(open(p, "rb"), as_attachment=True, filename=p.name, content_type="application/zip")


class BackupRestoreView(APIView):
    permission_classes = [AdminOnly]
    parser_classes = [MultiPartParser]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            raise ValidationError({"file": "Choose a backup file."})
        if request.data.get("confirm") != "RESTORE":
            raise ValidationError({"confirm": "Type RESTORE to confirm."})
        try:
            result = backups.restore_backup(f)
        except backups.BackupError as e:
            raise ValidationError({"file": str(e)})
        return Response(result)


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"ok": True})
