from django.contrib import admin

from .models import Plan, Product, ProductVariant


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "product_type", "sales_price", "cost_price", "is_active")
    list_filter = ("product_type", "is_active")
    search_fields = ("name",)
    inlines = [VariantInline]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "billing_period", "min_quantity", "start_date", "end_date", "is_active")
    list_filter = ("billing_period", "is_active")
    search_fields = ("name",)
