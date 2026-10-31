from django.conf import settings
from django.db import models
from django.db.models import Q


class Method(models.TextChoices):
    CASH = "cash", "Cash"
    BANK = "bank", "Bank transfer"
    CARD = "card", "Card"
    UPI = "upi", "UPI"


class Payment(models.Model):
    """Money received against one invoice. Never edited or deleted: a mistake is voided.

    An invoice's `amount_paid` is always the sum of its payments that are not voided, and it is
    recalculated from them (never incremented) every time one changes.
    """

    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="payments"
    )
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_date = models.DateField()
    note = models.CharField("Reference / note", max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_amount_positive"),
            models.CheckConstraint(
                condition=Q(method__in=Method.values), name="payments_method_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["invoice"], name="payments_invoice_idx"),
            models.Index(fields=["payment_date"], name="payments_date_idx"),
        ]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} for {self.invoice.number}"

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None
