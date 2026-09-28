import re
from urllib.parse import urlparse

from core.registry import FieldDef, ModuleDef, default_name_key, register

from .models import CHANNEL_CHOICES, STRENGTH_CHOICES, Contact

B, C, D, R, P, O = "Basics", "Contact details", "Role in decisions", "Relationship", "Personal", "Other"

FIELDS = [
    FieldDef("full_name", "Full name", "text", B, required=True, list_default=True, searchable=True,
             aliases=["name", "contact name", "contact", "contact person", "person", "full name"],
             part_aliases=["first name", "firstname", "given name", "middle name", "last name", "lastname",
                           "surname", "family name"]),
    FieldDef("designation", "Designation", "text", B, list_default=True, searchable=True,
             aliases=["job title", "position", "role", "job role"]),
    FieldDef("firm", "Firm / company", "link", B, list_default=True, filterable=True, searchable=True,
             always_on=True, targets={"investors": "investor"}, target_labels={"investors": "Investor side"},
             help="The investor firm or client company this person works at.",
             aliases=["firm", "company", "organisation", "organization", "investor", "fund", "firm name",
                      "company name", "employer", "account"]),
    FieldDef("department", "Department / team", "text", B, searchable=True,
             aliases=["department", "team", "dept", "function"]),

    FieldDef("work_email", "Work email", "email", C, list_default=True, searchable=True,
             aliases=["email", "e-mail", "email address", "e-mail address", "official email", "office email",
                      "business email", "email id"]),
    FieldDef("personal_email", "Personal email", "email", C, searchable=True,
             aliases=["e-mail 2 address", "email 2", "personal e-mail", "other email", "secondary email"]),
    FieldDef("mobile", "Mobile", "phone", C, list_default=True, searchable=True,
             aliases=["mobile phone", "mobile number", "mobile no", "cell", "cell phone", "phone", "phone number",
                      "whatsapp", "whatsapp number", "contact number"]),
    FieldDef("office_phone", "Office phone", "phone", C, searchable=True,
             aliases=["business phone", "work phone", "landline", "office", "office number", "direct line"]),
    FieldDef("linkedin", "LinkedIn", "url", C,
             aliases=["linkedin url", "linkedin profile", "profile url", "url", "linkedin link"]),
    FieldDef("city", "City", "text", C, searchable=True, filterable=True,
             aliases=["location", "based in", "business city", "base"]),

    FieldDef("decision_level", "Decision-maker level", "dropdown", D, choice_list="decision_maker_levels",
             list_default=True, filterable=True, aliases=["seniority", "level", "dm level", "decision maker"]),
    FieldDef("primary_contact", "Primary point of contact", "boolean", D, filterable=True,
             aliases=["primary", "primary contact", "poc", "primary poc", "key contact", "point of contact"]),

    FieldDef("relationship_owner", "Relationship owner", "user", R, list_default=True, filterable=True,
             aliases=["owner", "rm", "relationship manager", "coverage", "covered by"]),
    FieldDef("relationship_strength", "Relationship strength", "choice", R, choices=STRENGTH_CHOICES,
             filterable=True, aliases=["strength", "relationship"]),
    FieldDef("last_contacted", "Last contacted", "date", R, list_default=True, filterable=True,
             aliases=["last contact", "last contacted on", "last touch", "last met", "last spoke"]),
    FieldDef("introduced_by", "Introduced by", "text", R, searchable=True,
             aliases=["referred by", "introducer", "intro by", "introduction", "referral", "source"]),

    FieldDef("birthday", "Birthday", "birthday", P, filterable=True,
             aliases=["birth date", "date of birth", "dob", "birth day"]),
    FieldDef("assistant_name", "Assistant / EA name", "text", P,
             aliases=["assistant", "ea", "ea name", "assistant name", "secretary", "pa", "executive assistant"]),
    FieldDef("assistant_contact", "Assistant / EA contact", "text", P,
             aliases=["assistant phone", "assistant email", "ea contact", "ea phone", "ea email",
                      "assistant contact", "assistant's phone"]),
    FieldDef("preferred_channel", "Preferred channel", "choice", P, choices=CHANNEL_CHOICES, filterable=True,
             aliases=["channel", "preferred mode", "contact preference", "preferred contact method"]),

    FieldDef("tags", "Tags", "tags", O, filterable=True, aliases=["labels", "categories", "groups"]),
    FieldDef("notes", "Notes", "longtext", O, searchable=True, aliases=["remarks", "comments", "note"]),
]


def _linkedin_handle(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if "linkedin.com" not in (parsed.hostname or ""):
        return ""
    m = re.match(r"/(in|pub)/([^/?#]+)", parsed.path or "")
    return m.group(2).lower() if m else ""


def duplicate_keys(values: dict) -> list[str]:
    """Same person if any of these match: an email, the mobile number, the LinkedIn profile,
    or the same name at the same firm."""
    keys = []
    for k in ("work_email", "personal_email"):
        if e := (values.get(k) or "").strip().lower():
            keys.append(f"email:{e}")
    digits = re.sub(r"\D", "", values.get("mobile") or "")
    if len(digits) >= 8:
        keys.append(f"mobile:{digits[-10:]}")
    if handle := _linkedin_handle(values.get("linkedin") or ""):
        keys.append(f"linkedin:{handle}")
    if name := default_name_key(values.get("full_name") or ""):
        keys.append(f"name:{name}@{values.get('firm') or ''}")
    return keys


def subtitle(c: Contact) -> str:
    firm = c.investor.firm_name if c.investor_id else ""
    return " · ".join(p for p in (c.designation, firm) if p)


MODULE = register(
    ModuleDef(
        key="contacts",
        label="Contacts",
        singular="Contact",
        model=Contact,
        fields=FIELDS,
        groups=[B, C, D, R, P, O],
        title_field="full_name",
        subtitle=subtitle,
        duplicate_keys=duplicate_keys,
        segment_field="firm",
    )
)
