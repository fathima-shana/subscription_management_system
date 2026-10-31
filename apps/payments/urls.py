from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_list, name="list"),
    path("record/<int:invoice_pk>/", views.payment_create, name="create"),
    path("<int:pk>/void/", views.payment_void, name="void"),
]
