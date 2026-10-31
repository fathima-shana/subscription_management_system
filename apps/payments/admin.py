from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments are recorded and voided through the app so invoice totals stay correct."""

    list_display = ("invoice", "method", "amount", "payment_date", "created_by", "voided_at")
    list_filter = ("method",)
    search_fields = ("invoice__number", "note")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
