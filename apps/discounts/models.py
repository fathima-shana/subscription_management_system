from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone


class DiscountType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED = "fixed", "Fixed amount"


class AppliesTo(models.TextChoices):
    PRODUCT = "product", "Selected products"
    SUBSCRIPTION = "subscription", "Whole subscription"


class Discount(models.Model):
    """A discount rule. The billing engine decides when it applies (see engine.py).

    `used_count` is only ever changed through services.redeem(), which is atomic and can never
    go past `usage_limit` (a database CHECK backs this up).
    """

    name = models.CharField(max_length=100)
    discount_type = models.CharField(
        max_length=12, choices=DiscountType.choices, default=DiscountType.PERCENTAGE
    )
    value = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Percent (e.g. 10) or a flat amount."
    )
    applies_to = models.CharField(
        max_length=14, choices=AppliesTo.choices, default=AppliesTo.SUBSCRIPTION
    )
    products = models.ManyToManyField("catalog.Product", blank=True, related_name="discounts")
    min_purchase = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Minimum amount (before tax) the discount applies to.",
    )
    min_quantity = models.PositiveIntegerField(
        default=1, help_text="Minimum total quantity it applies to."
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Leave empty for unlimited use."
    )
    used_count = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="discounts_discount_name_ci_uniq"),
            models.CheckConstraint(condition=Q(value__gt=0), name="discounts_value_positive"),
            models.CheckConstraint(
                condition=Q(discount_type="fixed") | Q(value__lte=100),
                name="discounts_percent_max_100",
            ),
            models.CheckConstraint(
                condition=Q(min_purchase__gte=0), name="discounts_min_purchase_non_neg"
            ),
            models.CheckConstraint(
                condition=Q(min_quantity__gte=1), name="discounts_min_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(end_date__isnull=True)
                | Q(end_date__gte=F("start_date")),
                name="discounts_dates_ordered",
            ),
            models.CheckConstraint(
                condition=Q(usage_limit__isnull=True) | Q(usage_limit__gte=1),
                name="discounts_limit_positive",
            ),
            models.CheckConstraint(
                condition=Q(usage_limit__isnull=True) | Q(used_count__lte=F("usage_limit")),
                name="discounts_used_within_limit",
            ),
            models.CheckConstraint(
                condition=Q(discount_type__in=DiscountType.values), name="discounts_type_valid"
            ),
            models.CheckConstraint(
                condition=Q(applies_to__in=AppliesTo.values), name="discounts_applies_to_valid"
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def value_label(self) -> str:
        if self.discount_type == DiscountType.PERCENTAGE:
            return f"{self.value.normalize():f}%"
        return f"₹{self.value:,.2f}"

    @property
    def is_exhausted(self) -> bool:
        return self.usage_limit is not None and self.used_count >= self.usage_limit

    @property
    def uses_left(self):
        return None if self.usage_limit is None else max(self.usage_limit - self.used_count, 0)

    def status(self, on_date=None) -> str:
        """One word for badges: inactive, scheduled, expired, exhausted or active."""
        today = on_date or timezone.localdate()
        if not self.is_active:
            return "inactive"
        if self.start_date and today < self.start_date:
            return "scheduled"
        if self.end_date and today > self.end_date:
            return "expired"
        if self.is_exhausted:
            return "exhausted"
        return "active"
