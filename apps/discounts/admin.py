from django.contrib import admin

from .models import Discount


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "discount_type",
        "value",
        "applies_to",
        "used_count",
        "usage_limit",
        "is_active",
    )
    list_filter = ("discount_type", "applies_to", "is_active")
    search_fields = ("name",)
    readonly_fields = ("used_count",)
