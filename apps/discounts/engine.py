"""Discount engine: decides which discount applies to a set of subscription lines.

Pure Python on top of already-loaded objects (no writes), so it is easy to unit-test and the
billing engine can call it inside its own transaction.

Documented rules
* A discount applies only when it is active, inside its date range, not exhausted, and the
  quantity and amount it covers reach its minimums.
* "Selected products" discounts look only at lines of those products; "Whole subscription"
  discounts look at every line. Amounts are before tax.
* Percentage = base x value / 100. Fixed = the value, but never more than the base.
* NO STACKING: when several discounts are eligible, the one that saves the most wins.
  Ties go to the older discount (lower id). The usage counter is consumed separately,
  by services.redeem(), when the invoice is actually created.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from apps.core.money import ZERO, D, quantize

from .models import AppliesTo, DiscountType


@dataclass(frozen=True)
class Line:
    """What the engine needs to know about one subscription line (amount before discount and tax)."""

    product_id: int
    quantity: int
    amount: Decimal


@dataclass(frozen=True)
class Applied:
    discount: object
    base: Decimal  # the amount the discount was calculated on
    amount: Decimal  # the saving


def covered_lines(discount, lines):
    """The lines this discount looks at (all of them, or only the selected products')."""
    if discount.applies_to == AppliesTo.PRODUCT:
        wanted = {p.pk for p in discount.products.all()}  # prefetch-friendly
        return [line for line in lines if line.product_id in wanted]
    return list(lines)


def _covered(discount, lines):
    """(quantity, amount) of the lines this discount looks at."""
    covered = covered_lines(discount, lines)
    if not covered:
        return 0, ZERO
    return sum(line.quantity for line in covered), sum((D(line.amount) for line in covered), ZERO)


def ineligible_reason(discount, lines, on_date=None):
    """None when the discount can apply, otherwise a short human-readable reason."""
    today = on_date or timezone.localdate()
    if not discount.is_active:
        return "The discount is switched off."
    if discount.start_date and today < discount.start_date:
        return f"It starts on {discount.start_date:%d %b %Y}."
    if discount.end_date and today > discount.end_date:
        return f"It ended on {discount.end_date:%d %b %Y}."
    if discount.is_exhausted:
        return "The usage limit has been reached."
    quantity, amount = _covered(discount, lines)
    if quantity == 0:
        return "None of the selected products are on this subscription."
    if quantity < discount.min_quantity:
        return f"Minimum quantity {discount.min_quantity} not reached (has {quantity})."
    if amount < discount.min_purchase:
        return f"Minimum purchase ₹{discount.min_purchase:,.2f} not reached (has ₹{amount:,.2f})."
    return None


def evaluate(discount, lines, on_date=None):
    """The saving this single discount gives, or None if it does not apply."""
    if ineligible_reason(discount, lines, on_date) is not None:
        return None
    _, base = _covered(discount, lines)
    if discount.discount_type == DiscountType.PERCENTAGE:
        saving = quantize(base * D(discount.value) / 100)
    else:
        saving = quantize(min(D(discount.value), base))
    if saving <= 0:
        return None
    return Applied(discount=discount, base=quantize(base), amount=saving)


def best_discount(discounts, lines, on_date=None):
    """The single eligible discount with the biggest saving (no stacking), or None."""
    best = None
    for discount in discounts:
        applied = evaluate(discount, lines, on_date)
        if applied is None:
            continue
        if best is None or (applied.amount, -applied.discount.pk) > (
            best.amount,
            -best.discount.pk,
        ):
            best = applied
    return best


def explain(discounts, lines, on_date=None):
    """[(discount, Applied or None, reason or None)] for debugging and the admin UI."""
    rows = []
    for discount in discounts:
        reason = ineligible_reason(discount, lines, on_date)
        rows.append((discount, None if reason else evaluate(discount, lines, on_date), reason))
    return rows
