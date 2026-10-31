from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.money import quantize


class ProductType(models.TextChoices):
    SERVICE = "service", "Service"
    GOODS = "goods", "Goods"


class Product(models.Model):
    """Something that can be sold on a subscription. Deactivated, never deleted."""

    name = models.CharField(max_length=150)
    product_type = models.CharField(
        max_length=10, choices=ProductType.choices, default=ProductType.SERVICE
    )
    sales_price = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    default_tax = models.ForeignKey(
        "taxes.Tax",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Pre-selected on subscription lines. Can be overridden per line.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="catalog_product_name_ci_uniq"),
            models.CheckConstraint(
                condition=Q(sales_price__gte=0), name="catalog_product_sales_non_neg"
            ),
            models.CheckConstraint(
                condition=Q(cost_price__gte=0), name="catalog_product_cost_non_neg"
            ),
            models.CheckConstraint(
                condition=Q(product_type__in=ProductType.values), name="catalog_product_type_valid"
            ),
        ]
        indexes = [models.Index(fields=["is_active", "name"], name="catalog_product_active_idx")]

    def __str__(self):
        return self.name

    @property
    def margin(self):
        return quantize(self.sales_price - self.cost_price)

    def price_for(self, variant=None):
        """Unit price for this product, optionally with a variant's extra price added."""
        if variant is None:
            return quantize(self.sales_price)
        return variant.price


class ProductVariant(models.Model):
    """One option of a product, e.g. Brand = Odoo (+560). Final price = product price + extra."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    attribute = models.CharField(max_length=60)
    value = models.CharField(max_length=60)
    extra_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["attribute", "value"]
        constraints = [
            models.UniqueConstraint(
                "product", Lower("attribute"), Lower("value"), name="catalog_variant_unique_option"
            ),
            models.CheckConstraint(
                condition=Q(extra_price__gte=0), name="catalog_variant_extra_non_neg"
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return f"{self.attribute}: {self.value}"

    @property
    def price(self):
        return quantize(self.product.sales_price + self.extra_price)


class BillingPeriod(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"


class Plan(models.Model):
    """A recurring billing plan: how often to bill and which lifecycle actions are allowed."""

    name = models.CharField(max_length=100)
    billing_period = models.CharField(
        max_length=10, choices=BillingPeriod.choices, default=BillingPeriod.MONTHLY
    )
    min_quantity = models.PositiveIntegerField(
        default=1, help_text="Smallest quantity allowed per line."
    )
    start_date = models.DateField(
        null=True, blank=True, help_text="First day the plan can be used."
    )
    end_date = models.DateField(null=True, blank=True, help_text="Last day the plan can be used.")
    auto_close = models.BooleanField(
        default=False, help_text="Close subscriptions automatically at the end date."
    )
    closable = models.BooleanField(default=True, help_text="Subscriptions can be closed early.")
    pausable = models.BooleanField(default=False, help_text="Subscriptions can be paused.")
    renewable = models.BooleanField(default=True, help_text="Subscriptions can be renewed.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="catalog_plan_name_ci_uniq"),
            models.CheckConstraint(
                condition=Q(min_quantity__gte=1), name="catalog_plan_min_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(start_date__isnull=True)
                | Q(end_date__isnull=True)
                | Q(end_date__gte=models.F("start_date")),
                name="catalog_plan_dates_ordered",
            ),
            models.CheckConstraint(
                condition=Q(billing_period__in=BillingPeriod.values),
                name="catalog_plan_period_valid",
            ),
        ]

    def __str__(self):
        return self.name
