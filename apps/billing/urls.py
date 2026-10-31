from django.urls import path

from . import views

app_name = "invoices"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("<int:pk>/", views.invoice_detail, name="detail"),
    path("<int:pk>/print/", views.invoice_print, name="print"),
    path("<int:pk>/send/", views.invoice_send, name="send"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="cancel"),
]
