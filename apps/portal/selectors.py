"""What a customer may see. Everything goes through the shared scoping helpers, so a customer
can only ever reach rows that belong to their own account, and never internal drafts."""

from django.db.models import Min

from apps.billing import selectors as billing_selectors
from apps.billing.models import InvoiceStatus
from apps.payments import selectors as payment_selectors
from apps.subscriptions import selectors as subscription_selectors
from apps.subscriptions.models import Status

INVOICE_FILTERS = [("confirmed", "Unpaid"), ("overdue", "Overdue"), ("paid", "Paid")]


def subscriptions(user, q="", status=""):
    """Own subscriptions, except drafts (those are internal work in progress)."""
    return subscription_selectors.subscription_queryset(user, q, status).exclude(
        status=Status.DRAFT
    )


def get_subscription(user, pk):
    sub = subscription_selectors.get_subscription(user, pk)
    return None if sub is None or sub.status == Status.DRAFT else sub


def invoices(user, today, q="", status=""):
    return billing_selectors.invoice_queryset(user, today, q, status).exclude(
        status=InvoiceStatus.DRAFT
    )


def get_invoice(user, pk):
    invoice = billing_selectors.get_invoice(user, pk)
    return None if invoice is None or invoice.status == InvoiceStatus.DRAFT else invoice


def live_payments(invoice):
    return (
        invoice.payments.filter(voided_at__isnull=True)
        .select_related("created_by")
        .order_by("payment_date", "id")
    )


def dashboard(user, today):
    subs = subscriptions(user)
    invoice_qs = invoices(user, today)
    open_qs = invoice_qs.filter(status=InvoiceStatus.CONFIRMED)
    return {
        "active_count": subs.filter(status=Status.ACTIVE).count(),
        "next_billing": subs.filter(status=Status.ACTIVE).aggregate(d=Min("next_billing_date"))[
            "d"
        ],
        "outstanding": payment_selectors.balance_totals(open_qs),
        "overdue": payment_selectors.balance_totals(open_qs.filter(due_date__lt=today)),
        "recent_subscriptions": list(subs[:5]),
        "recent_invoices": list(invoice_qs[:5]),
    }
