"""Reading a person's details from their email signature (or a pasted block of text).

Plain rules, no AI: find the signature (after a sign-off like "Regards" or a "--" line, else the last
lines), then pick out the name, title, company, phone numbers, LinkedIn and website. Everything found
is a suggestion the person checks before saving.
"""

from __future__ import annotations

import re

SIGN_OFF = re.compile(
    r"^\s*(best(\s+(regards|wishes))?|kind\s+regards|warm(est)?\s+regards|regards|thanks(\s+(and|&)\s+regards)?|"
    r"thank\s+you|many\s+thanks|cheers|sincerely|yours(\s+(sincerely|truly|faithfully))?|rgds|br|thx)[\s,.!-]*$",
    re.I,
)
QUOTE_START = re.compile(r"^\s*(>|on .{5,200} wrote:\s*$|from:\s|-{2,}\s*original message|_{5,}|sent from my)", re.I)
TITLE_WORDS = re.compile(
    r"\b(partner|director|manager|vice president|vp|svp|avp|principal|associate|analyst|chief|ceo|cfo|coo|cto|cio|"
    r"founder|co-founder|head|officer|md|managing|president|chairman|chairperson|consultant|advisor|adviser|"
    r"counsel|lead|executive|investment|banker|controller|treasurer|secretary|investor relations)\b", re.I)
COMPANY_WORDS = re.compile(
    r"\b(ltd|limited|llp|pvt|private|inc|llc|plc|gmbh|capital|ventures|partners|advisors|advisers|advisory|bank|"
    r"fund|funds|group|holdings|securities|technologies|tech|labs|industries|pharma|foods|corp|corporation|company|"
    r"co\.|investments|asset management|family office)\b", re.I)
PHONE = re.compile(r"(?<![\w/])(\+?\d[\d\s().-]{7,18}\d)(?![\w/])")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%\-]+/?", re.I)
WEBSITE = re.compile(r"\b(?:https?://)?(?:www\.)[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:/[^\s]*)?", re.I)
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
MOBILE_HINT = re.compile(r"\b(m|mob|mobile|cell|cellphone|whatsapp|wa)\b\s*[:.]?", re.I)
OFFICE_HINT = re.compile(r"\b(t|tel|telephone|o|off|office|direct|d|landline|board|p|ph|phone|f|fax)\b\s*[:.]?", re.I)


def _body(text: str) -> list[str]:
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if QUOTE_START.match(line):
            break
        lines.append(line.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def signature_lines(text: str) -> list[str]:
    lines = _body(text)
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() in ("--", "-- ", "—"):
            start = i + 1
            break
        if SIGN_OFF.match(lines[i]) and i >= len(lines) - 15:
            start = i + 1
            break
    block = lines[start:] if start is not None else lines[-8:]
    return [ln.strip() for ln in block if ln.strip()][:12]


def _digits(s: str) -> str:
    return re.sub(r"[^\d+]", "", s)


def _phone_kind(line: str, number: str) -> str:
    before = line[: line.find(number)] if number in line else line
    before = re.split(r"[|,;/•·]", before)[-1]  # only the label right before this number
    if MOBILE_HINT.search(before):
        return "mobile"
    if OFFICE_HINT.search(before):
        return "office"
    d = re.sub(r"\D", "", number)
    if len(d) >= 10 and d[-10] in "6789" and (len(d) == 10 or d.startswith("91")):
        return "mobile"  # an Indian mobile number
    return "office"


def _same_person(line: str, name: str, email: str) -> bool:
    words = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", name or "")}
    local = re.findall(r"[a-z]{2,}", (email or "").split("@")[0].lower())
    lw = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", line)}
    return bool(words and words & lw) or bool(local and set(local) & lw)


def parse(text: str, sender_name: str = "", sender_email: str = "") -> dict:
    """Suggested contact details: full_name, designation, company, mobile, office_phone, linkedin,
    website, city (only what was found)."""
    lines = signature_lines(text)
    out: dict[str, str] = {}
    whole = "\n".join(lines)
    if m := LINKEDIN.search("\n".join(_body(text))):
        url = m.group(0)
        out["linkedin"] = url if url.lower().startswith("http") else "https://" + url
    for line in lines:
        for m in PHONE.finditer(line):
            num = m.group(1)
            d = re.sub(r"\D", "", num)
            if not 8 <= len(d) <= 15 or re.fullmatch(r"(19|20)\d{2}", d[:4]) and len(d) == 8:
                continue
            kind = _phone_kind(line, num)
            key = "mobile" if kind == "mobile" else "office_phone"
            label = re.split(r"[|,;/•·]", line[: line.find(num)])[-1]
            if re.search(r"\bf(ax)?\b", label, re.I):
                continue  # a fax number
            out.setdefault(key, _digits(num) if num.strip().startswith("+") else num.strip())
    for m in WEBSITE.finditer(whole):
        if "linkedin.com" not in m.group(0).lower():
            out.setdefault("website", m.group(0) if m.group(0).lower().startswith("http") else "https://" + m.group(0))
            break
    plain = [ln for ln in lines if not EMAIL.search(ln) and not LINKEDIN.search(ln) and not WEBSITE.search(ln)
             and len(re.sub(r"\D", "", ln)) < 7 and len(ln) <= 90]
    # Name: a line that looks like the sender, else the first short line of words.
    sender = (sender_name or "").strip().strip('"')
    for ln in plain:
        if _same_person(ln, sender_name, sender_email) and not TITLE_WORDS.search(ln) and len(ln.split()) <= 5:
            name = re.sub(r"\s*[|,]\s*$", "", ln).strip()
            words = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", name)}
            fuller = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", sender)}
            out["full_name"] = sender if sender and "@" not in sender and words < fuller else name
            break
    if "full_name" not in out and sender and "@" not in sender:
        out["full_name"] = sender
    if "full_name" not in out:  # a pasted block: the first line that reads like a name
        for ln in plain:
            words = ln.split()
            if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words) and not TITLE_WORDS.search(ln) \
                    and not COMPANY_WORDS.search(ln) and not SIGN_OFF.match(ln):
                out["full_name"] = ln
                break
    for ln in plain:
        if ln == out.get("full_name") or _same_person(ln, out.get("full_name", ""), "") and not TITLE_WORDS.search(ln):
            continue
        # "Partner | Northwind Ventures" or "Partner, Northwind Ventures"
        parts = [p.strip() for p in re.split(r"\s[|•·]\s|\s[-–]\s|,\s", ln) if p.strip()]
        for p in parts:
            if "designation" not in out and TITLE_WORDS.search(p) and not COMPANY_WORDS.search(p) and len(p) <= 60:
                out["designation"] = p
            elif "company" not in out and COMPANY_WORDS.search(p) and len(p) <= 80:
                out["company"] = p
    if "company" not in out:
        for ln in plain:
            if ln not in (out.get("full_name"), out.get("designation")) and ln[:1].isupper() and len(ln.split()) <= 6 \
                    and not TITLE_WORDS.search(ln) and not SIGN_OFF.match(ln) \
                    and not _same_person(ln, out.get("full_name", "") or sender_name, sender_email):
                out["company"] = ln
                break
    return {k: v[:200] for k, v in out.items() if v}


def from_text(text: str) -> dict:
    """A pasted signature or selection: also pick up an email address in it."""
    out = parse(text)
    if m := EMAIL.search(text or ""):
        out.setdefault("work_email", m.group(0).lower())
    return out
