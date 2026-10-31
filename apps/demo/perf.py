"""Page timings and query plans on a loaded database (used by the perf_check command)."""

import datetime
import statistics
import time
from dataclasses import dataclass

from django.db import connection, transaction
from django.db.models import Count
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.billing import engine
from apps.billing.models import Invoice
from apps.subscriptions.models import Subscription


@dataclass
class Result:
    name: str
    url: str
    ms: float
    queries: int
    status: int
    kb: float


def _pages(customer_user):
    today = timezone.localdate()
    deep_inv = max(
        1, Invoice.objects.count() // 15 // 2
    )  # roughly the middle page of the invoice list
    deep_sub = max(1, Subscription.objects.count() // 15 // 2)
    internal = [
        ("Dashboard", "/"),
        ("Reports (all time)", "/reports/?preset=all"),
        ("Reports (this month, one plan)", "/reports/?preset=month&plan=1"),
        ("Subscriptions list", "/subscriptions/"),
        ("Subscriptions: status filter", "/subscriptions/?status=active"),
        ("Subscriptions: search by company", "/subscriptions/?q=Kerala"),
        (f"Subscriptions: deep page {deep_sub}", f"/subscriptions/?page={deep_sub}"),
        ("Invoices list", "/invoices/"),
        ("Invoices: overdue filter", "/invoices/?status=overdue"),
        ("Invoices: search by customer", "/invoices/?q=Traders"),
        (f"Invoices: deep page {deep_inv}", f"/invoices/?page={deep_inv}"),
        ("Payments list", "/payments/"),
        ("Customers list", "/customers/"),
        ("Customers: search", "/customers/?q=Nair"),
        ("Billing (due list)", "/billing/"),
        (
            "Subscription detail",
            f"/subscriptions/{Subscription.objects.filter(status='active').values_list('pk', flat=True).first()}/",
        ),
        ("Invoice detail", f"/invoices/{Invoice.objects.values_list('pk', flat=True).first()}/"),
    ]
    portal = [
        ("Portal dashboard", "/"),
        ("Portal subscriptions", "/portal/subscriptions/"),
        ("Portal invoices", "/portal/invoices/"),
    ]
    return internal, portal


def time_page(client, url, runs):
    samples, queries, status, size = [], 0, 0, 0
    for _ in range(runs):
        with CaptureQueriesContext(connection) as ctx:
            start = time.perf_counter()
            response = client.get(url)
            body = b"".join(response.streaming_content) if response.streaming else response.content
            samples.append((time.perf_counter() - start) * 1000)
        queries, status, size = len(ctx), response.status_code, len(body) / 1024
    return statistics.median(samples), queries, status, size


def run(runs=3, stdout=None):
    admin = User.objects.filter(role=Role.ADMIN, is_active=True).order_by("id").first()
    if admin is None:
        raise RuntimeError("No admin user found. Run seed_demo_data or createsuperuser first.")
    # a customer with plenty of data, to see the portal at its heaviest
    busiest = (
        Invoice.objects.values("customer__user_id")
        .order_by()
        .annotate(n=Count("id"))
        .order_by("-n")
        .first()
    )
    customer = User.objects.get(pk=busiest["customer__user_id"]) if busiest else None
    internal, portal = _pages(customer)
    results = []
    client = Client(SERVER_NAME="localhost")
    client.force_login(admin)
    exports = [
        ("CSV export: invoices (all)", "/reports/export/invoices/?preset=all"),
        ("CSV export: payments (all)", "/reports/export/payments/?preset=all"),
        ("CSV export: subscriptions (all)", "/reports/export/subscriptions/?preset=all"),
    ]
    for name, url in internal + exports:
        ms, q, status, kb = time_page(client, url, 1 if name.startswith("CSV") else runs)
        results.append(Result(name, url, ms, q, status, kb))
    if customer:
        client = Client(SERVER_NAME="localhost")
        client.force_login(customer)
        for name, url in portal:
            ms, q, status, kb = time_page(client, url, runs)
            results.append(
                Result(f"{name} (customer with {busiest['n']} invoices)", url, ms, q, status, kb)
            )
    return results


def billing_throughput(limit=300):
    """Bill up to `limit` due subscriptions inside a transaction that is rolled back."""
    today = timezone.localdate()
    due = engine.due_subscriptions(today).count()
    with transaction.atomic():
        ids = list(
            engine.due_subscriptions(today).order_by("id").values_list("pk", flat=True)[limit:]
        )
        # keep only the first `limit` due subscriptions by parking the others, inside this rolled-back transaction
        if ids:
            Subscription.objects.filter(pk__in=ids).update(
                next_billing_date=today + datetime.timedelta(days=400)
            )
        start = time.perf_counter()
        result = engine.run(today=today)
        seconds = time.perf_counter() - start
        transaction.set_rollback(True)
    return {
        "due": due,
        "billed": len(result.created),
        "failed": len(result.failed),
        "seconds": seconds,
    }


def explain_queries():
    """EXPLAIN ANALYZE for the queries that matter most."""
    from apps.payments.selectors import overdue_invoices
    from apps.subscriptions.selectors import subscription_queryset

    today = timezone.localdate()
    admin = User.objects.filter(role=Role.ADMIN).first()
    return [
        (
            "Subscriptions due for billing",
            engine.due_subscriptions(today).order_by("next_billing_date", "id"),
        ),
        ("Overdue invoices", overdue_invoices(today).order_by("due_date", "id")[:15]),
        (
            "Invoice list, first page",
            Invoice.objects.select_related("subscription", "customer__user").order_by("-id")[:15],
        ),
        ("Subscription list, first page (with subtotal)", subscription_queryset(admin)[:15]),
    ]
