from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("subscriptions/", views.subscription_list, name="subscriptions"),
    path("subscriptions/<int:pk>/", views.subscription_detail, name="subscription"),
    path("invoices/", views.invoice_list, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="invoice_print"),
]
