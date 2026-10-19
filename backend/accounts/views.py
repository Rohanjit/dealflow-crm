import secrets

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core.models import FirmSettings
from core.modules import ACTIONS, BUILT_MODULES, MODULES

from .models import DATE_FORMATS, NUMBER_GROUPINGS, Invite, Role, RoleModulePermission, User
from core.guest import NotInSample

from .permissions import IsActiveUser, flag_required


class LoginThrottle(AnonRateThrottle):
    rate = "10/min"
    scope = "login"


def me_payload(user: User) -> dict:
    return {
        "id": user.pk,
        "email": user.email,
        "full_name": user.full_name,
        "role": {"id": user.role_id, "name": user.role.name, "is_admin": user.role.is_admin},
        "perms": user.role.module_perms(),
        "flags": user.role.flags(),
        "date_format": user.date_format,
        "number_grouping": user.number_grouping,
        "must_change_password": user.must_change_password,
    }


def _password_errors(password, user=None):
    try:
        validate_password(password or "", user)
    except DjangoValidationError as e:
        return " ".join(e.messages)
    return None


def _temp_password():
    # Easy to read out over the phone: three groups, no look-alike characters.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


# ---------------------------------------------------------------- app bootstrap and setup


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AppView(APIView):
    """First call the frontend makes: is setup needed, who am I."""

    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings as dj_settings

        from core import guest

        fs = FirmSettings.load()
        user = request.user if request.user.is_authenticated and request.user.is_active else None
        return Response(
            {
                "needs_setup": not dj_settings.DEMO and not User.objects.exists(),
                "demo": dj_settings.DEMO,
                "sample": fs.sample_loaded_at is not None,
                "guest_available": guest.guest_available(),
                "is_guest": guest.is_guest(user),
                "workspace_kind": fs.workspace_kind,
                "firm_name": fs.firm_name,
                "user": me_payload(user) if user else None,
                "built_modules": BUILT_MODULES,
                "modules": [{"key": k, "label": lbl} for k, lbl in MODULES],
                "date_formats": [v for v, _ in DATE_FORMATS],
                "number_groupings": [{"value": v, "label": lbl} for v, lbl in NUMBER_GROUPINGS],
            }
        )


class SetupView(APIView):
    """One-time setup wizard: firm name + the first Admin account."""

    permission_classes = [AllowAny]

    def post(self, request):
        from django.conf import settings as dj_settings

        if dj_settings.DEMO:
            raise PermissionDenied("This is a demo site: it can't be set up. Continue as a guest instead.")
        d = request.data
        errors = {}
        kind = "individual" if d.get("workspace_kind") == "individual" else "firm"
        firm = str(d.get("firm_name") or "").strip()[:150]
        name = str(d.get("full_name") or "").strip()
        email = str(d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        if not firm and kind == "firm":
            errors["firm_name"] = "Enter your firm's name."
        if not name:
            errors["full_name"] = "Enter your name."
        try:
            validate_email(email)
        except DjangoValidationError:
            errors["email"] = "Enter a valid email address."
        if msg := _password_errors(password):
            errors["password"] = msg
        if errors:
            raise ValidationError(errors)
        with transaction.atomic():
            # Re-check inside the transaction so two browsers can't both set up.
            if User.objects.select_for_update().exists():
                raise PermissionDenied("Dealflow is already set up. Sign in instead.")
            admin_role = Role.objects.get(is_admin=True)
            user = User.objects.create_user(email=email, password=password, full_name=name, role=admin_role)
            fs = FirmSettings.load()
            fs.firm_name = firm
            fs.workspace_kind = kind
            fs.setup_completed_at = timezone.now()
            fs.save()
        login(request, user)
        return Response(me_payload(user), status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- auth


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        email = str(request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        user = authenticate(request, username=email, password=password)
        if not user or not user.is_active:
            raise ValidationError({"detail": "That email and password don't match."})
        login(request, user)
        return Response(me_payload(user))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(me_payload(request.user))

    def patch(self, request):
        """Own display preferences and name."""
        u = request.user
        d = request.data
        if "date_format" in d:
            if d["date_format"] not in dict(DATE_FORMATS):
                raise ValidationError({"date_format": "Pick a date format."})
            u.date_format = d["date_format"]
        if "number_grouping" in d:
            if d["number_grouping"] not in dict(NUMBER_GROUPINGS):
                raise ValidationError({"number_grouping": "Pick a number format."})
            u.number_grouping = d["number_grouping"]
        if "full_name" in d:
            name = str(d["full_name"] or "").strip()
            if not name:
                raise ValidationError({"full_name": "Enter your name."})
            u.full_name = name[:150]
        u.save()
        return Response(me_payload(u))


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]  # allowed while a password change is forced

    def post(self, request):
        u = request.user
        if not u.check_password(request.data.get("current_password") or ""):
            raise ValidationError({"current_password": "Your current password is wrong."})
        new = request.data.get("new_password") or ""
        if msg := _password_errors(new, u):
            raise ValidationError({"new_password": msg})
        if new == request.data.get("current_password"):
            raise ValidationError({"new_password": "Pick a password different from the current one."})
        u.set_password(new)
        u.must_change_password = False
        u.save()
        update_session_auth_hash(request, u)
        return Response(me_payload(u))


# ---------------------------------------------------------------- users


def user_payload(u: User) -> dict:
    return {
        "id": u.pk,
        "email": u.email,
        "full_name": u.full_name,
        "role": {"id": u.role_id, "name": u.role.name, "is_admin": u.role.is_admin},
        "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "date_joined": u.date_joined.isoformat(),
        "has_vault_pin": bool(u.vault_pin),
    }


def _guard_admin_changes(actor: User, target: User | None, new_role: Role | None):
    """People who can manage users but aren't admins can't create, edit or promote admins."""
    if actor.is_admin:
        return
    if (target and target.is_admin) or (new_role and new_role.is_admin):
        raise PermissionDenied("Only admins can manage admin accounts.")


def _active_admins_excluding(user: User) -> int:
    return User.objects.filter(role__is_admin=True, is_active=True).exclude(pk=user.pk).count()


class UserLookupView(APIView):
    """Names for user pickers (relationship owner etc.). Any signed-in user."""

    def get(self, request):
        return Response(
            [{"id": u.pk, "full_name": u.full_name, "is_active": u.is_active} for u in User.objects.all()]
        )


class UserListView(APIView):
    permission_classes = [flag_required("manage_users"), NotInSample]

    def get(self, request):
        return Response([user_payload(u) for u in User.objects.select_related("role")])

    def post(self, request):
        d = request.data
        errors = {}
        name = str(d.get("full_name") or "").strip()
        email = str(d.get("email") or "").strip().lower()
        if not name:
            errors["full_name"] = "Enter their name."
        try:
            validate_email(email)
            if User.objects.filter(email__iexact=email).exists():
                errors["email"] = "Someone already uses this email."
        except DjangoValidationError:
            errors["email"] = "Enter a valid email address."
        role = Role.objects.filter(pk=d.get("role")).first()
        if not role:
            errors["role"] = "Pick a role."
        temp = (d.get("temp_password") or "").strip() or _temp_password()
        if msg := _password_errors(temp):
            errors["temp_password"] = msg
        if errors:
            raise ValidationError(errors)
        _guard_admin_changes(request.user, None, role)
        u = User.objects.create_user(email=email, password=temp, full_name=name, role=role, must_change_password=True)
        return Response({**user_payload(u), "temp_password": temp}, status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [flag_required("manage_users"), NotInSample]

    def patch(self, request, pk):
        u = get_object_or_404(User.objects.select_related("role"), pk=pk)
        d = request.data
        new_role = None
        if "role" in d:
            new_role = Role.objects.filter(pk=d["role"]).first()
            if not new_role:
                raise ValidationError({"role": "Pick a role."})
        _guard_admin_changes(request.user, u, new_role)
        losing_admin = u.is_admin and (
            (new_role is not None and not new_role.is_admin) or ("is_active" in d and not d["is_active"])
        )
        if losing_admin and _active_admins_excluding(u) == 0:
            raise ValidationError({"detail": "Dealflow needs at least one active admin."})
        if "is_active" in d and not d["is_active"] and u.pk == request.user.pk:
            raise ValidationError({"detail": "You can't deactivate your own account."})
        if "full_name" in d:
            name = str(d["full_name"] or "").strip()
            if not name:
                raise ValidationError({"full_name": "Enter their name."})
            u.full_name = name[:150]
        if "email" in d:
            email = str(d["email"] or "").strip().lower()
            try:
                validate_email(email)
            except DjangoValidationError:
                raise ValidationError({"email": "Enter a valid email address."})
            if User.objects.filter(email__iexact=email).exclude(pk=u.pk).exists():
                raise ValidationError({"email": "Someone already uses this email."})
            u.email = email
        if new_role:
            u.role = new_role
        if "is_active" in d:
            u.is_active = bool(d["is_active"])
        u.save()
        if not u.is_active:
            _end_sessions(u)
        return Response(user_payload(u))


class UserResetPasswordView(APIView):
    """Admins reset forgotten passwords (no email in V1): a temporary password the
    person must change at next sign-in."""

    permission_classes = [flag_required("manage_users"), NotInSample]

    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        _guard_admin_changes(request.user, u, None)
        temp = (request.data.get("temp_password") or "").strip() or _temp_password()
        if msg := _password_errors(temp, u):
            raise ValidationError({"temp_password": msg})
        u.set_password(temp)
        u.must_change_password = True
        u.save()
        _end_sessions(u)
        return Response({"temp_password": temp})


def _end_sessions(user: User):
    from django.contrib.sessions.models import Session

    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        if str(s.get_decoded().get("_auth_user_id")) == str(user.pk):
            s.delete()


# ---------------------------------------------------------------- roles


def role_payload(r: Role) -> dict:
    return {
        "id": r.pk,
        "name": r.name,
        "is_admin": r.is_admin,
        "perms": r.module_perms(),
        "flags": r.flags(),
        "user_count": r.users.count(),
    }


class AdminRequired(IsActiveUser):
    message = "Only admins can change roles."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin


def _save_role(role: Role, d: dict):
    if "name" in d:
        name = " ".join(str(d["name"] or "").split())[:80]
        if not name:
            raise ValidationError({"name": "Enter a role name."})
        if Role.objects.filter(name__iexact=name).exclude(pk=role.pk).exists():
            raise ValidationError({"name": "A role with this name already exists."})
        role.name = name
    flags = d.get("flags") or {}
    for flag in ("manage_users", "manage_settings", "restore_archive"):
        if flag in flags:
            setattr(role, flag, bool(flags[flag]))
    role.save()
    perms = d.get("perms") or {}
    for module, _ in MODULES:
        if module not in perms:
            continue
        p = perms[module] or {}
        vals = {f"can_{a}": bool(p.get(a)) for a in ACTIONS}
        # Anything beyond viewing implies viewing.
        if vals["can_create"] or vals["can_edit"] or vals["can_delete"]:
            vals["can_view"] = True
        RoleModulePermission.objects.update_or_create(role=role, module=module, defaults=vals)


class RoleListView(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [flag_required("manage_users")()]
        return [AdminRequired(), NotInSample()]

    def get(self, request):
        return Response([role_payload(r) for r in Role.objects.prefetch_related("module_permissions")])

    @transaction.atomic
    def post(self, request):
        if not str(request.data.get("name") or "").strip():
            raise ValidationError({"name": "Enter a role name."})
        role = Role(name="__new__")
        role.name = f"__new__{secrets.token_hex(4)}"
        role.save()
        _save_role(role, request.data)
        return Response(role_payload(role), status=status.HTTP_201_CREATED)


class RoleDetailView(APIView):
    permission_classes = [AdminRequired, NotInSample]

    @transaction.atomic
    def patch(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        if role.is_admin:
            raise ValidationError({"detail": "The Admin role always has full access and can't be changed."})
        _save_role(role, request.data)
        return Response(role_payload(role))

    def delete(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        if role.is_admin:
            raise ValidationError({"detail": "The Admin role can't be deleted."})
        if role.users.exists():
            raise ValidationError({"detail": "Move the people in this role to another role first."})
        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- invites


def invite_payload(i: Invite) -> dict:
    return {
        "id": i.pk,
        "token": i.token,
        "role": {"id": i.role_id, "name": i.role.name},
        "note": i.note,
        "created_by": i.created_by.full_name if i.created_by else None,
        "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat(),
        "used_at": i.used_at.isoformat() if i.used_at else None,
        "used_by": i.used_by.full_name if i.used_by else None,
        "revoked": i.revoked,
        "usable": i.is_usable,
    }


class InviteListView(APIView):
    permission_classes = [flag_required("manage_users"), NotInSample]

    def get(self, request):
        qs = Invite.objects.select_related("role", "created_by", "used_by")[:100]
        return Response([invite_payload(i) for i in qs])

    def post(self, request):
        role = Role.objects.filter(pk=request.data.get("role")).first()
        if not role:
            raise ValidationError({"role": "Pick a role."})
        _guard_admin_changes(request.user, None, role)
        i = Invite.objects.create(
            role=role, note=str(request.data.get("note") or "").strip()[:150], created_by=request.user
        )
        return Response(invite_payload(i), status=status.HTTP_201_CREATED)


class InviteRevokeView(APIView):
    permission_classes = [flag_required("manage_users"), NotInSample]

    def delete(self, request, pk):
        i = get_object_or_404(Invite, pk=pk)
        i.revoked = True
        i.save(update_fields=["revoked"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteAcceptView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def _invite(self, token):
        i = Invite.objects.select_related("role").filter(token=token).first()
        if not i or not i.is_usable:
            raise ValidationError({"detail": "This invite link has expired or was already used. Ask your admin for a new one."})
        return i

    def get(self, request, token):
        i = self._invite(token)
        return Response({"firm_name": FirmSettings.load().firm_name, "role": i.role.name, "note": i.note})

    def post(self, request, token):
        d = request.data
        errors = {}
        name = str(d.get("full_name") or "").strip()
        email = str(d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        if not name:
            errors["full_name"] = "Enter your name."
        try:
            validate_email(email)
            if User.objects.filter(email__iexact=email).exists():
                errors["email"] = "An account with this email already exists. Sign in instead."
        except DjangoValidationError:
            errors["email"] = "Enter a valid email address."
        if msg := _password_errors(password):
            errors["password"] = msg
        if errors:
            raise ValidationError(errors)
        with transaction.atomic():
            i = Invite.objects.select_for_update().select_related("role").filter(token=token).first()
            if not i or not i.is_usable:
                raise ValidationError({"detail": "This invite link has expired or was already used."})
            u = User.objects.create_user(email=email, password=password, full_name=name, role=i.role)
            i.used_at = timezone.now()
            i.used_by = u
            i.save()
        login(request, u)
        return Response(me_payload(u), status=status.HTTP_201_CREATED)
