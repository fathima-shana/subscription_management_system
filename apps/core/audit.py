from .models import AuditLog


def record(*, actor, action: str, entity=None, details: dict | None = None) -> AuditLog:
    """Write one audit entry. Never put passwords or tokens in `details`."""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        entity_type=entity._meta.label_lower if entity is not None else "",
        entity_id=str(entity.pk) if entity is not None else "",
        details=details or {},
    )
