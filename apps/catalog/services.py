"""Business operations for the catalog. Admin-only; every change is audited."""

from django.db import IntegrityError, transaction

from apps.core import audit
from apps.core.errors import DuplicateError
from apps.core.permissions import ensure_admin

from .models import Plan, Product, ProductVariant


def _create(model, label_field, **fields):
    try:
        with transaction.atomic():  # savepoint: a duplicate must not poison the request transaction
            obj = model.objects.create(**fields)
    except IntegrityError as exc:
        raise DuplicateError(str(fields.get(label_field))) from exc
    return obj


def _update(actor, obj, fields, action, extra=None):
    try:
        with transaction.atomic():
            changed = audit.apply_changes(obj, fields)
    except IntegrityError as exc:
        raise DuplicateError(str(obj)) from exc
    if changed:
        audit.record(
            actor=actor, action=action, entity=obj, details={"changed": changed, **(extra or {})}
        )
    return obj


def _toggle(actor, obj, active, prefix, details):
    if obj.is_active != active:
        obj.is_active = active
        obj.save(update_fields=["is_active"])
        audit.record(
            actor=actor,
            action=f"{prefix}.{'activated' if active else 'deactivated'}",
            entity=obj,
            details=details,
        )
    return obj


# --- Products ------------------------------------------------------------------------


@transaction.atomic
def create_product(*, actor, **fields) -> Product:
    ensure_admin(actor)
    product = _create(Product, "name", **fields)
    audit.record(
        actor=actor,
        action="product.created",
        entity=product,
        details={"name": product.name, "sales_price": str(product.sales_price)},
    )
    return product


@transaction.atomic
def update_product(*, actor, product: Product, **fields) -> Product:
    ensure_admin(actor)
    return _update(actor, product, fields, "product.updated")


@transaction.atomic
def set_product_active(*, actor, product: Product, active: bool) -> Product:
    ensure_admin(actor)
    return _toggle(actor, product, active, "product", {"name": product.name})


# --- Variants ------------------------------------------------------------------------


@transaction.atomic
def add_variant(*, actor, product: Product, **fields) -> ProductVariant:
    ensure_admin(actor)
    variant = _create(ProductVariant, "value", product=product, **fields)
    audit.record(
        actor=actor,
        action="variant.created",
        entity=variant,
        details={
            "product": product.name,
            "option": variant.label,
            "extra_price": str(variant.extra_price),
        },
    )
    return variant


@transaction.atomic
def update_variant(*, actor, variant: ProductVariant, **fields) -> ProductVariant:
    ensure_admin(actor)
    return _update(actor, variant, fields, "variant.updated", {"product": variant.product.name})


@transaction.atomic
def set_variant_active(*, actor, variant: ProductVariant, active: bool) -> ProductVariant:
    ensure_admin(actor)
    return _toggle(
        actor,
        variant,
        active,
        "variant",
        {"product": variant.product.name, "option": variant.label},
    )


# --- Plans ---------------------------------------------------------------------------


@transaction.atomic
def create_plan(*, actor, **fields) -> Plan:
    ensure_admin(actor)
    plan = _create(Plan, "name", **fields)
    audit.record(
        actor=actor,
        action="plan.created",
        entity=plan,
        details={"name": plan.name, "billing_period": plan.billing_period},
    )
    return plan


@transaction.atomic
def update_plan(*, actor, plan: Plan, **fields) -> Plan:
    ensure_admin(actor)
    return _update(actor, plan, fields, "plan.updated")


@transaction.atomic
def set_plan_active(*, actor, plan: Plan, active: bool) -> Plan:
    ensure_admin(actor)
    return _toggle(actor, plan, active, "plan", {"name": plan.name})
