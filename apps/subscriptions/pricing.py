"""Prices a set of subscription lines: discount first, then tax. Pure functions, no writes.

Order of operations (the billing engine uses exactly this, so previews match invoices):
1. line amount = quantity x unit price
2. the best eligible discount is chosen (no stacking) and its saving is shared across the
   lines it covers in proportion to their amounts; any leftover cent goes to the largest line
3. tax is computed on each line AFTER its share of the discount
4. totals are sums of the rounded line figures, so the lines always add up exactly
"""

from dataclasses import dataclass

from apps.core.money import ZERO, quantize
from apps.discounts import engine
from apps.discounts.engine import Line
from apps.taxes.calculator import compute_taxes


@dataclass(frozen=True)
class PricedLine:
    line: object
    amount: object  # before discount and tax
    discount: object
    taxable: object  # amount - discount
    tax: object
    total: object  # taxable + tax
    tax_lines: tuple


@dataclass(frozen=True)
class Priced:
    lines: tuple
    subtotal: object
    discount_total: object
    tax_total: object
    total: object
    applied: object  # engine.Applied or None


def _share_out(saving, amounts):
    """Split `saving` across `amounts` proportionally; the result sums to exactly `saving`."""
    base = sum(amounts, ZERO)
    if base <= 0:
        return [ZERO] * len(amounts)
    shares = [quantize(a * saving / base) for a in amounts]
    leftover = saving - sum(shares, ZERO)
    if leftover:
        biggest = max(range(len(amounts)), key=lambda i: amounts[i])
        shares[biggest] += leftover
    return shares


def price_lines(lines, discounts=(), on_date=None) -> Priced:
    """`lines`: objects with product_id, quantity, unit_price, tax (Tax or None)."""
    lines = list(lines)
    amounts = [quantize(l.quantity * l.unit_price) for l in lines]
    engine_lines = [Line(l.product_id, l.quantity, amt) for l, amt in zip(lines, amounts)]

    applied = engine.best_discount(discounts, engine_lines, on_date) if lines else None
    discounts_per_line = [ZERO] * len(lines)
    if applied is not None:
        covered = set(map(id, engine.covered_lines(applied.discount, engine_lines)))
        idx = [i for i, el in enumerate(engine_lines) if id(el) in covered]
        for i, share in zip(idx, _share_out(applied.amount, [amounts[i] for i in idx])):
            discounts_per_line[i] = share

    priced = []
    for line, amount, discount in zip(lines, amounts, discounts_per_line):
        taxable = amount - discount
        result = compute_taxes(taxable, [line.tax] if line.tax else [])
        priced.append(
            PricedLine(
                line=line,
                amount=amount,
                discount=discount,
                taxable=taxable,
                tax=result.total,
                total=taxable + result.total,
                tax_lines=result.lines,
            )
        )
    return Priced(
        lines=tuple(priced),
        subtotal=sum((p.amount for p in priced), ZERO),
        discount_total=sum((p.discount for p in priced), ZERO),
        tax_total=sum((p.tax for p in priced), ZERO),
        total=sum((p.total for p in priced), ZERO),
        applied=applied,
    )
