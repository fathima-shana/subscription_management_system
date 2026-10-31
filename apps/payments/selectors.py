"""Read queries: payment lists and the outstanding / overdue figures."""

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce

from apps.billing.models import Invoice, InvoiceStatus
from apps.core.money import ZERO
from apps.core.scoping import scope_to_customer

from .models import Method, Payment

_BALANCE = ExpressionWrapper(
    F("total") - F("amount_paid"), output_field=DecimalField(max_digits=16, decimal_places=2)
)


def payment_queryset(user, q: str = "", method: str = ""):
    qs = scope_to_customer(
        Payment.objects.all(), user, lookup="invoice__customer__user"
    ).select_related("invoice", "created_by", "voided_by")
    if q:
        qs = qs.filter(
            Q(invoice__number__icontains=q)
            | Q(invoice__bill_to_name__icontains=q)
            | Q(note__icontains=q)
            | Q(invoice__subscription__number__icontains=q)
        )
    if method in Method.values:
        qs = qs.filter(method=method)
    return qs.order_by("-payment_date", "-id")


def open_invoices():
    """Invoices that still have money owing: confirmed and not fully paid."""
    return Invoice.objects.filter(status=InvoiceStatus.CONFIRMED)


def overdue_invoices(today):
    """Past their due date and still owing. Derived on the fly, never stored."""
    return open_invoices().filter(due_date__lt=today)


def _sum_balance(qs):
    row = qs.aggregate(amount=Coalesce(Sum(_BALANCE), ZERO), count=Count("id"))
    return {"amount": row["amount"], "count": row["count"]}


def balance_totals(qs):
    """{'amount': total still owing, 'count': invoices} for any Invoice queryset."""
    return _sum_balance(qs)


def outstanding_totals(today):
    """{'outstanding': {amount, count}, 'overdue': {amount, count}}"""
    return {
        "outstanding": _sum_balance(open_invoices()),
        "overdue": _sum_balance(overdue_invoices(today)),
    }


def collected(start=None, end=None):
    """Money received (live payments only) between two dates, inclusive."""
    qs = Payment.objects.filter(voided_at__isnull=True)
    if start:
        qs = qs.filter(payment_date__gte=start)
    if end:
        qs = qs.filter(payment_date__lte=end)
    return qs.aggregate(s=Coalesce(Sum("amount"), ZERO))["s"]
