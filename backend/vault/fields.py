from django.db.models import Q

from core.registry import FieldDef, ModuleDef, register

from .models import Document
from .pin import require_open

D, F = "Document", "File"

FIELDS = [
    FieldDef("title", "Title", "text", D, required=True, list_default=True, searchable=True),
    FieldDef("category", "Category", "dropdown", D, choice_list="document_categories", list_default=True,
             filterable=True),
    FieldDef("tagged", "Tagged to", "link", D, multi=True, list_default=True, filterable=True, always_on=True,
             targets={"companies": "companies", "outreach": "outreach", "deals": "deals", "investors": "investors"},
             target_labels={"companies": "Companies", "outreach": "Outreach", "deals": "Deals", "investors": "Investors"},
             help="The companies, outreach, deals and investors this document is about. It shows on each of their pages."),
    FieldDef("description", "Description", "longtext", D, searchable=True),
    FieldDef("file_name", "File name", "text", F, readonly=True, searchable=True),
    FieldDef("file_type", "File type", "text", F, readonly=True, list_default=True, filterable=True),
    FieldDef("file_size", "Size", "integer", F, readonly=True, list_default=True),
    FieldDef("created_by", "Uploaded by", "user", F, readonly=True, list_default=True, filterable=True),
    FieldDef("created_at", "Uploaded on", "date", F, readonly=True, list_default=True, filterable=True),
]


def vault_filters(qs, params, user=None):
    """?about=deals:5 — documents about a record, including those tagged to its company (a company's
    audited accounts belong on each of its deals), and for a company those tagged to its outreach
    and deals too."""
    about = params.get("about")
    if not about:
        return qs
    from deals.models import Deal
    from outreach.models import Outreach

    from core.records import to_id

    module, _, raw = about.partition(":")
    pk = to_id(raw)
    if pk is None:
        return qs.none()
    if module == "investors":
        cond = Q(investors=pk)
    elif module == "companies":
        cond = Q(companies=pk) | Q(outreach__company_id=pk) | Q(deals__company_id=pk)
    elif module == "deals":
        company = Deal.objects.filter(pk=pk).values_list("company_id", flat=True).first()
        cond = Q(deals=pk) | Q(companies=company)
    elif module == "outreach":
        company = Outreach.objects.filter(pk=pk).values_list("company_id", flat=True).first()
        cond = Q(outreach=pk) | Q(companies=company)
    else:
        return qs.none()
    return qs.filter(pk__in=Document.objects.filter(cond).values("pk"))


def subtitle(d: Document) -> str:
    return " · ".join(p for p in (d.category.label if d.category_id else "", d.file_type) if p)


VAULT = register(
    ModuleDef(
        key="vault",
        label="Vault",
        singular="Document",
        model=Document,
        fields=FIELDS,
        groups=[D, F],
        title_field="title",
        subtitle=subtitle,
        extra_filters=vault_filters,
        importable=False,
        gate=require_open,
    )
)
