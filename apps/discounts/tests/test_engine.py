import datetime
import threading
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.catalog.models import Product
from apps.discounts import engine, services
from apps.discounts.engine import Line
from apps.discounts.models import Discount

TODAY = datetime.date(2026, 6, 15)
D = Decimal


def mk(name, kind="percentage", value="10", **extra):
    products = extra.pop("products", [])
    d = Discount.objects.create(name=name, discount_type=kind, value=D(value), **extra)
    if products:
        d.products.set(products)
    return d


class EngineTestCase(TestCase):
    def setUp(self):
        self.crm = Product.objects.create(name="CRM", sales_price=D("1000"))
        self.pos = Product.objects.create(name="POS", sales_price=D("500"))
        # 2 x CRM = 2000 and 1 x POS = 500  -> 3 units, 2500 total
        self.lines = [Line(self.crm.pk, 2, D("2000")), Line(self.pos.pk, 1, D("500"))]


class CalculationTests(EngineTestCase):
    def test_percentage_on_whole_subscription(self):
        applied = engine.evaluate(mk("Ten", value="10"), self.lines, TODAY)
        self.assertEqual((applied.base, applied.amount), (D("2500.00"), D("250.00")))

    def test_fixed_amount(self):
        self.assertEqual(
            engine.evaluate(mk("Flat", "fixed", "300"), self.lines, TODAY).amount, D("300.00")
        )

    def test_fixed_never_exceeds_the_base(self):
        small = [Line(self.pos.pk, 1, D("50"))]
        self.assertEqual(
            engine.evaluate(mk("Big", "fixed", "300"), small, TODAY).amount, D("50.00")
        )

    def test_product_scope_uses_only_matching_lines(self):
        d = mk("CRM only", value="10", applies_to="product", products=[self.crm])
        applied = engine.evaluate(d, self.lines, TODAY)
        self.assertEqual((applied.base, applied.amount), (D("2000.00"), D("200.00")))

    def test_product_scope_with_no_matching_line(self):
        other = Product.objects.create(name="Other", sales_price=D("1"))
        d = mk("Other only", applies_to="product", products=[other])
        self.assertIsNone(engine.evaluate(d, self.lines, TODAY))
        self.assertIn(
            "None of the selected products", engine.ineligible_reason(d, self.lines, TODAY)
        )

    def test_rounds_half_up(self):
        lines = [Line(self.crm.pk, 1, D("0.25"))]
        self.assertEqual(engine.evaluate(mk("Tiny", value="2"), lines, TODAY).amount, D("0.01"))


class EligibilityTests(EngineTestCase):
    def reason(self, d):
        return engine.ineligible_reason(d, self.lines, TODAY)

    def test_inactive(self):
        self.assertIn("switched off", self.reason(mk("Off", is_active=False)))

    def test_date_window_is_inclusive(self):
        self.assertIsNone(self.reason(mk("Start", start_date=TODAY)))
        self.assertIsNone(self.reason(mk("End", end_date=TODAY)))

    def test_not_started_and_expired(self):
        self.assertIn(
            "starts on", self.reason(mk("Soon", start_date=TODAY + datetime.timedelta(days=1)))
        )
        self.assertIn(
            "ended on", self.reason(mk("Old", end_date=TODAY - datetime.timedelta(days=1)))
        )

    def test_usage_limit(self):
        self.assertIn("usage limit", self.reason(mk("Used up", usage_limit=2, used_count=2)))
        self.assertIsNone(self.reason(mk("One left", usage_limit=2, used_count=1)))

    def test_minimum_quantity(self):
        self.assertIsNone(self.reason(mk("Q3", min_quantity=3)))  # exactly 3 units
        self.assertIn("Minimum quantity 4", self.reason(mk("Q4", min_quantity=4)))

    def test_minimum_purchase(self):
        self.assertIsNone(self.reason(mk("P2500", min_purchase=D("2500"))))
        self.assertIn("Minimum purchase", self.reason(mk("P2501", min_purchase=D("2500.01"))))

    def test_minimums_count_only_covered_lines(self):
        d = mk("CRM min", applies_to="product", products=[self.crm], min_purchase=D("2100"))
        self.assertIn("Minimum purchase", self.reason(d))  # CRM lines are 2000, POS does not count


class BestDiscountTests(EngineTestCase):
    def test_largest_saving_wins_and_no_stacking(self):
        ten = mk("Ten", value="10")  # 250
        flat = mk("Flat", "fixed", "400")  # 400
        crm = mk("CRM 15", value="15", applies_to="product", products=[self.crm])  # 300
        best = engine.best_discount([ten, flat, crm], self.lines, TODAY)
        self.assertEqual(best.discount, flat)
        self.assertEqual(best.amount, D("400.00"))

    def test_tie_goes_to_older_discount(self):
        first = mk("First", "fixed", "100")
        second = mk("Second", "fixed", "100")
        self.assertEqual(engine.best_discount([second, first], self.lines, TODAY).discount, first)

    def test_ineligible_ones_are_skipped(self):
        expired = mk("Expired", "fixed", "999", end_date=TODAY - datetime.timedelta(days=1))
        ok = mk("OK", "fixed", "50")
        self.assertEqual(engine.best_discount([expired, ok], self.lines, TODAY).discount, ok)

    def test_none_when_nothing_applies(self):
        self.assertIsNone(engine.best_discount([mk("Off", is_active=False)], self.lines, TODAY))
        self.assertIsNone(engine.best_discount([], self.lines, TODAY))

    def test_explain_gives_a_reason_for_each_rejection(self):
        rows = engine.explain([mk("Good"), mk("Q9", min_quantity=9)], self.lines, TODAY)
        self.assertIsNotNone(rows[0][1])
        self.assertIsNone(rows[0][2])
        self.assertIsNone(rows[1][1])
        self.assertIn("Minimum quantity 9", rows[1][2])


class ModelTests(EngineTestCase):
    def test_status_words(self):
        self.assertEqual(mk("A").status(TODAY), "active")
        self.assertEqual(mk("B", is_active=False).status(TODAY), "inactive")
        self.assertEqual(
            mk("C", start_date=TODAY + datetime.timedelta(days=2)).status(TODAY), "scheduled"
        )
        self.assertEqual(
            mk("D", end_date=TODAY - datetime.timedelta(days=2)).status(TODAY), "expired"
        )
        self.assertEqual(mk("E", usage_limit=1, used_count=1).status(TODAY), "exhausted")

    def test_labels(self):
        self.assertEqual(mk("P", value="12.50").value_label, "12.5%")
        self.assertEqual(mk("F", "fixed", "1500").value_label, "₹1,500.00")

    def test_database_constraints(self):
        bad = [
            dict(name="z", value=D("0")),
            dict(name="o", value=D("101")),
            dict(name="q", min_quantity=0),
            dict(name="d", start_date=TODAY, end_date=TODAY - datetime.timedelta(days=1)),
            dict(name="l", usage_limit=0),
            dict(name="u", usage_limit=1, used_count=2),
            dict(name="n", min_purchase=D("-1")),
        ]
        for fields in bad:
            fields.setdefault("value", D("10"))
            with self.assertRaises(IntegrityError, msg=fields["name"]), transaction.atomic():
                Discount.objects.create(discount_type="percentage", **fields)

    def test_name_unique_ignoring_case(self):
        mk("Spring")
        with self.assertRaises(IntegrityError), transaction.atomic():
            mk("SPRING")

    def test_fixed_may_exceed_100(self):
        mk("Big flat", "fixed", "500")


class RedeemTests(EngineTestCase):
    def test_redeem_counts_up_to_the_limit_then_refuses(self):
        d = mk("Limited", usage_limit=3)
        results = [services.redeem(d.pk) for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])
        d.refresh_from_db()
        self.assertEqual(d.used_count, 3)

    def test_unlimited_discount_always_redeems(self):
        d = mk("Unlimited")
        self.assertTrue(all(services.redeem(d.pk) for _ in range(4)))
        d.refresh_from_db()
        self.assertEqual(d.used_count, 4)

    def test_unknown_id(self):
        self.assertFalse(services.redeem(999999))


class RedeemConcurrencyTests(TransactionTestCase):
    def test_parallel_redemptions_never_exceed_the_limit(self):
        d = mk("Race", usage_limit=3)
        results, lock = [], threading.Lock()

        def worker():
            try:
                with transaction.atomic():
                    ok = services.redeem(d.pk)
                with lock:
                    results.append(ok)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        d.refresh_from_db()
        self.assertEqual(results.count(True), 3)
        self.assertEqual(d.used_count, 3)
