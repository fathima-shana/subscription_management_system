"""Data integrity checks: the books must always add up.

Each check is one aggregate query, so it stays fast on a large database. The seed command runs
this after loading data, and the tests run it after the billing engine and payments.
"""

from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.billing.models import Invoice, InvoiceLine, InvoiceStatus
from apps.core.models import NumberSequence
from apps.discounts.models import Discount
from apps.payments.models import Payment
from apps.subscriptions.models import Status, Subscription

MONEY = DecimalField(max_digits=16, decimal_places=2)
ZERO = Value(0, output_field=MONEY)


def _sum_for_invoice(model, field, invoice_field="invoice", extra=None):
    qs = model.objects.filter(**{invoice_field: OuterRef("pk")})
    if extra:
        qs = qs.filter(extra)
    return Coalesce(
        Subquery(
            qs.values(invoice_field).annotate(s=Sum(field)).values("s")[:1], output_field=MONEY
        ),
        ZERO,
    )


def run_checks():
    """Return a list of human-readable problems (an empty list means everything adds up)."""
    problems = []

    def report(name, count, example=None):
        if count:
            problems.append(
                f"{name}: {count} record(s)" + (f", for example {example}" if example else "")
            )

    invoices = Invoice.objects.annotate(
        lines_amount=_sum_for_invoice(InvoiceLine, "amount"),
        lines_discount=_sum_for_invoice(InvoiceLine, "discount_amount"),
        lines_tax=_sum_for_invoice(InvoiceLine, "tax_amount"),
        lines_total=_sum_for_invoice(InvoiceLine, "total"),
        live_paid=_sum_for_invoice(Payment, "amount", extra=Q(voided_at__isnull=True)),
    )

    bad = invoices.exclude(total=F("subtotal") - F("discount_total") + F("tax_total"))
    report(
        "Invoice total is not subtotal - discount + tax",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )

    for label, field, lines in [
        ("subtotal", "subtotal", "lines_amount"),
        ("discount", "discount_total", "lines_discount"),
        ("tax", "tax_total", "lines_tax"),
        ("total", "total", "lines_total"),
    ]:
        bad = invoices.exclude(**{field: F(lines)})
        report(
            f"Invoice {label} differs from the sum of its lines",
            bad.count(),
            bad.values_list("number", flat=True).first(),
        )

    bad = InvoiceLine.objects.exclude(total=F("amount") - F("discount_amount") + F("tax_amount"))
    report("Invoice line total is not amount - discount + tax", bad.count())

    bad = invoices.exclude(amount_paid=F("live_paid"))
    report(
        "amount_paid differs from the sum of live payments",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )

    live = invoices.exclude(status__in=[InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED])
    bad = live.filter(status=InvoiceStatus.PAID, amount_paid__lt=F("total"))
    report(
        "Paid invoice that is not fully paid",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )
    bad = live.filter(status=InvoiceStatus.CONFIRMED, amount_paid__gte=F("total"))
    report(
        "Confirmed invoice that is already fully paid",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )
    bad = Invoice.objects.filter(status=InvoiceStatus.CANCELLED, amount_paid__gt=0)
    report(
        "Cancelled invoice with a payment",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )

    dupes = (
        Invoice.objects.values("subscription", "period_start")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    report("Period billed more than once", dupes.count())

    used = dict(
        Invoice.objects.filter(discount__isnull=False)
        .values_list("discount")
        .annotate(n=Count("id"))
    )
    for d in Discount.objects.all():
        if d.used_count != used.get(d.pk, 0):
            problems.append(
                f"Discount {d.name}: used_count {d.used_count} but {used.get(d.pk, 0)} invoice(s) use it"
            )

    bad = Subscription.objects.filter(
        status=Status.ACTIVE, next_billing_date__isnull=True, expiry_date__isnull=True
    )
    report(
        "Active subscription with no next billing date and no expiry",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )
    bad = Subscription.objects.filter(status=Status.CLOSED, next_billing_date__isnull=False)
    report(
        "Closed subscription that still has a next billing date",
        bad.count(),
        bad.values_list("number", flat=True).first(),
    )
    bad = Subscription.objects.filter(
        status__in=[Status.DRAFT, Status.QUOTATION, Status.CONFIRMED], invoices__isnull=False
    )
    report("Subscription that was never activated but has invoices", bad.distinct().count())

    for key, model, prefix in [("subscription", Subscription, "SUB"), ("invoice", Invoice, "INV")]:
        highest = max(
            (
                int(n.split("-")[1])
                for n in model.objects.order_by("-number").values_list("number", flat=True)[:1]
            ),
            default=0,
        )
        seq = NumberSequence.objects.filter(key=key).first()
        if highest and (seq is None or seq.next_value <= highest):
            problems.append(
                f"Number sequence {key} is behind the highest {prefix} number ({highest})"
            )
    return problems
