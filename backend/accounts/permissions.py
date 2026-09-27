from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class IsActiveUser(BasePermission):
    """Default for every API view: signed in, active, and not waiting on a forced
    password change."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        if user.must_change_password:
            raise PermissionDenied(
                detail={"detail": "Change your password to continue.", "code": "password_change_required"}
            )
        return True


def flag_required(flag: str):
    class HasFlag(IsActiveUser):
        message = "You don't have permission to do this."

        def has_permission(self, request, view):
            return super().has_permission(request, view) and request.user.has_flag(flag)

    HasFlag.__name__ = f"Has_{flag}"
    return HasFlag


class ModulePermission(IsActiveUser):
    """Checks the role's per-module permission for the view's `module_key`.
    The view maps each action to view/create/edit/delete via `action_perms`."""

    message = "Your role doesn't allow this."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        needed = view.action_perms.get(view.action, "view")
        return request.user.can(view.module_key, needed)
