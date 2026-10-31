from django.urls import path

from . import views

app_name = "qtemplates"

urlpatterns = [
    path("", views.template_list, name="list"),
    path("new/", views.template_create, name="create"),
    path("<int:pk>/", views.template_detail, name="detail"),
    path("<int:pk>/edit/", views.template_edit, name="edit"),
    path("<int:pk>/active/", views.template_toggle_active, name="toggle_active"),
    path("<int:pk>/lines/new/", views.template_line_create, name="line_create"),
    path("lines/<int:pk>/delete/", views.template_line_delete, name="line_delete"),
]
