from django.urls import path

from . import views

app_name = "staff"

urlpatterns = [
    path("", views.staff_list, name="list"),
    path("new/", views.staff_create, name="create"),
    path("<int:pk>/active/", views.staff_toggle_active, name="toggle_active"),
]
