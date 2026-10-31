"""Business operations on single invoices (the engine creates them, these manage them)."""

from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.permissions import ensure_admin, ensure_internal

from .models import Invoice, InvoiceStatus

ALLOWED = {
    InvoiceStatus.DRAFT: {InvoiceStatus.CONFIRMED, InvoiceStatus.CANCELLED},
    InvoiceStatus.CONFIRMED: {InvoiceStatus.PAID, InvoiceStatus.CANCELLED},
    InvoiceStatus.PAID: set(),
    InvoiceStatus.CANCELLED: set(),
}


class InvoiceError(Exception):
    """An invoice operation was refused; the message is fit to show to the user."""


def _locked(invoice):
    return Invoice.objects.select_for_update().get(pk=invoice.pk)


@transaction.atomic
def confirm_invoice(*, actor, invoice) -> Invoice:
    ensure_internal(actor)
    inv = _locked(invoice)
    if InvoiceStatus.CONFIRMED not in ALLOWED[InvoiceStatus(inv.status)]:
        raise InvoiceError(f"A {inv.get_status_display().lower()} invoice cannot be confirmed.")
    inv.status = InvoiceStatus.CONFIRMED
    inv.save(update_fields=["status"])
    audit.record(
        actor=actor, action="invoice.confirmed", entity=inv, details={"number": inv.number}
    )
    return inv


@transaction.atomic
def cancel_invoice(*, actor, invoice, reason="") -> Invoice:
    """Admin only. Only an invoice with no payment on it can be cancelled. The period stays billed."""
    ensure_admin(actor)
    inv = _locked(invoice)
    if InvoiceStatus.CANCELLED not in ALLOWED[InvoiceStatus(inv.status)]:
        raise InvoiceError(f"A {inv.get_status_display().lower()} invoice cannot be cancelled.")
    if inv.amount_paid > 0:
        raise InvoiceError("This invoice already has a payment and cannot be cancelled.")
    inv.status = InvoiceStatus.CANCELLED
    inv.save(update_fields=["status"])
    audit.record(
        actor=actor,
        action="invoice.cancelled",
        entity=inv,
        details={"number": inv.number, "reason": reason, "total": str(inv.total)},
    )
    return inv


def send_invoice(*, actor, invoice, base_url="") -> Invoice:
    """Email the invoice summary to the customer (console backend in development)."""
    ensure_internal(actor)
    if invoice.status not in (InvoiceStatus.CONFIRMED, InvoiceStatus.PAID):
        raise InvoiceError("Only confirmed or paid invoices can be sent.")
    if not invoice.bill_to_email:
        raise InvoiceError("The customer has no email address.")
    body = (
        f"Hello {invoice.bill_to_name},\n\n"
        f"Your invoice {invoice.number} for {invoice.plan_name} "
        f"({invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}) is ready.\n\n"
        f"Total: Rs {invoice.total:,.2f}\nAmount due: Rs {invoice.balance:,.2f}\n"
        f"Due date: {invoice.due_date:%d %b %Y} ({invoice.payment_term_name})\n\n"
        "Thank you,\nBrightloop\n"
    )
    send_mail(f"Invoice {invoice.number}", body, None, [invoice.bill_to_email])
    with transaction.atomic():
        invoice.sent_at = timezone.now()
        invoice.save(update_fields=["sent_at"])
        audit.record(
            actor=actor,
            action="invoice.sent",
            entity=invoice,
            details={"number": invoice.number, "to": invoice.bill_to_email},
        )
    return invoice
