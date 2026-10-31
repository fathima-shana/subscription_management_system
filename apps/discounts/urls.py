from django.urls import path

from . import views

app_name = "discounts"

urlpatterns = [
    path("", views.discount_list, name="list"),
    path("new/", views.discount_create, name="create"),
    path("<int:pk>/edit/", views.discount_edit, name="edit"),
    path("<int:pk>/active/", views.discount_toggle_active, name="toggle_active"),
]
