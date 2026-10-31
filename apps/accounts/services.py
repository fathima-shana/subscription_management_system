"""Business operations for accounts. Views call these; they never touch models directly."""
from django.db import IntegrityError, transaction

from .models import CustomerProfile, Role, User


class DuplicateEmailError(Exception):
    """Raised when an account already exists for the email (case-insensitive)."""


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
