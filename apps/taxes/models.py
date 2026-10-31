from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class TaxType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED = "fixed", "Fixed amount"


class Tax(models.Model):
    """A tax rule. Percentage taxes apply `rate`% of the line base; fixed taxes add `rate` per line.

    Taxes are deactivated, never deleted, so old invoices (which snapshot the rate) stay explainable.
    """

    name = models.CharField(max_length=80)
    tax_type = models.CharField(max_length=12, choices=TaxType.choices, default=TaxType.PERCENTAGE)
    rate = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        help_text="Percent (e.g. 18) or a flat amount, depending on type.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="taxes_tax_name_ci_uniq"),
            models.CheckConstraint(condition=Q(rate__gte=0), name="taxes_tax_rate_non_negative"),
            models.CheckConstraint(
                condition=Q(tax_type="fixed") | Q(rate__lte=100), name="taxes_tax_percent_max_100"
            ),
            models.CheckConstraint(
                condition=Q(tax_type__in=TaxType.values), name="taxes_tax_type_valid"
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        if self.tax_type == TaxType.PERCENTAGE:
            return f"{self.name} ({self.rate.normalize():f}%)"
        return f"{self.name} (₹{self.rate:,.2f} flat)"
