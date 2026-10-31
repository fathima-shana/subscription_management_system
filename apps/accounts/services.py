"""Business operations for accounts. Views call these; they never touch models directly.

Every privileged operation re-checks the actor's role here, so the rule holds even if a
view forgets its decorator (defence in depth).
"""
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from apps.core import audit

from .models import CustomerProfile, Role, User


class DuplicateEmailError(Exception):
    """Raised when an account already exists for the email (case-insensitive)."""


def _require_admin(actor) -> None:
    if not getattr(actor, "is_authenticated", False) or actor.role != Role.ADMIN:
        raise PermissionDenied("Only an admin can do this.")


@transaction.atomic
def register_customer(*, email: str, password: str, full_name: str, company_name: str = "") -> User:
    """Create a customer account and its billing profile in one transaction."""
    try:
        with transaction.atomic():  # savepoint, so the outer request transaction survives
            user = User.objects.create_user(
                email=email, password=password, full_name=full_name.strip(), role=Role.CUSTOMER
            )
    except IntegrityError as exc:  # lost a race against the unique index
        raise DuplicateEmailError(email) from exc
    CustomerProfile.objects.create(user=user, company_name=company_name.strip())
    return user


@transaction.atomic
def create_staff(*, actor, email: str, password: str, full_name: str) -> User:
    """Admin-only. Always creates role=staff; the role cannot be chosen by the caller."""
    _require_admin(actor)
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email, password=password, full_name=full_name.strip(), role=Role.STAFF
            )
    except IntegrityError as exc:
        raise DuplicateEmailError(email) from exc
    audit.record(actor=actor, action="staff.created", entity=user, details={"email": user.email})
    return user


@transaction.atomic
def set_staff_active(*, actor, user: User, active: bool) -> User:
    """Admin-only. Activate or deactivate a staff account (never an admin or customer)."""
    _require_admin(actor)
    if user.role != Role.STAFF:
        raise PermissionDenied("Only staff accounts can be changed here.")
    if user.is_active != active:
        user.is_active = active
        user.save(update_fields=["is_active"])
        audit.record(
            actor=actor,
            action="staff.activated" if active else "staff.deactivated",
            entity=user,
            details={"email": user.email},
        )
    return user
