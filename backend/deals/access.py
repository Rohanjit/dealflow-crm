"""Restricted deals: who can see them, and how that changes.

A restricted deal is seen only by its deal team (lead banker, MDs, VPs, associates, analysts),
the people added to it by name, and admins. Everything about it goes with it: its investor
tracker, log, documents tagged to it, tasks linked to it (except for the person they're assigned
to or who added them, who see the link as "Restricted deal") and the outreach attempt it came from.
The company stays visible. Everyone else sees a "Restricted deal" placeholder with "Ask for access".

Changes go through requests. Anyone on the deal team can ask to restrict the deal, lift the
restriction or give someone access; an MD on the deal approves (if it has no MD, the lead banker;
if neither, an admin). When an approver (or an admin) makes the change it applies at once.
Taking someone's access away needs no approval. Every change is written to the deal's log.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Note

from .models import AccessRequest, Deal

User = get_user_model()

TEAM_M2M = ("team_md", "team_vp", "team_associate", "team_analyst")
ROLE_LABELS = {"lead_banker": "Lead banker", "team_md": "MD", "team_vp": "VP", "team_associate": "Associate",
               "team_analyst": "Analyst"}


# ---------------------------------------------------------------- who can see what


def _seen_by(user) -> Q:
    return (Q(lead_banker=user) | Q(team_md=user) | Q(team_vp=user) | Q(team_associate=user)
            | Q(team_analyst=user) | Q(access=user))


def hidden_deals(user):
    """Ids of the deals this person can't see (a subquery), or None when they can see them all."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Deal.objects.filter(restricted=True).values("pk")
    if user.is_admin:
        return None
    seen = Deal.objects.filter(restricted=True).filter(_seen_by(user)).values("pk")
    return Deal.objects.filter(restricted=True).exclude(pk__in=seen).values("pk")


def can_see(user, deal: Deal) -> bool:
    if not deal.restricted or (user is not None and user.is_admin):
        return True
    if user is None:
        return False
    return Deal.objects.filter(pk=deal.pk).filter(_seen_by(user)).exists()


# Conditions for ModuleDef.hide: the records of each module that go with a hidden deal.

def hide_deals(user):
    ids = hidden_deals(user)
    return None if ids is None else Q(pk__in=ids)


def hide_tracker(user):
    ids = hidden_deals(user)
    return None if ids is None else Q(deal_id__in=ids)


def hide_outreach(user):
    ids = hidden_deals(user)
    return None if ids is None else Q(deal__in=ids)


def hide_documents(user):
    from vault.models import Document

    ids = hidden_deals(user)
    return None if ids is None else Q(pk__in=Document.deals.through.objects.filter(deal_id__in=ids).values("document_id"))


def hide_tasks(user):
    ids = hidden_deals(user)
    if ids is None:
        return None
    return Q(deal_id__in=ids) & ~Q(assigned_to=user) & ~Q(created_by=user)


# ---------------------------------------------------------------- the team and the approvers


def team(deal: Deal) -> list[tuple[str, User]]:
    out = []
    if deal.lead_banker_id:
        out.append(("lead_banker", deal.lead_banker))
    for attr in TEAM_M2M:
        out += [(attr, u) for u in getattr(deal, attr).all()]
    return out


def team_ids(deal: Deal) -> set[int]:
    return {u.pk for _, u in team(deal)}


def approvers(deal: Deal) -> list[User]:
    """MDs on the deal; with none, the lead banker; with neither, the admins (active people only)."""
    mds = [u for u in deal.team_md.all() if u.is_active]
    if mds:
        return mds
    if deal.lead_banker_id and deal.lead_banker.is_active:
        return [deal.lead_banker]
    return list(User.objects.filter(role__is_admin=True, is_active=True))


def can_approve(user, deal: Deal) -> bool:
    return user.is_admin or any(u.pk == user.pk for u in approvers(deal))


def can_request(user, deal: Deal) -> bool:
    return user.is_admin or user.pk in team_ids(deal)


# ---------------------------------------------------------------- changes


class AccessError(Exception):
    pass


def _log(deal: Deal, user, text: str):
    from core import current

    current.note(f"{deal.company.name if deal.company_id else 'Deal'}: {text}")
    Note.objects.create(module="deals", record_id=deal.pk, kind="note", date=timezone.localdate(), text=text, created_by=user)


def _describe(r: AccessRequest, done=True) -> str:
    """'Restricted the deal' (for the log) or 'Restrict the deal' (for a request)."""
    who = r.person.full_name if r.person_id else "someone"
    if done:
        return {"restrict": "Restricted the deal", "unrestrict": "Lifted the restriction",
                "add": f"Gave {who} access", "join": f"Gave {who} access (they asked for it)"}[r.kind]
    return {"restrict": "Restrict the deal", "unrestrict": "Lift the restriction",
            "add": f"Give {who} access", "join": f"{who} asks for access"}[r.kind]


def _apply(r: AccessRequest, approver):
    deal = r.deal
    if r.kind == "restrict":
        deal.restricted = True
        deal.save(update_fields=["restricted"])
    elif r.kind == "unrestrict":
        deal.restricted = False
        deal.save(update_fields=["restricted"])
    elif r.person_id:
        deal.access.add(r.person)
    r.status = "approved"
    r.decided_by = approver
    r.decided_at = timezone.now()
    r.save()
    who = r.requested_by.full_name
    by = f" (asked by {who})" if r.requested_by_id != approver.pk else ""
    _log(deal, approver, f"Access: {_describe(r)}{by}.")
    # Anything else waiting that this settles goes too.
    if r.kind in ("restrict", "unrestrict"):
        AccessRequest.objects.filter(deal=deal, kind__in=("restrict", "unrestrict"), status="pending").update(
            status="withdrawn", decided_at=timezone.now())
    elif r.person_id:
        AccessRequest.objects.filter(deal=deal, kind__in=("add", "join"), person=r.person, status="pending").update(
            status="withdrawn", decided_at=timezone.now())


@transaction.atomic
def ask(deal: Deal, user, kind: str, person=None) -> AccessRequest:
    """Ask for a change (or make it, for approvers and admins). Returns the request."""
    if kind == "join":
        if can_see(user, deal):
            raise AccessError("You can already see this deal.")
        person = user
    else:
        if not can_request(user, deal):
            raise AccessError("Only the deal team can change who sees this deal.")
        if kind == "restrict" and deal.restricted:
            raise AccessError("This deal is already restricted.")
        if kind == "unrestrict" and not deal.restricted:
            raise AccessError("This deal isn't restricted.")
        if kind == "add":
            if person is None or not person.is_active:
                raise AccessError("Pick someone to give access to.")
            if not deal.restricted:
                raise AccessError("Everyone who can see deals can see this one. Restrict it first.")
            if person.is_admin or person.pk in team_ids(deal) or deal.access.filter(pk=person.pk).exists():
                raise AccessError(f"{person.full_name} can already see this deal.")
    same = AccessRequest.objects.filter(deal=deal, kind=kind, person=person, status="pending").first()
    if same:
        return same
    r = AccessRequest.objects.create(deal=deal, kind=kind, person=person, requested_by=user)
    if kind != "join" and can_approve(user, deal):
        _apply(r, user)
    return r


@transaction.atomic
def decide(r: AccessRequest, user, decision: str):
    if r.status != "pending":
        raise AccessError("This request has already been dealt with.")
    if decision == "withdraw":
        if r.requested_by_id != user.pk:
            raise AccessError("Only the person who asked can withdraw it.")
        r.status, r.decided_at = "withdrawn", timezone.now()
        r.save()
        return
    if not can_approve(user, r.deal):
        raise AccessError("Only an MD on the deal (or the lead banker if it has no MD) can approve this.")
    if decision == "approve":
        _apply(r, user)
    elif decision == "decline":
        r.status, r.decided_by, r.decided_at = "declined", user, timezone.now()
        r.save()
        _log(r.deal, user, f"Access: declined “{_describe(r, done=False)}” (asked by {r.requested_by.full_name}).")
    else:
        raise AccessError("Approve or decline.")


def remove(deal: Deal, user, person):
    """Take away someone's named access. No approval needed; the team keeps its access."""
    if not can_request(user, deal) and user.pk != person.pk:
        raise AccessError("Only the deal team can change who sees this deal.")
    if not deal.access.filter(pk=person.pk).exists():
        raise AccessError(f"{person.full_name} wasn't added by name.")
    deal.access.remove(person)
    _log(deal, user, f"Access: took away {person.full_name}'s access.")


def merge_access(keeper: Deal, losers: list[Deal]):
    """Merging deals: if any was restricted the one kept is too, and everyone who could see one of
    them keeps seeing it (their team and named people are added by name)."""
    if not any(d.restricted for d in [keeper, *losers]):
        return
    if not keeper.restricted:
        keeper.restricted = True
        keeper.save(update_fields=["restricted"])
    have = team_ids(keeper) | set(keeper.access.values_list("pk", flat=True))
    for d in losers:
        for u in [u for _, u in team(d)] + list(d.access.all()):
            if u.pk not in have:
                keeper.access.add(u)
                have.add(u.pk)


# ---------------------------------------------------------------- what screens get


def request_payload(r: AccessRequest, user=None) -> dict:
    seen = user is None or can_see(user, r.deal)
    return {
        "id": r.pk,
        "deal": r.deal_id,
        "deal_title": (r.deal.company.name if r.deal.company_id else f"Deal {r.deal_id}") if seen else "Restricted deal",
        "kind": r.kind,
        "what": _describe(r, done=False),
        "person": {"id": r.person_id, "name": r.person.full_name} if r.person_id else None,
        "requested_by": {"id": r.requested_by_id, "name": r.requested_by.full_name},
        "created_at": r.created_at.isoformat(),
        "status": r.status,
        "decided_by": r.decided_by.full_name if r.decided_by_id else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


def placeholder(deal: Deal, user) -> dict:
    """What someone who can't see a restricted deal is told about it."""
    mine = AccessRequest.objects.filter(deal=deal, kind="join", requested_by=user).first()
    return {
        "id": deal.pk,
        "approvers": [u.full_name for u in approvers(deal)],
        "request": {"status": mine.status, "at": (mine.decided_at or mine.created_at).isoformat()} if mine else None,
    }


def access_payload(deal: Deal, user) -> dict:
    pending = AccessRequest.objects.filter(deal=deal, status="pending").select_related(
        "deal__company", "person", "requested_by", "decided_by")
    return {
        "restricted": deal.restricted,
        "team": [{"id": u.pk, "name": u.full_name, "role": ROLE_LABELS[attr]} for attr, u in team(deal)],
        "people": [{"id": u.pk, "name": u.full_name} for u in deal.access.order_by("full_name")],
        "approvers": [u.full_name for u in approvers(deal)],
        "can_request": can_request(user, deal),
        "can_approve": can_approve(user, deal),
        "pending": [request_payload(r) for r in pending],
    }


def requests_for(user) -> dict:
    """Pending requests this person can decide, and their own pending ones."""
    pending = AccessRequest.objects.filter(status="pending", deal__archived_at__isnull=True).select_related(
        "deal__company", "deal__lead_banker", "person", "requested_by")
    to_decide = [r for r in pending if r.requested_by_id != user.pk and can_approve(user, r.deal)]
    mine = [r for r in pending if r.requested_by_id == user.pk]
    return {"to_decide": [request_payload(r) for r in to_decide], "mine": [request_payload(r, user) for r in mine],
            "count": len(to_decide)}


def _placeholders(deals, user) -> list[dict]:
    deals = deals.filter(archived_at__isnull=True).select_related("lead_banker").prefetch_related("team_md")
    return [placeholder(d, user) for d in deals.order_by("pk")]


def deals_list_extra(params, user) -> dict:
    """The deals list mentions every restricted deal this person can't see (whatever the filters,
    so filtering can't reveal anything about them)."""
    from core.records import to_id

    ids = hidden_deals(user)
    if ids is None:
        return {}
    deals = Deal.objects.filter(pk__in=ids)
    # A company's page lists its deals: mention only that company's restricted ones there.
    module, _, pk = (params.get("company") or "").partition(":")
    if module == "companies":
        deals = deals.filter(company_id=to_id(pk))
    return {"restricted": _placeholders(deals, user)}


def tracker_list_extra(params, user) -> dict:
    """An investor's deal history mentions restricted deals the investor is on."""
    from core.records import to_id

    ids = hidden_deals(user)
    raw = params.get("investor") or ""
    module, _, pk = raw.partition(":")
    inv = to_id(pk) if module == "investors" else None
    if ids is None or inv is None:
        return {}
    on = Deal.objects.filter(pk__in=ids, tracker__investor_id=inv, tracker__archived_at__isnull=True).distinct()
    return {"restricted": _placeholders(on, user)}
