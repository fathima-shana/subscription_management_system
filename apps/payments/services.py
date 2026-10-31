"""Recording and voiding payments.

Both operations lock the invoice row first, so two people paying the same invoice at once can
never take it past its total. After every change the invoice's amount_paid and status are
recalculated from its live (non-voided) payments.
"""

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import Invoice, InvoiceStatus
from apps.core import audit
from apps.core.money import ZERO, D, quantize
from apps.core.permissions import ensure_admin, ensure_internal

from .models import Method, Payment


class PaymentError(Exception):
    """A payment operation was refused; the message is fit to show to the user."""


def _settle(invoice):
    """Recalculate amount_paid and status from the live payments."""
    paid = (
        Payment.objects.filter(invoice=invoice, voided_at__isnull=True).aggregate(s=Sum("amount"))[
            "s"
        ]
        or ZERO
    )
    invoice.amount_paid = paid
    if invoice.status in (InvoiceStatus.CONFIRMED, InvoiceStatus.PAID):
        invoice.status = InvoiceStatus.PAID if paid >= invoice.total else InvoiceStatus.CONFIRMED
    invoice.save(update_fields=["amount_paid", "status"])


@transaction.atomic
def record_payment(*, actor, invoice, amount, method, payment_date=None, note="") -> Payment:
    ensure_internal(actor)
    try:
        amount = quantize(D(amount))
    except ValueError as exc:
        raise PaymentError("Enter a valid amount.") from exc
    if amount <= 0:
        raise PaymentError("The amount must be more than zero.")
    if method not in Method.values:
        raise PaymentError("Choose a payment method.")
    today = timezone.localdate()
    payment_date = payment_date or today
    if payment_date > today:
        raise PaymentError("The payment date cannot be in the future.")

    inv = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status != InvoiceStatus.CONFIRMED:
        raise PaymentError(f"A {inv.get_status_display().lower()} invoice cannot receive payments.")
    if amount > inv.balance:
        raise PaymentError(
            f"The payment (₹{amount:,.2f}) is more than the balance due (₹{inv.balance:,.2f})."
        )

    payment = Payment.objects.create(
        invoice=inv,
        method=method,
        amount=amount,
        payment_date=payment_date,
        note=note.strip()[:200],
        created_by=actor,
    )
    _settle(inv)
    audit.record(
        actor=actor,
        action="payment.recorded",
        entity=payment,
        details={
            "invoice": inv.number,
            "amount": str(amount),
            "method": method,
            "settled": inv.status == InvoiceStatus.PAID,
        },
    )
    invoice.amount_paid, invoice.status = (
        inv.amount_paid,
        inv.status,
    )  # keep the caller's object current
    return payment


@transaction.atomic
def void_payment(*, actor, payment, reason="") -> Payment:
    """Admin only. Reverses a payment entered by mistake; the invoice is reopened if needed."""
    ensure_admin(actor)
    inv = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    pay = Payment.objects.select_for_update().get(pk=payment.pk)
    if pay.voided_at is not None:
        raise PaymentError("This payment was already voided.")
    pay.voided_at = timezone.now()
    pay.voided_by = actor
    pay.void_reason = reason.strip()[:200]
    pay.save(update_fields=["voided_at", "voided_by", "void_reason"])
    _settle(inv)
    audit.record(
        actor=actor,
        action="payment.voided",
        entity=pay,
        details={"invoice": inv.number, "amount": str(pay.amount), "reason": pay.void_reason},
    )
    return pay
