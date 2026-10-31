"""Tax calculator: pure functions, no database access, easy to unit-test.

Rules (documented because auditors ask):
* The base is the line amount AFTER discounts and BEFORE tax.
* Several taxes on one line are each computed on the same base (no compounding).
* A fixed tax is a flat amount added once per line.
* Each tax amount is rounded to cents (half-up) individually, then summed, so the displayed
  tax lines always add up exactly to the displayed total.
"""

from dataclasses import dataclass

from apps.core.money import ZERO, D, quantize

from .models import TaxType


@dataclass(frozen=True)
class TaxLine:
    tax: object
    amount: object  # Decimal


@dataclass(frozen=True)
class TaxResult:
    base: object  # Decimal
    lines: tuple
    total: object  # Decimal

    @property
    def grand_total(self):
        return self.base + self.total


def compute_tax(base, tax):
    """Tax amount for one tax on `base` (any object with .tax_type and .rate)."""
    base = D(base)
    if base < 0:
        raise ValueError("Tax base cannot be negative.")
    if tax.tax_type == TaxType.PERCENTAGE:
        return quantize(base * D(tax.rate) / 100)
    return quantize(tax.rate)


def compute_taxes(base, taxes) -> TaxResult:
    base = quantize(base)
    lines = tuple(TaxLine(tax=t, amount=compute_tax(base, t)) for t in taxes)
    total = sum((line.amount for line in lines), ZERO)
    return TaxResult(base=base, lines=lines, total=total)
