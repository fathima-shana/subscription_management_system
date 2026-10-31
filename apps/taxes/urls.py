from django.urls import path

from . import views

app_name = "taxes"

urlpatterns = [
    path("", views.tax_list, name="list"),
    path("new/", views.tax_create, name="create"),
    path("preview/", views.tax_preview, name="preview"),
    path("<int:pk>/edit/", views.tax_edit, name="edit"),
    path("<int:pk>/active/", views.tax_toggle_active, name="toggle_active"),
]
