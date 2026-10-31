"""Role-based access control.

Roles are compared as plain strings so `core` never imports from `accounts`.
Anonymous users are sent to the login page; signed-in users with the wrong
role get a 403 page. Deactivated users are treated as signed out by Django
itself (ModelBackend.get_user), so a deactivation takes effect immediately.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(*roles: str):
    allowed = frozenset(str(r) for r in roles)

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user.role not in allowed:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def ensure_admin(actor) -> None:
    """Service-layer guard: raise PermissionDenied unless `actor` is an active admin.

    Views are already decorated, but services re-check so the rule survives a forgotten decorator.
    """
    if not getattr(actor, "is_authenticated", False) or getattr(actor, "role", None) != "admin":
        raise PermissionDenied("Only an admin can do this.")
