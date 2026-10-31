from decimal import Decimal

from .models import AuditLog
from .money import quantize


def record(*, actor, action: str, entity=None, details: dict | None = None) -> AuditLog:
    """Write one audit entry. Never put passwords or tokens in `details`."""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        entity_type=entity._meta.label_lower if entity is not None else "",
        entity_id=str(entity.pk) if entity is not None else "",
        details=details or {},
    )


def _jsonable(value):
    if isinstance(value, Decimal):  # every Decimal in this project is a 2-decimal amount or rate
        return str(quantize(value))
    return value if value is None or isinstance(value, (bool, int)) else str(value)


def apply_changes(instance, values: dict) -> dict:
    """Set `values` on `instance`, save only what changed, return {field: [old, new]}.

    The returned dict goes straight into an audit entry as the before/after record.
    """
    changed = {}
    for field, new in values.items():
        old = getattr(instance, field)
        if old != new:
            changed[field] = [_jsonable(old), _jsonable(new)]
            setattr(instance, field, new)
    if changed:
        instance.save(update_fields=list(changed))
    return changed
