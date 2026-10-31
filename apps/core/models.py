from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only record of sensitive actions (who did what to which record)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    action = models.CharField(max_length=60)  # e.g. "staff.created"
    entity_type = models.CharField(max_length=80, blank=True)  # e.g. "accounts.user"
    entity_id = models.CharField(max_length=40, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx"),
            models.Index(fields=["action", "-created_at"], name="audit_action_idx"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}#{self.entity_id}"


class NumberSequence(models.Model):
    """Gap-free-per-key counter for human-readable numbers such as SUB-00001 and INV-00001."""

    key = models.CharField(max_length=20, unique=True)
    next_value = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.key} -> {self.next_value}"
