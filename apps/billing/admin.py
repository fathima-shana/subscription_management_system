from django.contrib import admin

from .models import Invoice, InvoiceLine


class LineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in InvoiceLine._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Read-only: issued invoices are changed only through the app (cancel, payments)."""

    list_display = (
        "number",
        "subscription",
        "bill_to_name",
        "status",
        "total",
        "amount_paid",
        "due_date",
    )
    list_filter = ("status",)
    search_fields = ("number", "bill_to_name", "subscription__number")
    inlines = [LineInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Invoice._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
