from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.subscription_list, name="list"),
    path("new/", views.subscription_create, name="create"),
    path("<int:pk>/", views.subscription_detail, name="detail"),
    path("<int:pk>/edit/", views.subscription_edit, name="edit"),
    path("<int:pk>/transition/", views.subscription_transition, name="transition"),
    path("<int:pk>/renew/", views.subscription_renew, name="renew"),
    path("<int:pk>/lines/new/", views.line_create, name="line_create"),
    path("lines/<int:pk>/delete/", views.line_delete, name="line_delete"),
]
