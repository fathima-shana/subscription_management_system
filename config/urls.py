from django.contrib import admin
from django.urls import include, path

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("staff/", include("apps.accounts.urls_staff")),
    path("customers/", include("apps.accounts.urls_customers")),
    path("catalog/", include("apps.catalog.urls")),
    path("taxes/", include("apps.taxes.urls")),
    path("discounts/", include("apps.discounts.urls")),
    path("subscriptions/", include("apps.subscriptions.urls")),
    path("quotation-templates/", include("apps.subscriptions.urls_templates")),
    path("", include("apps.core.urls")),
]
