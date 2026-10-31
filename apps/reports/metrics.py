"""Report figures. Everything is computed with database aggregates (no row-by-row Python loops).

Definitions (also shown on the report page):
* MRR - monthly recurring revenue: the line amounts (quantity x unit price, before discounts and
  tax) of ACTIVE subscriptions, each normalised to a month: yearly / 12, weekly x 52/12,
  daily x 365/12.
* Invoiced - total of invoices issued in the range (cancelled and draft ones excluded).
* Collected - payments received in the range (voided payments excluded).
* Outstanding / overdue - balance still owing on confirmed invoices, as of today.
"""

import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce, TruncMonth

from apps.billing.models import Invoice, InvoiceStatus
from apps.billing.periods import add_months
from apps.core.money import ZERO, quantize
from apps.payments import selectors as payment_selectors
from apps.payments.models import Method, Payment
from apps.subscriptions.models import Status, Subscription, SubscriptionLine

MONTHLY_FACTOR = {
    "daily": Decimal(365) / 12,
    "weekly": Decimal(52) / 12,
    "monthly": Decimal(1),
    "yearly": Decimal(1) / 12,
}

LINE_AMOUNT = ExpressionWrapper(
    F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=16, decimal_places=2)
)


def _by_date(qs, field, start, end):
    if start:
        qs = qs.filter(**{f"{field}__gte": start})
    if end:
        qs = qs.filter(**{f"{field}__lte": end})
    return qs


def _plan(qs, plan_id, path):
    return qs.filter(**{path: plan_id}) if plan_id else qs


# --- Subscriptions ------------------------------------------------------------------------


def mrr_by_plan(plan_id=None):
    qs = _plan(
        SubscriptionLine.objects.filter(subscription__status=Status.ACTIVE),
        plan_id,
        "subscription__plan_id",
    )
    rows = (
        qs.values(
            "subscription__plan_id",
            "subscription__plan__name",
            "subscription__plan__billing_period",
        )
        .annotate(recurring=Sum(LINE_AMOUNT), subscriptions=Count("subscription_id", distinct=True))
        .order_by("subscription__plan__name")
    )
    result = []
    for row in rows:
        period = row["subscription__plan__billing_period"]
        result.append(
            {
                "plan_id": row["subscription__plan_id"],
                "name": row["subscription__plan__name"],
                "period": period,
                "subscriptions": row["subscriptions"],
                "recurring": quantize(row["recurring"]),
                "mrr": quantize(row["recurring"] * MONTHLY_FACTOR[period]),
            }
        )
    return sorted(result, key=lambda r: (-r["mrr"], r["name"]))


def mrr(rows):
    return sum((r["mrr"] for r in rows), ZERO)


def status_counts(plan_id=None):
    qs = _plan(Subscription.objects.all(), plan_id, "plan_id")
    counts = dict(qs.values_list("status").annotate(n=Count("id")))
    return [
        {"status": value, "label": label, "count": counts.get(value, 0)}
        for value, label in Status.choices
    ]


def new_subscriptions(start, end, plan_id=None):
    qs = _plan(Subscription.objects.all(), plan_id, "plan_id")
    return _by_date(qs, "created_at__date", start, end).count()


# --- Money ----------------------------------------------------------------------------------


def invoiced(start, end, plan_id=None):
    qs = Invoice.objects.exclude(status__in=[InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED])
    qs = _by_date(_plan(qs, plan_id, "subscription__plan_id"), "issue_date", start, end)
    row = qs.aggregate(amount=Coalesce(Sum("total"), ZERO), count=Count("id"))
    return {"amount": row["amount"], "count": row["count"]}


def _live_payments(start, end, plan_id):
    qs = Payment.objects.filter(voided_at__isnull=True)
    return _by_date(
        _plan(qs, plan_id, "invoice__subscription__plan_id"), "payment_date", start, end
    )


def collected(start, end, plan_id=None):
    return _live_payments(start, end, plan_id).aggregate(s=Coalesce(Sum("amount"), ZERO))["s"]


def outstanding(today, plan_id=None):
    base = _plan(payment_selectors.open_invoices(), plan_id, "subscription__plan_id")
    return {
        "outstanding": payment_selectors.balance_totals(base),
        "overdue": payment_selectors.balance_totals(base.filter(due_date__lt=today)),
    }


def overdue_invoices(today, plan_id=None):
    return _plan(payment_selectors.overdue_invoices(today), plan_id, "subscription__plan_id")


def overdue_list(today, plan_id=None, limit=10):
    qs = (
        overdue_invoices(today, plan_id)
        .select_related("subscription")
        .order_by("due_date", "id")[:limit]
    )
    return [{"invoice": i, "days": (today - i.due_date).days, "balance": i.balance} for i in qs]


def payments_by_method(start, end, plan_id=None):
    rows = (
        _live_payments(start, end, plan_id)
        .values("method")
        .annotate(total=Sum("amount"), count=Count("id"))
    )
    totals = {r["method"]: r for r in rows}
    grand = sum((r["total"] for r in totals.values()), ZERO)
    result = []
    for value, label in Method.choices:
        row = totals.get(value)
        amount = row["total"] if row else ZERO
        result.append(
            {
                "method": value,
                "label": label,
                "amount": amount,
                "count": row["count"] if row else 0,
                "pct": int(amount * 100 / grand) if grand else 0,
            }
        )
    return sorted(result, key=lambda r: -r["amount"])


def collected_by_month(end, months=6, plan_id=None):
    first = add_months(end.replace(day=1), -(months - 1))
    rows = (
        _live_payments(first, end, plan_id)
        .annotate(m=TruncMonth("payment_date"))
        .values("m")
        .annotate(total=Sum("amount"))
        .order_by("m")
    )
    totals = {r["m"]: r["total"] for r in rows}
    series = []
    for i in range(months):
        month = add_months(first, i)
        series.append({"month": month, "total": totals.get(month, ZERO)})
    peak = max((s["total"] for s in series), default=ZERO)
    for s in series:
        s["pct"] = max(int(s["total"] * 100 / peak), 3) if peak and s["total"] else 0
    return series


# --- Everything the pages need, in one place -----------------------------------------------


def build_report(filters, today):
    plan_id = filters.plan_id
    rows = mrr_by_plan(plan_id)
    owed = outstanding(today, plan_id)
    return {
        "mrr_rows": rows,
        "mrr": mrr(rows),
        "active": sum(r["subscriptions"] for r in rows),
        "status_counts": status_counts(plan_id),
        "new_subscriptions": new_subscriptions(filters.start, filters.end, plan_id),
        "invoiced": invoiced(filters.start, filters.end, plan_id),
        "collected": collected(filters.start, filters.end, plan_id),
        "outstanding": owed["outstanding"],
        "overdue": owed["overdue"],
        "by_method": payments_by_method(filters.start, filters.end, plan_id),
        "by_month": collected_by_month(today, 6, plan_id),
        "overdue_list": overdue_list(today, plan_id),
    }
