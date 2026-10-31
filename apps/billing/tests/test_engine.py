import datetime
import threading
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from apps.accounts.models import Role
from apps.billing import engine
from apps.billing.models import Invoice
from apps.core.models import AuditLog
from apps.core.testing import make_user
from apps.discounts.models import Discount
from apps.subscriptions import services
from apps.subscriptions.models import Status
from apps.subscriptions.tests.helpers import SubsTestCase

D = Decimal
d = datetime.date


class EngineTestCase(SubsTestCase):
    def active(self, **over):
        return self.move(self.make_sub(**over), "quotation", "confirmed", "active")

    def reload(self, sub):
        sub.refresh_from_db()
        return sub


class BasicBillingTests(EngineTestCase):
    def test_bills_a_due_subscription(self):
        sub = self.active()
        result = engine.run(today=self.today)
        self.assertEqual(len(result.created), 1)
        inv = Invoice.objects.get()
        self.assertEqual(inv.number, "INV-00001")
        self.assertEqual(inv.status, "confirmed")
        self.assertEqual(
            (inv.subtotal, inv.discount_total, inv.tax_total, inv.total),
            (D("1000.00"), D("0.00"), D("180.00"), D("1180.00")),
        )
        self.assertEqual((inv.period_start, inv.issue_date), (self.today, self.today))
        self.assertEqual(inv.due_date, self.today + datetime.timedelta(days=30))  # "30 days" term
        self.assertEqual(
            inv.period_end,
            engine.next_period_start(self.today, "monthly", self.today)
            - datetime.timedelta(days=1),
        )
        self.assertEqual(
            self.reload(sub).next_billing_date,
            engine.next_period_start(self.today, "monthly", self.today),
        )

    def test_lines_snapshot_prices_and_tax(self):
        self.active()
        engine.run(today=self.today)
        line = Invoice.objects.get().lines.get()
        self.assertEqual(
            (line.description, line.quantity, line.unit_price, line.amount),
            ("CRM", 1, D("1000.00"), D("1000.00")),
        )
        self.assertEqual(
            (line.tax_name, line.tax_type, line.tax_rate, line.tax_amount, line.total),
            ("GST 18", "percentage", D("18.00"), D("180.00"), D("1180.00")),
        )

    def test_invoice_is_unaffected_by_later_changes(self):
        self.active()
        engine.run(today=self.today)
        self.crm.sales_price = D("9999")
        self.crm.name = "Renamed"
        self.crm.save()
        self.gst.rate = D("5")
        self.gst.save()
        self.customer.company_name = "New Name Ltd"
        self.customer.save()
        inv = Invoice.objects.get()
        line = inv.lines.get()
        self.assertEqual(
            (inv.total, line.description, line.tax_rate, inv.bill_to_name),
            (D("1180.00"), "CRM", D("18.00"), "Cara Customer Co"),
        )

    def test_customer_details_are_copied(self):
        self.customer.billing_address = "12 MG Road"
        self.customer.tax_id = "29ABCDE1234F1Z5"
        self.customer.save()
        self.active()
        engine.run(today=self.today)
        inv = Invoice.objects.get()
        self.assertEqual(
            (inv.bill_to_address, inv.bill_to_tax_id, inv.bill_to_email),
            ("12 MG Road", "29ABCDE1234F1Z5", "cust@x.com"),
        )

    def test_only_active_and_due_subscriptions_are_billed(self):
        future = self.active(start_date=self.today + datetime.timedelta(days=3))
        draft = self.make_sub()
        quote = self.move(self.make_sub(), "quotation")
        confirmed = self.move(self.make_sub(), "quotation", "confirmed")
        paused = self.move(self.active(), "paused")
        result = engine.run(today=self.today)
        self.assertEqual(result.created, [])
        self.assertEqual(Invoice.objects.count(), 0)
        engine.run(today=self.today + datetime.timedelta(days=3))
        self.assertEqual(Invoice.objects.get().subscription, future)

    def test_run_is_recorded_in_the_audit_log(self):
        self.active()
        engine.run(today=self.today, actor=self.admin)
        entry = AuditLog.objects.get(action="billing.run")
        self.assertEqual(
            (entry.details["invoices"], entry.details["total"], entry.actor),
            (1, "1180.00", self.admin),
        )


class IdempotencyTests(EngineTestCase):
    def test_running_twice_bills_once(self):
        self.active()
        engine.run(today=self.today)
        second = engine.run(today=self.today)
        self.assertEqual((len(second.created), Invoice.objects.count()), (0, 1))

    def test_database_refuses_a_second_invoice_for_a_period(self):
        sub = self.active()
        engine.run(today=self.today)
        inv = Invoice.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(
                number="INV-X",
                subscription=sub,
                customer=self.customer,
                period_start=inv.period_start,
                period_end=inv.period_end,
                issue_date=self.today,
                due_date=self.today,
                bill_to_name="x",
                plan_name="p",
                payment_term_name="t",
                subtotal=0,
                total=0,
            )

    def test_already_billed_period_only_moves_the_date_on(self):
        sub = self.active()
        engine.run(today=self.today)
        Invoice.objects.get()
        sub = self.reload(sub)
        original_next = sub.next_billing_date
        sub.next_billing_date = (
            self.today
        )  # simulate a crash after the invoice but before the date moved
        sub.save()
        result = engine.run(today=self.today)
        self.assertEqual(
            (result.created, result.healed, Invoice.objects.count()), ([], [sub.number], 1)
        )
        self.assertEqual(self.reload(sub).next_billing_date, original_next)

    def test_cancelled_invoice_period_is_not_billed_again(self):
        from apps.billing import services as invoice_services

        self.active()
        engine.run(today=self.today)
        invoice_services.cancel_invoice(actor=self.admin, invoice=Invoice.objects.get())
        engine.run(today=self.today)
        self.assertEqual(Invoice.objects.count(), 1)


class CatchUpTests(EngineTestCase):
    def test_missed_periods_are_billed_one_by_one_on_the_anchor_day(self):
        sub = self.active(start_date=d(2026, 1, 31))
        result = engine.run(today=d(2026, 4, 15))
        starts = list(
            Invoice.objects.order_by("period_start").values_list("period_start", flat=True)
        )
        self.assertEqual(starts, [d(2026, 1, 31), d(2026, 2, 28), d(2026, 3, 31)])
        ends = list(Invoice.objects.order_by("period_start").values_list("period_end", flat=True))
        self.assertEqual(ends, [d(2026, 2, 27), d(2026, 3, 30), d(2026, 4, 29)])
        self.assertEqual(self.reload(sub).next_billing_date, d(2026, 4, 30))
        self.assertEqual(len(result.created), 3)

    def test_catch_up_is_capped_per_run_and_resumes_next_run(self):
        self.plan.billing_period = "daily"
        self.plan.save()
        sub = self.active(start_date=d(2026, 1, 1))
        with mock.patch.object(engine, "MAX_PERIODS_PER_RUN", 5):
            engine.run(today=d(2026, 1, 12))
            self.assertEqual(Invoice.objects.count(), 5)
            engine.run(today=d(2026, 1, 12))
        self.assertEqual(Invoice.objects.count(), 10)
        with mock.patch.object(engine, "MAX_PERIODS_PER_RUN", 5):
            engine.run(today=d(2026, 1, 12))
        self.assertEqual(Invoice.objects.count(), 12)  # Jan 1 .. Jan 12
        self.assertEqual(self.reload(sub).next_billing_date, d(2026, 1, 13))

    def test_numbers_have_no_gaps(self):
        self.active(start_date=d(2026, 1, 31))
        engine.run(today=d(2026, 4, 15))
        self.assertEqual(
            list(Invoice.objects.order_by("id").values_list("number", flat=True)),
            ["INV-00001", "INV-00002", "INV-00003"],
        )


class DiscountBillingTests(EngineTestCase):
    def discount(self, **over):
        fields = dict(name="Ten", discount_type="percentage", value=D("10"))
        fields.update(over)
        return Discount.objects.create(**fields)

    def test_discount_is_applied_snapshotted_and_redeemed(self):
        ten = self.discount()
        self.active()
        engine.run(today=self.today)
        inv = Invoice.objects.get()
        self.assertEqual(
            (inv.discount, inv.discount_name, inv.discount_total, inv.tax_total, inv.total),
            (ten, "Ten", D("100.00"), D("162.00"), D("1062.00")),
        )
        self.assertEqual(inv.lines.get().discount_amount, D("100.00"))
        ten.refresh_from_db()
        self.assertEqual(ten.used_count, 1)

    def test_usage_limit_is_never_exceeded_across_invoices(self):
        limited = self.discount(usage_limit=2)
        for _ in range(4):
            self.active()
        engine.run(today=self.today)
        limited.refresh_from_db()
        self.assertEqual(limited.used_count, 2)
        self.assertEqual(
            Invoice.objects.filter(discount=limited).count(), 2
        )  # usage matches invoices
        self.assertEqual(Invoice.objects.filter(discount__isnull=True).count(), 2)

    def test_lost_race_for_the_last_redemption_falls_back_to_the_next_best(self):
        self.discount(name="Big", value=D("20"))
        small = self.discount(name="Small", value=D("5"))
        self.active()
        real = engine.redeem
        calls = []

        def fake(pk):
            calls.append(pk)
            return False if len(calls) == 1 else real(pk)

        with mock.patch.object(engine, "redeem", side_effect=fake):
            engine.run(today=self.today)
        inv = Invoice.objects.get()
        self.assertEqual((inv.discount, len(calls)), (small, 2))
        self.assertEqual(Discount.objects.get(name="Big").used_count, 0)

    def test_discount_valid_on_the_period_start_is_used_when_catching_up(self):
        self.discount(name="Spring", start_date=d(2026, 3, 1), end_date=d(2026, 3, 31))
        self.active(start_date=d(2026, 2, 1))
        engine.run(today=d(2026, 4, 5))
        by_period = {i.period_start: i.discount_name for i in Invoice.objects.all()}
        self.assertEqual(by_period, {d(2026, 2, 1): "", d(2026, 3, 1): "Spring", d(2026, 4, 1): ""})


class FailureIsolationTests(EngineTestCase):
    def test_one_bad_subscription_does_not_stop_the_run(self):
        good = self.active()
        bad = self.active()
        bad.lines.all().delete()  # a subscription that somehow lost its lines
        result = engine.run(today=self.today)
        self.assertEqual([c[2] for c in result.created], [good.number])
        self.assertEqual(result.failed, [(bad.number, "The subscription has no product lines.")])
        bad = self.reload(bad)
        self.assertEqual(bad.next_billing_date, self.today)  # untouched, will be retried

    def test_failure_after_the_invoice_rolls_everything_back(self):
        ten = Discount.objects.create(name="Ten", discount_type="percentage", value=D("10"))
        self.plan.auto_close = True
        self.plan.save()
        sub = self.active(
            expiry_date=self.today
        )  # this is the last period, so the engine will close it
        with mock.patch.object(
            engine.subscription_services, "transition", side_effect=RuntimeError("boom")
        ):
            result = engine.run(today=self.today)
        self.assertEqual(Invoice.objects.count(), 0)  # no invoice left behind
        ten.refresh_from_db()
        self.assertEqual(ten.used_count, 0)  # discount not consumed
        self.assertEqual(self.reload(sub).next_billing_date, self.today)
        self.assertEqual(result.created, [])
        self.assertIn("boom", result.failed[0][1])
        # and the next run succeeds
        ok = engine.run(today=self.today)
        self.assertEqual(len(ok.created), 1)


class EndOfSubscriptionTests(EngineTestCase):
    def test_auto_close_plan_closes_after_the_last_period(self):
        self.plan.auto_close = True
        self.plan.closable = False  # the system must still be able to close it
        self.plan.save()
        sub = self.active(expiry_date=self.today + datetime.timedelta(days=10))
        result = engine.run(today=self.today)
        sub = self.reload(sub)
        self.assertEqual(
            (sub.status, sub.next_billing_date, result.closed), ("closed", None, [sub.number])
        )
        self.assertEqual(sub.history.last().note, "Reached end date")
        self.assertEqual(Invoice.objects.count(), 1)

    def test_plan_without_auto_close_stops_billing_but_stays_active(self):
        sub = self.active(expiry_date=self.today + datetime.timedelta(days=10))
        result = engine.run(today=self.today)
        sub = self.reload(sub)
        self.assertEqual(
            (sub.status, sub.next_billing_date, result.ended), ("active", None, [sub.number])
        )
        engine.run(today=self.today + datetime.timedelta(days=90))
        self.assertEqual(Invoice.objects.count(), 1)  # never billed beyond the expiry

    def test_multiple_periods_until_expiry(self):
        self.plan.auto_close = True
        self.plan.save()
        sub = self.active(start_date=d(2026, 1, 1), expiry_date=d(2026, 3, 31))
        engine.run(today=d(2026, 6, 1))
        self.assertEqual(Invoice.objects.count(), 3)  # Jan, Feb, Mar
        self.assertEqual(self.reload(sub).status, "closed")

    def test_due_date_already_past_expiry_is_finished_without_an_invoice(self):
        self.plan.auto_close = True
        self.plan.save()
        sub = self.active(start_date=d(2026, 1, 1), expiry_date=d(2026, 1, 20))
        Invoice.objects.count()
        type(sub).objects.filter(pk=sub.pk).update(next_billing_date=d(2026, 2, 1))
        engine.run(today=d(2026, 2, 2))
        self.assertEqual((self.reload(sub).status, Invoice.objects.count()), ("closed", 0))

    def test_expired_subscriptions_on_auto_close_plans_are_closed_even_if_not_due(self):
        self.plan.auto_close = True
        self.plan.save()
        active = self.active(start_date=self.days(-40))
        type(active).objects.filter(pk=active.pk).update(
            expiry_date=self.days(-1), next_billing_date=self.days(20)
        )
        paused = self.move(self.active(start_date=self.days(-40)), "paused")
        type(paused).objects.filter(pk=paused.pk).update(
            expiry_date=self.today - datetime.timedelta(days=2)
        )
        result = engine.run(today=self.today)
        self.assertEqual(sorted(result.closed), sorted([active.number, paused.number]))
        self.assertEqual(self.reload(active).status, "closed")
        self.assertEqual(self.reload(paused).status, "closed")

    def test_plans_without_auto_close_are_left_alone(self):
        sub = self.active(start_date=self.days(-40))
        type(sub).objects.filter(pk=sub.pk).update(
            expiry_date=self.days(-1), next_billing_date=self.days(20)
        )
        engine.run(today=self.today)
        self.assertEqual(self.reload(sub).status, "active")


class ConcurrentRunTests(TransactionTestCase):
    def test_parallel_runs_never_bill_a_subscription_twice(self):
        from apps.catalog.models import Plan, Product
        from apps.core.testing import make_people
        from apps.subscriptions.models import PaymentTerm

        admin, staff, customer_user = make_people()
        term, _ = PaymentTerm.objects.get_or_create(name="30 days", defaults={"days": 30})
        plan = Plan.objects.create(name="Monthly", billing_period="monthly")
        product = Product.objects.create(name="CRM", sales_price=D("100"))
        today = datetime.date(2026, 6, 15)
        for _ in range(6):
            sub = services.create_subscription(
                actor=staff,
                customer=customer_user.profile,
                plan=plan,
                payment_term=term,
                start_date=today,
            )
            services.add_line(
                actor=staff, subscription=sub, product=product, variant=None, quantity=1, tax=None
            )
            for to in ("quotation", "confirmed", "active"):
                services.transition(actor=staff, subscription=sub, to=to, today=today)

        def worker():
            try:
                engine.run(today=today)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Invoice.objects.count(), 6)
        self.assertEqual(Invoice.objects.values("subscription").distinct().count(), 6)
        # a final run finds nothing left to do
        self.assertEqual(engine.run(today=today).created, [])
