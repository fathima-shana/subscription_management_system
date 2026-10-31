from django.db.models import Q

from apps.core.scoping import scope_to_customer

from .models import Invoice, InvoiceStatus

FILTERS = ["draft", "confirmed", "overdue", "paid", "cancelled"]


def invoice_queryset(user, today, q: str = "", status: str = ""):
    qs = scope_to_customer(Invoice.objects.all(), user, lookup="customer__user").select_related(
        "subscription", "customer__user"
    )
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(subscription__number__icontains=q)
            | Q(bill_to_name__icontains=q)
            | Q(bill_to_email__icontains=q)
        )
    if status == "overdue":  # derived, never stored
        qs = qs.filter(status=InvoiceStatus.CONFIRMED, due_date__lt=today)
    elif status in InvoiceStatus.values:
        qs = qs.filter(status=status)
    return qs.order_by("-id")


def get_invoice(user, pk):
    return (
        scope_to_customer(Invoice.objects.all(), user, lookup="customer__user")
        .select_related("subscription", "customer__user")
        .prefetch_related("lines")
        .filter(pk=pk)
        .first()
    )
