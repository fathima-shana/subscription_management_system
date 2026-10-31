"""Business operations for taxes. Admin-only; every change is audited."""

from django.db import IntegrityError, transaction

from apps.core import audit
from apps.core.errors import DuplicateError
from apps.core.permissions import ensure_admin

from .models import Tax


@transaction.atomic
def create_tax(*, actor, **fields) -> Tax:
    ensure_admin(actor)
    try:
        with transaction.atomic():
            tax = Tax.objects.create(**fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    audit.record(
        actor=actor,
        action="tax.created",
        entity=tax,
        details={"name": tax.name, "type": tax.tax_type, "rate": str(tax.rate)},
    )
    return tax


@transaction.atomic
def update_tax(*, actor, tax: Tax, **fields) -> Tax:
    ensure_admin(actor)
    try:
        with transaction.atomic():
            changed = audit.apply_changes(tax, fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    if changed:
        audit.record(actor=actor, action="tax.updated", entity=tax, details={"changed": changed})
    return tax


@transaction.atomic
def set_tax_active(*, actor, tax: Tax, active: bool) -> Tax:
    ensure_admin(actor)
    if tax.is_active != active:
        tax.is_active = active
        tax.save(update_fields=["is_active"])
        audit.record(
            actor=actor,
            action="tax.activated" if active else "tax.deactivated",
            entity=tax,
            details={"name": tax.name},
        )
    return tax
