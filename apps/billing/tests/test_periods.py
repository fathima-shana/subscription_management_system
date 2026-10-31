import datetime

from django.test import SimpleTestCase

from apps.billing.periods import add_months, next_period_start, period_end

d = datetime.date


class PeriodTests(SimpleTestCase):
    def test_monthly_stays_anchored_to_the_start_day(self):
        anchor = d(2026, 1, 31)
        step1 = next_period_start(anchor, "monthly", anchor)
        step2 = next_period_start(step1, "monthly", anchor)
        step3 = next_period_start(step2, "monthly", anchor)
        self.assertEqual((step1, step2, step3), (d(2026, 2, 28), d(2026, 3, 31), d(2026, 4, 30)))

    def test_leap_february(self):
        self.assertEqual(
            next_period_start(d(2028, 1, 31), "monthly", d(2028, 1, 31)), d(2028, 2, 29)
        )

    def test_year_rollover(self):
        self.assertEqual(add_months(d(2026, 12, 15), 1), d(2027, 1, 15))
        self.assertEqual(add_months(d(2026, 1, 15), -1), d(2025, 12, 15))

    def test_yearly_from_leap_day(self):
        anchor = d(2028, 2, 29)
        y1 = next_period_start(anchor, "yearly", anchor)
        y4 = next_period_start(
            next_period_start(next_period_start(y1, "yearly", anchor), "yearly", anchor),
            "yearly",
            anchor,
        )
        self.assertEqual((y1, y4), (d(2029, 2, 28), d(2032, 2, 29)))

    def test_daily_and_weekly(self):
        self.assertEqual(next_period_start(d(2026, 3, 31), "daily", d(2026, 3, 31)), d(2026, 4, 1))
        self.assertEqual(next_period_start(d(2026, 3, 31), "weekly", d(2026, 3, 31)), d(2026, 4, 7))

    def test_period_end_is_the_day_before_the_next_start(self):
        self.assertEqual(period_end(d(2026, 1, 1), "monthly", d(2026, 1, 1)), d(2026, 1, 31))
        self.assertEqual(period_end(d(2026, 1, 1), "yearly", d(2026, 1, 1)), d(2026, 12, 31))
        self.assertEqual(period_end(d(2026, 1, 1), "daily", d(2026, 1, 1)), d(2026, 1, 1))

    def test_unknown_period(self):
        with self.assertRaises(ValueError):
            next_period_start(d(2026, 1, 1), "hourly", d(2026, 1, 1))
