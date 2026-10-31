"""Read queries for accounts. Views never build these querysets themselves."""
from django.db.models import Q

from .models import CustomerProfile, Role, User


def staff_queryset(q: str = ""):
    qs = User.objects.filter(role=Role.STAFF)
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q))
    return qs.order_by("full_name", "id")


def customer_queryset(q: str = ""):
    qs = CustomerProfile.objects.select_related("user").filter(user__role=Role.CUSTOMER)
    if q:
        qs = qs.filter(
            Q(user__full_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(company_name__icontains=q)
        )
    return qs.order_by("user__full_name", "id")
