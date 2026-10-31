"""CSV exports. Rows are streamed in chunks so a large export does not load everything into memory.

Cells that start with = + - @ are prefixed with a quote so spreadsheet software never runs them
as formulas (CSV injection). Numbers are written as plain numbers.
"""

import csv

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce

from apps.billing.models import Invoice
from apps.payments.models import Payment
from apps.subscriptions.models import Subscription

from . import metrics

DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe(value):
    if isinstance(value, str) and value.startswith(DANGEROUS):
        return "'" + value
    return "" if value is None else value


class _Echo:
    def write(self, value):
        return value


def stream(header, rows):
    writer = csv.writer(_Echo())
    yield "\ufeff"  # lets Excel open the file as UTF-8
    yield writer.writerow(header)
    for row in rows:
        yield writer.writerow([safe(cell) for cell in row])


def _invoices(filters, today):
    qs = Invoice.objects.select_related("subscription").order_by("id")
    qs = metrics._by_date(
        metrics._plan(qs, filters.plan_id, "subscription__plan_id"),
        "issue_date",
        filters.start,
        filters.end,
    )
    header = [
        "Invoice",
        "Subscription",
        "Customer",
        "Email",
        "Plan",
        "Status",
        "Issue date",
        "Due date",
        "Period start",
        "Period end",
        "Subtotal",
        "Discount",
        "Tax",
        "Total",
        "Paid",
        "Balance",
    ]
    rows = (
        [
            i.number,
            i.subscription.number,
            i.bill_to_name,
            i.bill_to_email,
            i.plan_name,
            i.display_status(today),
            i.issue_date,
            i.due_date,
            i.period_start,
            i.period_end,
            i.subtotal,
            i.discount_total,
            i.tax_total,
            i.total,
            i.amount_paid,
            i.balance,
        ]
        for i in qs.iterator(chunk_size=2000)
    )
    return header, rows


def _payments(filters, today):
    qs = Payment.objects.select_related("invoice", "created_by").order_by("id")
    qs = metrics._by_date(
        metrics._plan(qs, filters.plan_id, "invoice__subscription__plan_id"),
        "payment_date",
        filters.start,
        filters.end,
    )
    header = [
        "Date",
        "Invoice",
        "Customer",
        "Method",
        "Amount",
        "Reference",
        "Recorded by",
        "Voided",
    ]
    rows = (
        [
            p.payment_date,
            p.invoice.number,
            p.invoice.bill_to_name,
            p.get_method_display(),
            p.amount,
            p.note,
            p.created_by.full_name if p.created_by else "",
            "yes" if p.is_voided else "",
        ]
        for p in qs.iterator(chunk_size=2000)
    )
    return header, rows


def _subscriptions(filters, today):
    amount = ExpressionWrapper(
        F("lines__quantity") * F("lines__unit_price"),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )
    qs = (
        Subscription.objects.select_related("customer__user", "plan", "payment_term")
        .annotate(
            subtotal=Coalesce(
                Sum(amount), Value(0), output_field=DecimalField(max_digits=16, decimal_places=2)
            )
        )
        .order_by("id")
    )
    qs = metrics._by_date(
        metrics._plan(qs, filters.plan_id, "plan_id"), "start_date", filters.start, filters.end
    )
    header = [
        "Subscription",
        "Customer",
        "Email",
        "Plan",
        "Billing period",
        "Status",
        "Start date",
        "Expiry date",
        "Next billing date",
        "Payment term",
        "Recurring subtotal",
    ]
    rows = (
        [
            s.number,
            s.customer.company_name or s.customer.user.full_name,
            s.customer.user.email,
            s.plan.name,
            s.plan.get_billing_period_display(),
            s.get_status_display(),
            s.start_date,
            s.expiry_date,
            s.next_billing_date,
            s.payment_term.name,
            s.subtotal,
        ]
        for s in qs.iterator(chunk_size=2000)
    )
    return header, rows


def _overdue(filters, today):
    qs = (
        metrics.overdue_invoices(today, filters.plan_id)
        .select_related("subscription")
        .order_by("due_date", "id")
    )
    header = [
        "Invoice",
        "Subscription",
        "Customer",
        "Email",
        "Due date",
        "Days overdue",
        "Total",
        "Paid",
        "Balance",
    ]
    rows = (
        [
            i.number,
            i.subscription.number,
            i.bill_to_name,
            i.bill_to_email,
            i.due_date,
            (today - i.due_date).days,
            i.total,
            i.amount_paid,
            i.balance,
        ]
        for i in qs.iterator(chunk_size=2000)
    )
    return header, rows


KINDS = {
    "invoices": _invoices,
    "payments": _payments,
    "subscriptions": _subscriptions,
    "overdue": _overdue,
}


def build(kind, filters, today):
    header, rows = KINDS[kind](filters, today)
    return stream(header, rows)
