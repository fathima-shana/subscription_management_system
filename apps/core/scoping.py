"""Object-level access: which rows may this user see?

Internal users (admin/staff) see everything. A customer sees only rows that belong
to their own account. Every customer-facing queryset must pass through here, so the
rule lives in one tested place instead of being repeated in every view.
"""


def scope_to_customer(queryset, user, lookup: str = "user"):
    """Restrict `queryset` to what `user` may see.

    `lookup` is the ORM path from the model to the owning User, for example
    "user" for CustomerProfile or "customer__user" for Subscription.
    """
    if not user.is_authenticated or not user.is_active:
        return queryset.none()
    if user.role in ("admin", "staff"):
        return queryset
    if user.role == "customer":
        return queryset.filter(**{lookup: user})
    return queryset.none()
