from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import CustomerProfile, Role, User
from apps.billing import engine
from apps.billing.models import Invoice, InvoiceLine
from apps.core.models import AuditLog
from apps.core.testing import make_user
from apps.demo import integrity, seed
from apps.discounts.models import Discount
from apps.payments import services as payment_services
from apps.payments.models import Payment
from apps.subscriptions.models import Subscription


def run(cmd, *args):
    out, err = StringIO(), StringIO()
    call_command(cmd, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class SeedTests(TestCase):
    def test_seed_creates_consistent_data(self):
        out, _ = run("seed_demo_data", "--subscriptions", "120")
        self.assertIn("Integrity check passed", out)
        self.assertEqual(Subscription.objects.count(), 120)
        self.assertEqual(CustomerProfile.objects.count(), 30)
        self.assertGreater(Invoice.objects.count(), 120)
        self.assertGreater(Payment.objects.count(), 50)
        self.assertEqual(integrity.run_checks(), [])
        self.assertTrue(AuditLog.objects.filter(action="demo.seeded").exists())

    def test_every_status_and_period_is_represented(self):
        run("seed_demo_data", "--subscriptions", "400")
        statuses = set(Subscription.objects.values_list("status", flat=True))
        self.assertEqual(
            statuses, {"draft", "quotation", "confirmed", "active", "paused", "closed"}
        )
        periods = set(Subscription.objects.values_list("plan__billing_period", flat=True))
        self.assertEqual(periods, {"daily", "weekly", "monthly", "yearly"})
        invoice_statuses = set(Invoice.objects.values_list("status", flat=True))
        self.assertTrue({"paid", "confirmed"} <= invoice_statuses)

    def test_same_seed_gives_the_same_data(self):
        run("seed_demo_data", "--subscriptions", "80", "--seed", "7")
        first = (
            Invoice.objects.count(),
            Payment.objects.count(),
            sum(Invoice.objects.values_list("total", flat=True)),
        )
        run("seed_demo_data", "--subscriptions", "80", "--seed", "7", "--reset-demo")
        second = (
            Invoice.objects.count(),
            Payment.objects.count(),
            sum(Invoice.objects.values_list("total", flat=True)),
        )
        self.assertEqual(first, second)

    def test_refuses_to_load_twice_without_reset(self):
        run("seed_demo_data", "--subscriptions", "20")
        with self.assertRaises(CommandError):
            run("seed_demo_data", "--subscriptions", "20")
        self.assertEqual(Subscription.objects.count(), 20)

    def test_reset_removes_only_demo_data(self):
        real = make_user("real@company.com", Role.CUSTOMER, "Real Customer")
        run("seed_demo_data", "--subscriptions", "40")
        run("seed_demo_data", "--subscriptions", "40", "--reset-demo")
        self.assertEqual(Subscription.objects.count(), 40)
        self.assertTrue(User.objects.filter(pk=real.pk).exists())
        self.assertEqual(
            User.objects.filter(email__endswith="@demo.example", role="customer").count(), 10
        )

    def test_demo_logins_work(self):
        run("seed_demo_data", "--subscriptions", "20")
        from django.test import Client

        for email in ("admin@demo.example", "staff@demo.example", "customer00001@demo.example"):
            self.assertTrue(Client().login(username=email, password=seed.DEMO_PASSWORD), email)

    def test_numbers_continue_after_seeding(self):
        run("seed_demo_data", "--subscriptions", "30")
        last = Invoice.objects.order_by("-id").first().number
        staff = User.objects.get(email="staff@demo.example")
        from apps.subscriptions import services as ss

        sub = Subscription.objects.filter(status="active").first()
        new = ss.create_subscription(
            actor=staff,
            customer=sub.customer,
            plan=sub.plan,
            payment_term=sub.payment_term,
            start_date=timezone.localdate(),
        )
        self.assertEqual(int(new.number.split("-")[1]), Subscription.objects.count())
        self.assertGreater(int(last.split("-")[1]), 0)

    def test_billing_engine_works_on_seeded_data_and_books_still_add_up(self):
        run("seed_demo_data", "--subscriptions", "300")
        today = timezone.localdate()
        due = engine.due_subscriptions(today).count()
        self.assertGreater(due, 0)  # a few are deliberately due now, for the demo
        before = Invoice.objects.count()
        result = engine.run(today=today)
        self.assertEqual((len(result.created), result.failed), (due, []))
        self.assertEqual(Invoice.objects.count(), before + due)
        self.assertEqual(engine.run(today=today).created, [])  # idempotent on a big dataset too
        self.assertEqual(integrity.run_checks(), [])

    def test_payments_on_seeded_invoices_keep_the_books_balanced(self):
        run("seed_demo_data", "--subscriptions", "120")
        staff = User.objects.get(email="staff@demo.example")
        admin = User.objects.get(email="admin@demo.example")
        invoice = Invoice.objects.filter(status="confirmed", amount_paid=0).first()
        payment = payment_services.record_payment(
            actor=staff, invoice=invoice, amount=invoice.total, method="upi"
        )
        self.assertEqual(integrity.run_checks(), [])
        payment_services.void_payment(actor=admin, payment=payment, reason="test")
        self.assertEqual(integrity.run_checks(), [])


class IntegrityTests(TestCase):
    """The checker must actually catch problems, otherwise 'all checks passed' means nothing."""

    def setUp(self):
        run("seed_demo_data", "--subscriptions", "60")
        self.assertEqual(integrity.run_checks(), [])

    def problems(self):
        return " | ".join(integrity.run_checks())

    def test_detects_a_wrong_invoice_total(self):
        inv = Invoice.objects.filter(total__gt=0).first()
        Invoice.objects.filter(pk=inv.pk).update(total=inv.total + 1)
        self.assertIn("not subtotal - discount + tax", self.problems())

    def test_detects_totals_that_differ_from_lines(self):
        inv = Invoice.objects.filter(total__gt=0).first()
        InvoiceLine.objects.filter(invoice=inv).update(amount=Decimal("0.01"))
        self.assertIn("differs from the sum of its lines", self.problems())

    def test_detects_amount_paid_out_of_step_with_payments(self):
        pay = Payment.objects.first()
        Payment.objects.filter(pk=pay.pk).update(amount=pay.amount - Decimal("0.01"))
        self.assertIn("amount_paid differs", self.problems())

    def test_detects_status_that_contradicts_the_payments(self):
        inv = Invoice.objects.filter(status="paid").first()
        Invoice.objects.filter(pk=inv.pk).update(status="confirmed")
        self.assertIn("Confirmed invoice that is already fully paid", self.problems())

    def test_detects_discount_usage_drift(self):
        d = Discount.objects.filter(name="Welcome 10%").first()
        Discount.objects.filter(pk=d.pk).update(used_count=d.used_count + 5)
        self.assertIn("used_count", self.problems())

    def test_detects_an_inactive_looking_active_subscription(self):
        sub = Subscription.objects.filter(status="closed").first()
        Subscription.objects.filter(pk=sub.pk).update(next_billing_date=timezone.localdate())
        self.assertIn("Closed subscription that still has a next billing date", self.problems())

    def test_verify_command(self):
        out, _ = run("verify_data")
        self.assertIn("All checks passed", out)
        inv = Invoice.objects.filter(total__gt=0).first()
        Invoice.objects.filter(pk=inv.pk).update(total=inv.total + 1)
        with self.assertRaises(CommandError):
            run("verify_data")


class PerfCheckTests(TestCase):
    def test_perf_check_runs_and_all_pages_return_200(self):
        run("seed_demo_data", "--subscriptions", "80")
        out, _ = run("perf_check", "--runs", "1")
        self.assertIn("pages are under 2000 ms", out)
        self.assertIn("Dashboard", out)
        self.assertIn("CSV export: invoices", out)
        self.assertNotIn("HTTP 500", out)

    def test_markdown_and_explain_and_billing_options(self):
        run("seed_demo_data", "--subscriptions", "80")
        before = Invoice.objects.count()
        out, _ = run("perf_check", "--runs", "1", "--markdown", "--explain", "--billing")
        self.assertIn("| Page | Time (ms)", out)
        self.assertIn("EXPLAIN ANALYZE", out)
        self.assertIn("Billing run (rolled back)", out)
        self.assertEqual(Invoice.objects.count(), before)  # the billing timing saved nothing

    def test_a_tiny_limit_flags_pages_as_slow(self):
        run("seed_demo_data", "--subscriptions", "40")
        with self.assertRaises(CommandError):
            run("perf_check", "--runs", "1", "--limit-ms", "0")

    def test_needs_an_admin(self):
        with self.assertRaises(CommandError):
            run("perf_check")
