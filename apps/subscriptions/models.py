from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.core.money import quantize


class PaymentTerm(models.Model):
    """How long a customer has to pay an invoice (due date = invoice date + days)."""

    name = models.CharField(max_length=60, unique=True)
    days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["days", "name"]

    def __str__(self):
        return self.name


class QuotationTemplate(models.Model):
    """A reusable starting point: plan + product lines + how long the quotation stays valid."""

    name = models.CharField(max_length=100)
    plan = models.ForeignKey("catalog.Plan", on_delete=models.PROTECT, related_name="+")
    validity_days = models.PositiveIntegerField(
        default=30, help_text="Days a quotation made from this template stays valid."
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="subs_template_name_ci_uniq"),
            models.CheckConstraint(
                condition=Q(validity_days__gte=1), name="subs_template_validity_positive"
            ),
        ]

    def __str__(self):
        return self.name


class QuotationTemplateLine(models.Model):
    template = models.ForeignKey(QuotationTemplate, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    variant = models.ForeignKey(
        "catalog.ProductVariant", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=1), name="subs_template_line_qty_positive"
            )
        ]

    @property
    def label(self) -> str:
        return (
            f"{self.product.name} ({self.variant.label})" if self.variant_id else self.product.name
        )

    @property
    def unit_price(self):
        return self.product.price_for(self.variant)

    @property
    def amount(self):
        return quantize(self.unit_price * self.quantity)


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    QUOTATION = "quotation", "Quotation"
    CONFIRMED = "confirmed", "Confirmed"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    CLOSED = "closed", "Closed"


class Subscription(models.Model):
    number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        "accounts.CustomerProfile", on_delete=models.PROTECT, related_name="subscriptions"
    )
    plan = models.ForeignKey("catalog.Plan", on_delete=models.PROTECT, related_name="+")
    template = models.ForeignKey(
        QuotationTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    payment_term = models.ForeignKey(PaymentTerm, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    start_date = models.DateField()
    expiry_date = models.DateField(
        null=True, blank=True, help_text="Last day of the subscription. Empty = open-ended."
    )
    quote_valid_until = models.DateField(
        null=True, blank=True, help_text="The quotation can be confirmed until this date."
    )
    next_billing_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    renewed_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=Status.values), name="subs_status_valid"),
            models.CheckConstraint(
                condition=Q(expiry_date__isnull=True) | Q(expiry_date__gte=F("start_date")),
                name="subs_expiry_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="subs_status_idx"),
            # The billing run only ever asks for active subscriptions that are due.
            models.Index(
                fields=["next_billing_date"], name="subs_due_idx", condition=Q(status="active")
            ),
            models.Index(fields=["customer", "status"], name="subs_customer_status_idx"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_editable(self) -> bool:
        """Header and lines can change only before the customer has confirmed."""
        return self.status in (Status.DRAFT, Status.QUOTATION)


class SubscriptionLine(models.Model):
    """One product on a subscription. `unit_price` is a snapshot taken when the line was added."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    variant = models.ForeignKey(
        "catalog.ProductVariant", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax = models.ForeignKey(
        "taxes.Tax", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="subs_line_qty_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="subs_line_price_non_neg"),
        ]

    @property
    def label(self) -> str:
        return (
            f"{self.product.name} ({self.variant.label})" if self.variant_id else self.product.name
        )

    @property
    def amount(self):
        return quantize(self.unit_price * self.quantity)


class SubscriptionStatusHistory(models.Model):
    """Append-only trail of every status change."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=10, blank=True)
    to_status = models.CharField(max_length=10)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["changed_at", "id"]
