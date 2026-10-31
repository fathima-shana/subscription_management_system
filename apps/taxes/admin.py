from django.contrib import admin

from .models import Tax


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = ("name", "tax_type", "rate", "is_active")
    list_filter = ("tax_type", "is_active")
    search_fields = ("name",)
