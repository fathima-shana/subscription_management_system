import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from apps.catalog.models import Product
from apps.discounts.models import Discount
from apps.subscriptions.pricing import price_lines
from apps.taxes.models import Tax

D = Decimal
TODAY = datetime.date(2026, 6, 15)


class PricingTests(TestCase):
    def setUp(self):
        self.crm = Product.objects.create(name="CRM", sales_price=D("1000"))
        self.pos = Product.objects.create(name="POS", sales_price=D("500"))
        self.gst = Tax.objects.create(name="GST", tax_type="percentage", rate=D("18"))

    def line(self, product, qty, price, tax=None):
        return SimpleNamespace(product_id=product.pk, quantity=qty, unit_price=D(price), tax=tax)

    def disc(self, name, kind="percentage", value="10", products=(), **extra):
        d = Discount.objects.create(
            name=name,
            discount_type=kind,
            value=D(value),
            applies_to="product" if products else "subscription",
            **extra,
        )
        d.products.set(products)
        return Discount.objects.prefetch_related("products").get(pk=d.pk)

    def test_no_discount_tax_on_full_amount(self):
        priced = price_lines([self.line(self.crm, 2, "1000", self.gst)], [], TODAY)
        self.assertEqual(
            (priced.subtotal, priced.discount_total, priced.tax_total, priced.total),
            (D("2000.00"), D("0.00"), D("360.00"), D("2360.00")),
        )
        self.assertIsNone(priced.applied)

    def test_tax_is_charged_after_the_discount(self):
        priced = price_lines([self.line(self.crm, 1, "1000", self.gst)], [self.disc("Ten")], TODAY)
        self.assertEqual(priced.discount_total, D("100.00"))
        self.assertEqual(priced.tax_total, D("162.00"))  # 18% of 900, not of 1000
        self.assertEqual(priced.total, D("1062.00"))

    def test_discount_is_shared_in_proportion_across_lines(self):
        lines = [self.line(self.crm, 1, "1000"), self.line(self.pos, 1, "500")]
        priced = price_lines(lines, [self.disc("Flat", "fixed", "150")], TODAY)
        self.assertEqual([p.discount for p in priced.lines], [D("100.00"), D("50.00")])

    def test_shares_always_add_up_exactly(self):
        lines = [
            self.line(self.crm, 1, "33.33"),
            self.line(self.crm, 1, "33.33"),
            self.line(self.pos, 1, "33.34"),
        ]
        priced = price_lines(lines, [self.disc("Flat", "fixed", "10")], TODAY)
        self.assertEqual(sum(p.discount for p in priced.lines), D("10.00"))
        self.assertEqual(priced.discount_total, D("10.00"))
        self.assertEqual(priced.subtotal - priced.discount_total + priced.tax_total, priced.total)

    def test_product_scoped_discount_touches_only_those_lines(self):
        lines = [self.line(self.crm, 1, "1000"), self.line(self.pos, 1, "500")]
        priced = price_lines(lines, [self.disc("CRM only", value="20", products=[self.crm])], TODAY)
        self.assertEqual([p.discount for p in priced.lines], [D("200.00"), D("0.00")])

    def test_best_discount_wins_no_stacking(self):
        lines = [self.line(self.crm, 1, "1000")]
        priced = price_lines(lines, [self.disc("Ten"), self.disc("Fifteen", value="15")], TODAY)
        self.assertEqual(priced.applied.discount.name, "Fifteen")
        self.assertEqual(priced.discount_total, D("150.00"))

    def test_discount_never_makes_a_line_negative(self):
        priced = price_lines(
            [self.line(self.pos, 1, "50")], [self.disc("Huge", "fixed", "999")], TODAY
        )
        self.assertEqual((priced.discount_total, priced.total), (D("50.00"), D("0.00")))

    def test_empty_subscription(self):
        priced = price_lines([], [self.disc("Ten")], TODAY)
        self.assertEqual((priced.subtotal, priced.total), (D("0"), D("0")))
