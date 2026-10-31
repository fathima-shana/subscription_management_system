"""Money helpers. Every amount in the system is a Decimal rounded once, the same way.

Rounding policy: 2 decimal places, ROUND_HALF_UP (0.005 -> 0.01). Never use float for money.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def D(value) -> Decimal:
    """Convert int/str/float/Decimal to Decimal safely (floats go through str, not binary)."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Not a valid amount: {value!r}") from exc


def quantize(amount) -> Decimal:
    """Round to cents using the project-wide policy."""
    return D(amount).quantize(CENT, rounding=ROUND_HALF_UP)
