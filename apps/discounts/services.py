"""Business operations for discounts. Admin-only changes; every change is audited."""

from django.db import IntegrityError, transaction
from django.db.models import F, Q

from apps.core import audit
from apps.core.errors import DuplicateError
from apps.core.permissions import ensure_admin

from .models import AppliesTo, Discount


def _target_ids(discount):
    return sorted(discount.products.values_list("pk", flat=True))


@transaction.atomic
def create_discount(*, actor, products=(), **fields) -> Discount:
    ensure_admin(actor)
    try:
        with transaction.atomic():
            discount = Discount.objects.create(**fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    if discount.applies_to == AppliesTo.PRODUCT:
        discount.products.set(products)
    audit.record(
        actor=actor,
        action="discount.created",
        entity=discount,
        details={
            "name": discount.name,
            "type": discount.discount_type,
            "value": str(discount.value),
            "applies_to": discount.applies_to,
            "products": _target_ids(discount),
        },
    )
    return discount


@transaction.atomic
def update_discount(*, actor, discount: Discount, products=(), **fields) -> Discount:
    ensure_admin(actor)
    before = _target_ids(discount)
    try:
        with transaction.atomic():
            changed = audit.apply_changes(discount, fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    # Targets only matter for product-scoped discounts; clear them otherwise.
    discount.products.set(products if discount.applies_to == AppliesTo.PRODUCT else [])
    after = _target_ids(discount)
    if before != after:
        changed["products"] = [before, after]
    if changed:
        audit.record(
            actor=actor, action="discount.updated", entity=discount, details={"changed": changed}
        )
    return discount


@transaction.atomic
def set_discount_active(*, actor, discount: Discount, active: bool) -> Discount:
    ensure_admin(actor)
    if discount.is_active != active:
        discount.is_active = active
        discount.save(update_fields=["is_active"])
        audit.record(
            actor=actor,
            action="discount.activated" if active else "discount.deactivated",
            entity=discount,
            details={"name": discount.name},
        )
    return discount


def redeem(discount_id: int) -> bool:
    """Use up one redemption. Returns False (and changes nothing) if the limit is reached.

    One UPDATE with the limit check inside the WHERE clause, so two invoices created at the
    same moment can never both take the last slot. Called by the billing engine inside the
    same transaction that creates the invoice.
    """
    updated = (
        Discount.objects.filter(pk=discount_id)
        .filter(Q(usage_limit__isnull=True) | Q(used_count__lt=F("usage_limit")))
        .update(used_count=F("used_count") + 1)
    )
    return updated == 1
