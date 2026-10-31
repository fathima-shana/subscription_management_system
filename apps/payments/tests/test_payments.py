import datetime
import threading
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Role
from apps.billing import engine
from apps.billing import services as invoice_services
from apps.billing.models import Invoice
from apps.core.models import AuditLog
from apps.core.testing import make_user
from apps.discounts.models import Discount
from apps.payments import selectors, services
from apps.payments.models import Payment
from apps.subscriptions.tests.helpers import SubsTestCase

D = Decimal


class PaymentTestCase(SubsTestCase):
    """One confirmed invoice of ₹1,180.00 (1000 + 18% GST)."""

    def setUp(self):
        super().setUp()
        self.sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        engine.run(today=self.today)
        self.inv = Invoice.objects.get()

    def pay(self, amount, method="cash", actor=None, **kw):
        return services.record_payment(
            actor=actor or self.staff, invoice=self.inv, amount=amount, method=method, **kw
        )

    def reload(self):
        self.inv.refresh_from_db()
        return self.inv


class ModelTests(PaymentTestCase):
    def test_database_rejects_zero_and_negative(self):
        for amount in (D("0"), D("-5")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Payment.objects.create(
                    invoice=self.inv, method="cash", amount=amount, payment_date=self.today
                )

    def test_database_rejects_unknown_method(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                invoice=self.inv, method="barter", amount=D("1"), payment_date=self.today
            )

    def test_database_stops_amount_paid_exceeding_total(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=self.inv.pk).update(amount_paid=self.inv.total + 1)


class RecordPaymentTests(PaymentTestCase):
    def test_full_payment_marks_invoice_paid(self):
        payment = self.pay("1180")
        inv = self.reload()
        self.assertEqual(
            (inv.status, inv.amount_paid, inv.balance), ("paid", D("1180.00"), D("0.00"))
        )
        self.assertEqual(
            (payment.method, payment.created_by, payment.payment_date),
            ("cash", self.staff, self.today),
        )
        entry = AuditLog.objects.get(action="payment.recorded")
        self.assertEqual((entry.details["amount"], entry.details["settled"]), ("1180.00", True))

    def test_partial_payments_add_up_then_settle(self):
        self.pay("500", "upi")
        inv = self.reload()
        self.assertEqual(
            (inv.status, inv.amount_paid, inv.balance), ("confirmed", D("500.00"), D("680.00"))
        )
        self.pay("400", "card")
        self.assertEqual((self.reload().status, self.inv.balance), ("confirmed", D("280.00")))
        self.pay("280", "bank")
        self.assertEqual((self.reload().status, self.inv.balance), ("paid", D("0.00")))
        self.assertEqual(self.inv.payments.count(), 3)

    def test_caller_object_is_kept_current(self):
        self.pay("1180")
        self.assertEqual((self.inv.status, self.inv.amount_paid), ("paid", D("1180.00")))

    def test_cannot_pay_more_than_the_balance(self):
        self.pay("1000")
        with self.assertRaises(services.PaymentError) as ctx:
            self.pay("180.01")
        self.assertIn("more than the balance due", str(ctx.exception))
        self.assertEqual(self.reload().amount_paid, D("1000.00"))
        self.assertEqual(self.inv.payments.count(), 1)

    def test_invalid_amounts(self):
        for bad in ("0", "-10", "abc", ""):
            with self.assertRaises(services.PaymentError, msg=bad):
                self.pay(bad)

    def test_amount_is_rounded_to_cents(self):
        self.pay("10.005")
        self.assertEqual(self.reload().amount_paid, D("10.01"))

    def test_unknown_method_and_future_date(self):
        with self.assertRaises(services.PaymentError):
            self.pay("10", "barter")
        with self.assertRaises(services.PaymentError):
            self.pay("10", payment_date=self.today + datetime.timedelta(days=1))

    def test_past_date_is_allowed(self):
        p = self.pay("10", payment_date=self.today - datetime.timedelta(days=3))
        self.assertEqual(p.payment_date, self.today - datetime.timedelta(days=3))

    def test_only_confirmed_invoices_can_be_paid(self):
        self.pay("1180")
        with self.assertRaises(services.PaymentError):
            self.pay("1")  # already paid
        other = self.move(self.make_sub(), "quotation", "confirmed", "active")
        engine.run(today=self.today)
        second = Invoice.objects.exclude(pk=self.inv.pk).get()
        invoice_services.cancel_invoice(actor=self.admin, invoice=second)
        second.refresh_from_db()
        with self.assertRaises(services.PaymentError):
            services.record_payment(actor=self.staff, invoice=second, amount="1", method="cash")

    def test_roles(self):
        self.pay("1", actor=self.admin)
        self.pay("1", actor=self.staff)
        with self.assertRaises(PermissionDenied):
            self.pay("1", actor=self.customer_user)
        self.assertEqual(Payment.objects.count(), 2)

    def test_note_is_stored_and_trimmed(self):
        self.assertEqual(self.pay("1", note="  UPI ref 123  ").note, "UPI ref 123")


class VoidTests(PaymentTestCase):
    def test_voiding_reopens_a_paid_invoice(self):
        p = self.pay("1180")
        services.void_payment(actor=self.admin, payment=p, reason="Entered twice")
        inv = self.reload()
        self.assertEqual((inv.status, inv.amount_paid), ("confirmed", D("0.00")))
        p.refresh_from_db()
        self.assertTrue(p.is_voided)
        self.assertEqual((p.voided_by, p.void_reason), (self.admin, "Entered twice"))
        self.assertEqual(
            AuditLog.objects.get(action="payment.voided").details["reason"], "Entered twice"
        )

    def test_voiding_one_of_two_keeps_the_other(self):
        a, b = self.pay("500"), self.pay("300")
        services.void_payment(actor=self.admin, payment=a)
        self.assertEqual((self.reload().amount_paid, self.inv.status), (D("300.00"), "confirmed"))

    def test_after_a_void_the_balance_can_be_paid_again(self):
        p = self.pay("1180")
        services.void_payment(actor=self.admin, payment=p)
        self.pay("1180", "bank")
        self.assertEqual(self.reload().status, "paid")

    def test_cannot_void_twice(self):
        p = self.pay("10")
        services.void_payment(actor=self.admin, payment=p)
        with self.assertRaises(services.PaymentError):
            services.void_payment(actor=self.admin, payment=p)

    def test_admin_only(self):
        p = self.pay("10")
        with self.assertRaises(PermissionDenied):
            services.void_payment(actor=self.staff, payment=p)
        with self.assertRaises(PermissionDenied):
            services.void_payment(actor=self.customer_user, payment=p)
        p.refresh_from_db()
        self.assertFalse(p.is_voided)

    def test_voided_payments_are_not_counted_as_collected(self):
        p = self.pay("400")
        self.pay("100")
        services.void_payment(actor=self.admin, payment=p)
        self.assertEqual(selectors.collected(), D("100.00"))

    def test_invoice_with_a_payment_still_cannot_be_cancelled(self):
        self.pay("10")
        with self.assertRaises(invoice_services.InvoiceError):
            invoice_services.cancel_invoice(actor=self.admin, invoice=self.reload())
        # but after voiding it, it can
        services.void_payment(actor=self.admin, payment=self.inv.payments.get())
        invoice_services.cancel_invoice(actor=self.admin, invoice=self.reload())


class OutstandingTests(PaymentTestCase):
    def test_totals(self):
        t = selectors.outstanding_totals(self.today)
        self.assertEqual(
            (t["outstanding"], t["overdue"]),
            ({"amount": D("1180.00"), "count": 1}, {"amount": D("0.00"), "count": 0}),
        )

    def test_part_payment_reduces_outstanding_by_the_balance_only(self):
        self.pay("500")
        self.assertEqual(
            selectors.outstanding_totals(self.today)["outstanding"],
            {"amount": D("680.00"), "count": 1},
        )

    def test_overdue_needs_a_past_due_date_and_money_owing(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=1)
        )
        self.assertEqual(
            selectors.outstanding_totals(self.today)["overdue"],
            {"amount": D("1180.00"), "count": 1},
        )
        self.assertEqual(list(selectors.overdue_invoices(self.today)), [self.inv])
        self.pay("1180")
        self.assertEqual(
            selectors.outstanding_totals(self.today)["overdue"], {"amount": D("0.00"), "count": 0}
        )
        self.assertEqual(
            selectors.outstanding_totals(self.today)["outstanding"],
            {"amount": D("0.00"), "count": 0},
        )

    def test_due_today_is_not_overdue(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today)
        self.assertEqual(selectors.outstanding_totals(self.today)["overdue"]["count"], 0)

    def test_cancelled_invoices_are_not_outstanding(self):
        invoice_services.cancel_invoice(actor=self.admin, invoice=self.inv)
        self.assertEqual(selectors.outstanding_totals(self.today)["outstanding"]["count"], 0)

    def test_collected_date_range(self):
        self.pay("100", payment_date=self.today - datetime.timedelta(days=40))
        self.pay("200")
        self.assertEqual(selectors.collected(self.today, self.today), D("200.00"))
        self.assertEqual(selectors.collected(), D("300.00"))


class ZeroTotalInvoiceTests(SubsTestCase):
    def test_fully_discounted_invoice_is_paid_on_creation(self):
        Discount.objects.create(name="Free", discount_type="percentage", value=D("100"))
        self.move(self.make_sub(), "quotation", "confirmed", "active")
        engine.run(today=self.today)
        inv = Invoice.objects.get()
        self.assertEqual((inv.total, inv.status, inv.balance), (D("0.00"), "paid", D("0.00")))
        self.assertEqual(selectors.outstanding_totals(self.today)["outstanding"]["count"], 0)


class EndToEndTests(PaymentTestCase):
    def test_bill_then_pay_in_parts(self):
        self.pay("1000", "bank", note="NEFT 998877")
        self.assertEqual(self.reload().status, "confirmed")
        self.pay("180", "upi")
        self.assertEqual(self.reload().status, "paid")
        # the next period is billed normally and is unpaid
        engine.run(today=self.today + datetime.timedelta(days=40))
        self.assertEqual(Invoice.objects.filter(status="confirmed").count(), 1)


class ConcurrentPaymentTests(TransactionTestCase):
    def test_parallel_payments_can_never_overpay(self):
        from apps.catalog.models import Plan, Product
        from apps.core.testing import make_people
        from apps.subscriptions import services as subscription_services
        from apps.subscriptions.models import PaymentTerm

        admin, staff, customer_user = make_people()
        term, _ = PaymentTerm.objects.get_or_create(name="30 days", defaults={"days": 30})
        plan = Plan.objects.create(name="Monthly", billing_period="monthly")
        product = Product.objects.create(name="CRM", sales_price=D("1000"))
        today = datetime.date(2026, 6, 15)
        sub = subscription_services.create_subscription(
            actor=staff,
            customer=customer_user.profile,
            plan=plan,
            payment_term=term,
            start_date=today,
        )
        subscription_services.add_line(
            actor=staff, subscription=sub, product=product, variant=None, quantity=1, tax=None
        )
        for to in ("quotation", "confirmed", "active"):
            subscription_services.transition(actor=staff, subscription=sub, to=to, today=today)
        engine.run(today=today)
        inv = Invoice.objects.get()
        outcomes, lock = [], threading.Lock()

        def worker():
            try:
                services.record_payment(
                    actor=staff, invoice=inv, amount="400", method="cash", payment_date=today
                )
                result = True
            except services.PaymentError:
                result = False
            finally:
                connection.close()
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        inv.refresh_from_db()
        self.assertEqual(outcomes.count(True), 2)  # 400 + 400 fits in 1000; a third does not
        self.assertEqual((inv.amount_paid, inv.status), (D("800.00"), "confirmed"))
        self.assertEqual(Payment.objects.count(), 2)


class AccessTests(PaymentTestCase):
    def code(self, user, name, method="get", *args, data=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_pages(self):
        for name, args in [("payments:list", ()), ("payments:create", (self.inv.pk,))]:
            self.assertEqual(self.code(None, name, "get", *args), 302, name)
            self.assertEqual(self.code(self.customer_user, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.staff, name, "get", *args), 200, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)

    def test_void_is_admin_only(self):
        p = self.pay("10")
        for user in (self.staff, self.customer_user):
            self.assertEqual(self.code(user, "payments:void", "post", p.pk), 403)
        p.refresh_from_db()
        self.assertFalse(p.is_voided)
        self.assertEqual(self.code(self.admin, "payments:void", "post", p.pk), 302)

    def test_void_needs_post(self):
        p = self.pay("10")
        self.assertEqual(self.code(self.admin, "payments:void", "get", p.pk), 405)

    def test_sidebar(self):
        self.client.force_login(self.staff)
        self.assertIn('href="/payments/"', self.client.get(reverse("core:home")).content.decode())

    def test_customer_scoping(self):
        p = self.pay("10")
        other = make_user("other@x.com", Role.CUSTOMER)
        self.assertEqual(set(selectors.payment_queryset(self.staff)), {p})
        self.assertEqual(set(selectors.payment_queryset(self.customer_user)), {p})
        self.assertEqual(set(selectors.payment_queryset(other)), set())

    def test_unknown_invoice_404(self):
        self.assertEqual(self.code(self.staff, "payments:create", "get", 99999), 404)


class FormViewTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.url = reverse("payments:create", args=[self.inv.pk])

    def data(self, **over):
        data = {
            "amount": "1180.00",
            "method": "upi",
            "payment_date": self.today.isoformat(),
            "note": "ref-1",
        }
        data.update(over)
        return data

    def test_form_defaults_to_the_balance_and_today(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('value="1180.00"', html)
        self.assertIn(f'value="{self.today.isoformat()}"', html)
        self.assertIn("Balance due", html)

    def test_full_payment_flow(self):
        r = self.client.post(self.url, self.data(), follow=True)
        self.assertContains(r, "is now paid in full")
        self.assertEqual(self.reload().status, "paid")
        self.assertContains(r, "ref-1")  # shown in the payments table on the invoice page

    def test_part_payment_message(self):
        r = self.client.post(self.url, self.data(amount="500"), follow=True)
        self.assertContains(r, "₹680.00 is still due")

    def test_validation_errors_shown(self):
        self.assertContains(
            self.client.post(self.url, self.data(amount="2000")), "more than the balance due"
        )
        self.assertContains(
            self.client.post(self.url, self.data(amount="0")), "greater than or equal to 0.01"
        )
        self.assertContains(self.client.post(self.url, self.data(method="")), "required")
        future = (self.today + datetime.timedelta(days=2)).isoformat()
        self.assertContains(
            self.client.post(self.url, self.data(payment_date=future)), "cannot be in the future"
        )
        self.assertEqual(Payment.objects.count(), 0)

    def test_paid_invoice_redirects_back_with_an_error(self):
        self.pay("1180")
        r = self.client.get(self.url, follow=True)
        self.assertContains(r, "cannot receive payments")

    def test_invoice_page_buttons_and_void(self):
        detail = reverse("invoices:detail", args=[self.inv.pk])
        self.assertIn("Record payment", self.client.get(detail).content.decode())
        p = self.pay("1180")
        html = self.client.get(detail).content.decode()
        self.assertNotIn("Record payment", html)
        self.assertNotIn(">Void<", html)  # staff cannot void
        self.client.force_login(self.admin)
        self.assertIn(">Void<", self.client.get(detail).content.decode())
        r = self.client.post(reverse("payments:void", args=[p.pk]), {"reason": "typo"}, follow=True)
        self.assertContains(r, "was voided")
        self.assertContains(r, "voided: typo")
        self.assertContains(r, "Record payment")  # reopened

    def test_overdue_badge_disappears_after_payment(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=5)
        )
        detail = reverse("invoices:detail", args=[self.inv.pk])
        self.assertIn("overdue", self.client.get(detail).content.decode())
        self.pay("1180")
        self.assertNotIn(">overdue<", self.client.get(detail).content.decode())


class ListTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_summary_cards(self):
        self.pay("500", "upi")
        html = self.client.get(reverse("payments:list")).content.decode()
        self.assertIn("₹680.00", html)  # outstanding
        self.assertIn("₹500.00", html)  # collected this month
        self.assertIn("1 open invoice", html)

    def test_overdue_card_links_to_overdue_invoices(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=1)
        )
        html = self.client.get(reverse("payments:list")).content.decode()
        self.assertIn("/invoices/?status=overdue", html)
        self.assertIn("1 invoice past due", html)

    def test_method_filter_and_search(self):
        self.pay("100", "upi", note="UPI-777")
        self.pay("200", "cash")
        html = self.client.get(reverse("payments:list"), {"method": "upi"}).content.decode()
        self.assertIn("UPI-777", html)
        self.assertNotIn("₹200.00</td>", html)
        self.assertIn(
            "UPI-777", self.client.get(reverse("payments:list"), {"q": "777"}).content.decode()
        )
        self.assertIn(
            "No payments match",
            self.client.get(reverse("payments:list"), {"q": "zzz"}).content.decode(),
        )
        self.assertEqual(
            self.client.get(reverse("payments:list"), {"method": "bogus"}).status_code, 200
        )

    def test_voided_payment_is_marked(self):
        p = self.pay("100")
        services.void_payment(actor=self.admin, payment=p)
        self.assertIn("voided", self.client.get(reverse("payments:list")).content.decode())

    def test_pagination_keeps_method_filter(self):
        for _ in range(17):
            Payment.objects.create(
                invoice=self.inv, method="cash", amount=D("1"), payment_date=self.today
            )
        html = self.client.get(reverse("payments:list"), {"method": "cash"}).content.decode()
        self.assertIn("Page 1 of 2", html)
        self.assertIn("page=2", html)

    def test_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("payments:list"))
            return len(ctx)

        self.pay("1")
        baseline = count()
        for _ in range(12):
            Payment.objects.create(
                invoice=self.inv,
                method="cash",
                amount=D("1"),
                payment_date=self.today,
                created_by=self.staff,
            )
        self.assertEqual(count(), baseline)

    def test_htmx_returns_fragment(self):
        self.pay("1")
        self.assertNotContains(
            self.client.get(reverse("payments:list"), HTTP_HX_REQUEST="true"), "<html"
        )
