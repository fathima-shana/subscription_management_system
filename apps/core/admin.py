from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: an audit trail that can be edited is not an audit trail."""

    list_display = ("created_at", "actor", "action", "entity_type", "entity_id")
    list_filter = ("action",)
    search_fields = ("actor__email", "entity_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
