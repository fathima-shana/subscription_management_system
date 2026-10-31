from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.taxes.calculator import compute_tax, compute_taxes


def pct(rate):
    return SimpleNamespace(tax_type="percentage", rate=Decimal(rate), name="T", label="T")


def flat(rate):
    return SimpleNamespace(tax_type="fixed", rate=Decimal(rate), name="F", label="F")


class CalculatorTests(SimpleTestCase):
    def test_percentage(self):
        self.assertEqual(compute_tax("1000", pct("18")), Decimal("180.00"))

    def test_percentage_rounds_half_up(self):
        self.assertEqual(compute_tax("0.25", pct("2")), Decimal("0.01"))  # 0.005 -> 0.01

    def test_fixed_is_flat_per_line(self):
        self.assertEqual(compute_tax("5", flat("12.50")), Decimal("12.50"))

    def test_zero_base(self):
        self.assertEqual(compute_tax("0", pct("18")), Decimal("0.00"))

    def test_negative_base_rejected(self):
        with self.assertRaises(ValueError):
            compute_tax("-1", pct("18"))

    def test_multiple_taxes_use_same_base_and_sum_exactly(self):
        result = compute_taxes("333.33", [pct("9"), pct("9"), flat("1")])
        self.assertEqual(
            [l.amount for l in result.lines], [Decimal("30.00"), Decimal("30.00"), Decimal("1.00")]
        )
        self.assertEqual(result.total, Decimal("61.00"))
        self.assertEqual(result.grand_total, Decimal("394.33"))

    def test_no_taxes(self):
        result = compute_taxes("100", [])
        self.assertEqual(result.total, Decimal("0.00"))
        self.assertEqual(result.grand_total, Decimal("100.00"))
