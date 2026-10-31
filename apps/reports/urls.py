from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report, name="report"),
    path("export/<str:kind>/", views.export, name="export"),
]
