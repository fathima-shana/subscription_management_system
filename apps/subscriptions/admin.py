from django.contrib import admin

from .models import (
    PaymentTerm,
    QuotationTemplate,
    QuotationTemplateLine,
    Subscription,
    SubscriptionLine,
    SubscriptionStatusHistory,
)


@admin.register(PaymentTerm)
class PaymentTermAdmin(admin.ModelAdmin):
    list_display = ("name", "days")


class TemplateLineInline(admin.TabularInline):
    model = QuotationTemplateLine
    extra = 0


@admin.register(QuotationTemplate)
class QuotationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "plan", "validity_days", "is_active")
    inlines = [TemplateLineInline]


class LineInline(admin.TabularInline):
    model = SubscriptionLine
    extra = 0


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "plan", "status", "start_date", "next_billing_date")
    list_filter = ("status", "plan")
    search_fields = ("number", "customer__user__email", "customer__company_name")
    readonly_fields = ("number", "status")
    inlines = [LineInline]


@admin.register(SubscriptionStatusHistory)
class HistoryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "from_status", "to_status", "changed_by", "changed_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
