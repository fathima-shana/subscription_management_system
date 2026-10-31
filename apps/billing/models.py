from django.db import models
from django.db.models import F, Q

from apps.core.money import quantize


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class Invoice(models.Model):
    """An invoice for one billing period of one subscription.

    Everything a customer sees (names, prices, tax rates, discount) is copied onto the invoice
    when it is created, so later catalog or customer changes never alter an issued invoice.
    "Overdue" is not stored: it is derived from due_date and status (see `display_status`).
    """

    number = models.CharField(max_length=20, unique=True, editable=False)
    subscription = models.ForeignKey(
        "subscriptions.Subscription", on_delete=models.PROTECT, related_name="invoices"
    )
    customer = models.ForeignKey(
        "accounts.CustomerProfile", on_delete=models.PROTECT, related_name="invoices"
    )
    status = models.CharField(
        max_length=10, choices=InvoiceStatus.choices, default=InvoiceStatus.CONFIRMED
    )
    period_start = models.DateField()
    period_end = models.DateField()
    issue_date = models.DateField()
    due_date = models.DateField()

    # Snapshots
    bill_to_name = models.CharField(max_length=200)
    bill_to_email = models.EmailField(blank=True)
    bill_to_address = models.TextField(blank=True)
    bill_to_tax_id = models.CharField(max_length=50, blank=True)
    plan_name = models.CharField(max_length=100)
    payment_term_name = models.CharField(max_length=60)

    discount = models.ForeignKey(
        "discounts.Discount", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    discount_name = models.CharField(max_length=100, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    discount_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # The database itself guarantees a period is never billed twice.
            models.UniqueConstraint(
                fields=["subscription", "period_start"], name="billing_one_invoice_per_period"
            ),
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")), name="billing_period_ordered"
            ),
            models.CheckConstraint(condition=Q(total__gte=0), name="billing_total_non_neg"),
            models.CheckConstraint(
                condition=Q(amount_paid__gte=0) & Q(amount_paid__lte=F("total")),
                name="billing_paid_within_total",
            ),
            models.CheckConstraint(
                condition=Q(status__in=InvoiceStatus.values), name="billing_status_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="billing_status_idx"),
            models.Index(fields=["due_date", "status"], name="billing_due_status_idx"),
            models.Index(fields=["customer"], name="billing_customer_idx"),
        ]

    def __str__(self):
        return self.number

    @property
    def balance(self):
        return quantize(self.total - self.amount_paid)

    def is_overdue(self, today) -> bool:
        return self.status == InvoiceStatus.CONFIRMED and self.due_date < today and self.balance > 0

    def display_status(self, today) -> str:
        return "overdue" if self.is_overdue(today) else self.status


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    description = models.CharField(max_length=250)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)  # quantity x unit price
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_name = models.CharField(max_length=80, blank=True)
    tax_type = models.CharField(max_length=12, blank=True)
    tax_rate = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="billing_line_qty_positive")
        ]
