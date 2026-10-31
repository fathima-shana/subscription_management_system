"""Shared helpers for tests in every module."""

from apps.accounts.models import CustomerProfile, Role, User

PW = "Str0ng!Pass"


def make_user(email, role, name=None, **extra):
    user = User.objects.create_user(
        email, PW, full_name=name or email.split("@")[0], role=role, **extra
    )
    if role == Role.CUSTOMER:
        CustomerProfile.objects.create(user=user, company_name=f"{user.full_name} Co")
    return user


def make_people():
    """Return (admin, staff, customer) users."""
    return (
        make_user("admin@x.com", Role.ADMIN, "Ada Admin"),
        make_user("staff@x.com", Role.STAFF, "Sam Staff"),
        make_user("cust@x.com", Role.CUSTOMER, "Cara Customer"),
    )
