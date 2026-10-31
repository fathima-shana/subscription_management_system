from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "catalog"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="catalog:product_list", permanent=False)),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/active/", views.product_toggle_active, name="product_toggle_active"),
    path("products/<int:pk>/variants/new/", views.variant_create, name="variant_create"),
    path("variants/<int:pk>/edit/", views.variant_edit, name="variant_edit"),
    path("variants/<int:pk>/active/", views.variant_toggle_active, name="variant_toggle_active"),
    path("plans/", views.plan_list, name="plan_list"),
    path("plans/new/", views.plan_create, name="plan_create"),
    path("plans/<int:pk>/edit/", views.plan_edit, name="plan_edit"),
    path("plans/<int:pk>/active/", views.plan_toggle_active, name="plan_toggle_active"),
]
