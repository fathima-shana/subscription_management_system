import datetime
from decimal import Decimal

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from apps.billing.models import Invoice
from apps.billing.periods import add_months
from apps.discounts.models import Discount
from apps.payments import services as payment_services
from apps.reports import filters as report_filters
from apps.reports import metrics

from .helpers import ReportTestCase

D = Decimal
d = datetime.date


class MrrTests(ReportTestCase):
    def test_each_period_is_normalised_to_a_month(self):
        self.active(plan=self.plan_of("monthly", "M"), quantity=1)  # 1000
        self.active(plan=self.plan_of("yearly", "Y"), quantity=12)  # 12000 / 12 = 1000
        self.active(plan=self.plan_of("weekly", "W"), quantity=1)  # 1000 x 52/12
        self.active(plan=self.plan_of("daily", "D"), quantity=1)  # 1000 x 365/12
        rows = {r["name"]: r["mrr"] for r in metrics.mrr_by_plan()}
        self.assertEqual(
            rows, {"M": D("1000.00"), "Y": D("1000.00"), "W": D("4333.33"), "D": D("30416.67")}
        )
        self.assertEqual(metrics.mrr(metrics.mrr_by_plan()), D("36750.00"))

    def test_only_active_subscriptions_count(self):
        self.active()
        self.make_sub()  # draft
        self.move(self.make_sub(), "quotation")
        self.move(self.make_sub(), "quotation", "confirmed")
        self.move(self.active(), "paused")
        self.move(self.active(), "closed")
        self.assertEqual(metrics.mrr(metrics.mrr_by_plan()), D("1000.00"))

    def test_uses_line_amounts_before_tax_and_discount(self):
        Discount.objects.create(name="Half", discount_type="percentage", value=D("50"))
        self.active(quantity=3)  # 3000, GST on the first line only
        rows = metrics.mrr_by_plan()
        self.assertEqual(
            (rows[0]["recurring"], rows[0]["mrr"], rows[0]["subscriptions"]),
            (D("3000.00"), D("3000.00"), 1),
        )

    def test_plan_filter(self):
        other = self.plan_of("monthly", "Other")
        self.active()
        self.active(plan=other, quantity=2)
        self.assertEqual(metrics.mrr(metrics.mrr_by_plan(other.pk)), D("2000.00"))
        self.assertEqual(metrics.mrr(metrics.mrr_by_plan(self.plan.pk)), D("1000.00"))

    def test_empty(self):
        self.assertEqual((metrics.mrr_by_plan(), metrics.mrr([])), ([], D("0")))

    def test_status_counts_cover_every_status(self):
        self.active()
        self.make_sub()
        counts = {s["status"]: s["count"] for s in metrics.status_counts()}
        self.assertEqual(
            counts,
            {"draft": 1, "quotation": 0, "confirmed": 0, "active": 1, "paused": 0, "closed": 0},
        )


class MoneyTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.active()
        self.bill()
        self.inv = Invoice.objects.get()  # 1180

    def test_invoiced_range_is_inclusive_and_skips_cancelled(self):
        self.assertEqual(
            metrics.invoiced(self.today, self.today), {"amount": D("1180.00"), "count": 1}
        )
        self.assertEqual(
            metrics.invoiced(self.days(1), self.days(5)), {"amount": D("0"), "count": 0}
        )
        self.assertEqual(metrics.invoiced(None, None)["count"], 1)
        from apps.billing import services as invoice_services

        invoice_services.cancel_invoice(actor=self.admin, invoice=self.inv)
        self.assertEqual(metrics.invoiced(None, None), {"amount": D("0"), "count": 0})

    def test_collected_excludes_voided_and_respects_dates(self):
        a = self.pay(self.inv, "400", date=self.days(-10))
        self.pay(self.inv, "100")
        payment_services.void_payment(actor=self.admin, payment=a)
        self.assertEqual(metrics.collected(None, None), D("100.00"))
        self.assertEqual(metrics.collected(self.days(-20), self.days(-5)), D("0"))
        self.assertEqual(metrics.collected(self.today, self.today), D("100.00"))

    def test_outstanding_and_overdue(self):
        self.pay(self.inv, "180")
        self.assertEqual(
            metrics.outstanding(self.today)["outstanding"], {"amount": D("1000.00"), "count": 1}
        )
        self.assertEqual(metrics.outstanding(self.today)["overdue"], {"amount": D("0"), "count": 0})
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.days(-3))
        self.assertEqual(
            metrics.outstanding(self.today)["overdue"], {"amount": D("1000.00"), "count": 1}
        )

    def test_plan_filter_applies_to_money(self):
        other = self.plan_of("monthly", "Other")
        self.assertEqual(metrics.invoiced(None, None, other.pk)["count"], 0)
        self.assertEqual(metrics.invoiced(None, None, self.plan.pk)["count"], 1)
        self.pay(self.inv, "50")
        self.assertEqual(metrics.collected(None, None, other.pk), D("0"))
        self.assertEqual(metrics.collected(None, None, self.plan.pk), D("50.00"))
        self.assertEqual(metrics.outstanding(self.today, other.pk)["outstanding"]["count"], 0)

    def test_payments_by_method(self):
        self.pay(self.inv, "300", "upi")
        self.pay(self.inv, "100", "cash")
        rows = metrics.payments_by_method(None, None)
        self.assertEqual([r["method"] for r in rows][:2], ["upi", "cash"])  # biggest first
        by = {r["method"]: r for r in rows}
        self.assertEqual(
            (by["upi"]["amount"], by["upi"]["pct"], by["upi"]["count"]), (D("300.00"), 75, 1)
        )
        self.assertEqual(
            (by["cash"]["pct"], by["card"]["amount"], by["bank"]["count"]), (25, D("0"), 0)
        )
        self.assertEqual(len(rows), 4)  # every method is always listed

    def test_overdue_list(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.days(-7))
        rows = metrics.overdue_list(self.today)
        self.assertEqual(
            (len(rows), rows[0]["days"], rows[0]["balance"], rows[0]["invoice"]),
            (1, 7, D("1180.00"), self.inv),
        )
        self.assertEqual(metrics.overdue_list(self.today, limit=0), [])

    def test_new_subscriptions(self):
        self.assertEqual(metrics.new_subscriptions(self.today, self.today), 1)
        self.assertEqual(metrics.new_subscriptions(self.days(1), None), 0)


class MonthlySeriesTests(ReportTestCase):
    def test_zero_filled_and_aligned_across_a_year_boundary(self):
        self.active()
        self.bill()
        inv = Invoice.objects.get()
        self.pay(inv, "100", date=self.today)
        this_month = self.today.replace(day=1)
        end = add_months(this_month, 3) + datetime.timedelta(
            days=9
        )  # a window that ends in a later year or month
        series = metrics.collected_by_month(end, 6)
        first = add_months(end.replace(day=1), -5)
        self.assertEqual([s["month"] for s in series], [add_months(first, i) for i in range(6)])
        self.assertEqual(
            {s["month"]: s["total"] for s in series if s["total"]}, {this_month: D("100.00")}
        )
        series = metrics.collected_by_month(self.today, 6)
        self.assertEqual(len(series), 6)
        last = series[-1]
        self.assertEqual(
            (last["month"], last["total"], last["pct"]),
            (self.today.replace(day=1), D("100.00"), 100),
        )
        self.assertEqual(sum(s["total"] for s in series[:-1]), 0)

    def test_small_values_still_get_a_visible_bar(self):
        self.active()
        self.bill()
        inv = Invoice.objects.get()
        self.pay(inv, "1000", date=self.today)
        self.pay(inv, "1", date=self.today - datetime.timedelta(days=45))
        series = metrics.collected_by_month(self.today, 3)
        self.assertTrue(any(0 < s["pct"] <= 3 for s in series))


class BuildReportTests(ReportTestCase):
    def test_build_report_keys_and_values(self):
        self.active()
        self.bill()
        self.pay(Invoice.objects.get(), "180", "upi")
        f = report_filters.parse({}, self.today)
        r = metrics.build_report(f, self.today)
        self.assertEqual((r["mrr"], r["active"], r["collected"]), (D("1000.00"), 1, D("180.00")))
        self.assertEqual(
            (r["invoiced"]["amount"], r["outstanding"]["amount"]), (D("1180.00"), D("1000.00"))
        )
        for key in (
            "status_counts",
            "new_subscriptions",
            "by_method",
            "by_month",
            "overdue_list",
            "mrr_rows",
            "overdue",
        ):
            self.assertIn(key, r)

    def test_query_count_does_not_grow_with_data(self):
        f = report_filters.parse({}, self.today)

        def count():
            with CaptureQueriesContext(connection) as ctx:
                metrics.build_report(f, self.today)
            return len(ctx)

        self.active()
        self.bill()
        baseline = count()
        for _ in range(6):
            self.active()
        self.bill()
        for inv in Invoice.objects.all()[:4]:
            self.pay(inv, "10")
        self.assertEqual(count(), baseline)


class FilterTests(ReportTestCase):
    T = d(2026, 11, 20)

    def parse(self, **params):
        return report_filters.parse(params, self.T)

    def test_presets(self):
        self.assertEqual((self.parse().start, self.parse().end), (d(2026, 11, 1), self.T))
        self.assertEqual(self.parse(preset="30d").start, d(2026, 10, 22))
        self.assertEqual(self.parse(preset="quarter").start, d(2026, 10, 1))
        self.assertEqual(
            report_filters.parse({"preset": "quarter"}, d(2026, 2, 5)).start, d(2026, 1, 1)
        )
        self.assertEqual(self.parse(preset="year").start, d(2026, 1, 1))
        f = self.parse(preset="all")
        self.assertEqual((f.start, f.end, f.label), (None, None, "All time"))

    def test_unknown_preset_falls_back(self):
        self.assertEqual(self.parse(preset="whenever").preset, "month")

    def test_custom_range(self):
        f = self.parse(preset="custom", **{"from": "2026-01-05", "to": "2026-02-10"})
        self.assertEqual(
            (f.preset, f.start, f.end, f.error), ("custom", d(2026, 1, 5), d(2026, 2, 10), "")
        )

    def test_bad_custom_ranges_fall_back_with_a_message(self):
        for params in (
            {"from": "x", "to": "y"},
            {"from": "2026-05-01", "to": "2026-04-01"},
            {"from": "2026-05-01"},
        ):
            f = self.parse(preset="custom", **params)
            self.assertEqual((f.preset, f.start), ("month", d(2026, 11, 1)), params)
            self.assertIn("valid date range", f.error)

    def test_plan_filter_ignores_garbage(self):
        self.assertEqual(self.parse(plan=str(self.plan.pk)).plan_id, self.plan.pk)
        for bad in ("abc", "999999", "-1", ""):
            self.assertIsNone(self.parse(plan=bad).plan_id, bad)

    def test_querystring_round_trips(self):
        f = self.parse(
            preset="custom", plan=str(self.plan.pk), **{"from": "2026-01-05", "to": "2026-02-10"}
        )
        from urllib.parse import parse_qs

        again = report_filters.parse({k: v[0] for k, v in parse_qs(f.querystring).items()}, self.T)
        self.assertEqual((again.start, again.end, again.plan_id), (f.start, f.end, f.plan_id))


class YearBoundaryTests(SimpleTestCase):
    def test_series_months_cross_the_new_year(self):
        from apps.reports.metrics import add_months as am

        first = am(d(2027, 2, 1), -5)
        self.assertEqual([am(first, i) for i in range(6)][0], d(2026, 9, 1))
        self.assertEqual([am(first, i) for i in range(6)][-1], d(2027, 2, 1))
