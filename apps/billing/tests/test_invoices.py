import datetime
from decimal import Decimal
from io import StringIO

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Role
from apps.billing import engine, selectors, services
from apps.billing.models import Invoice
from apps.core.models import AuditLog
from apps.core.testing import make_user
from apps.subscriptions.tests.helpers import SubsTestCase

D = Decimal


class InvoiceTestCase(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        engine.run(today=self.today)
        self.inv = Invoice.objects.get()


class AccessTests(InvoiceTestCase):
    def code(self, user, name, method="get", *args):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args)).status_code

    def test_read_pages_internal_only(self):
        for name, args in [
            ("invoices:list", ()),
            ("invoices:detail", (self.inv.pk,)),
            ("invoices:print", (self.inv.pk,)),
        ]:
            self.assertEqual(self.code(None, name, "get", *args), 302, name)
            self.assertEqual(self.code(self.customer_user, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.staff, name, "get", *args), 200, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)

    def test_cancel_is_admin_only_and_billing_page_is_admin_only(self):
        self.assertEqual(self.code(self.staff, "invoices:cancel", "post", self.inv.pk), 403)
        self.assertEqual(self.code(self.customer_user, "invoices:cancel", "post", self.inv.pk), 403)
        self.assertEqual(self.code(self.staff, "billing:home"), 403)
        self.assertEqual(self.code(self.staff, "billing:run", "post"), 403)
        self.assertEqual(self.code(self.admin, "billing:home"), 200)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "confirmed")

    def test_sidebar(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn('href="/invoices/"', html)
        self.assertNotIn('href="/billing/"', html)
        self.client.force_login(self.admin)
        self.assertIn('href="/billing/"', self.client.get(reverse("core:home")).content.decode())

    def test_run_requires_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("billing:run")).status_code, 405)

    def test_unknown_invoice_404(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("invoices:detail", args=[99999])).status_code, 404)


class ListAndDetailTests(InvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_list_shows_invoice(self):
        html = self.client.get(reverse("invoices:list")).content.decode()
        self.assertIn(self.inv.number, html)
        self.assertIn("₹1,180.00", html)

    def test_overdue_is_derived_from_the_due_date(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=1)
        )
        html = self.client.get(reverse("invoices:list"), {"status": "overdue"}).content.decode()
        self.assertIn(self.inv.number, html)
        self.assertIn("overdue", html)
        self.assertNotIn(
            self.inv.number,
            self.client.get(reverse("invoices:list"), {"status": "paid"}).content.decode(),
        )
        # not overdue when the due date is today or later
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today)
        self.assertNotIn(
            self.inv.number,
            self.client.get(reverse("invoices:list"), {"status": "overdue"}).content.decode(),
        )

    def test_paid_invoice_is_never_overdue(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            status="paid",
            amount_paid=self.inv.total,
            due_date=self.today - datetime.timedelta(days=9),
        )
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.display_status(self.today), "paid")

    def test_search_and_filters(self):
        self.assertIn(
            self.inv.number,
            self.client.get(reverse("invoices:list"), {"q": "cara"}).content.decode(),
        )
        self.assertIn(
            self.sub.number,
            self.client.get(reverse("invoices:list"), {"q": self.sub.number}).content.decode(),
        )
        self.assertIn(
            "No invoices match",
            self.client.get(reverse("invoices:list"), {"q": "zzz"}).content.decode(),
        )
        self.assertIn(
            self.inv.number,
            self.client.get(reverse("invoices:list"), {"status": "confirmed"}).content.decode(),
        )
        self.assertIn(
            "No invoices match",
            self.client.get(reverse("invoices:list"), {"status": "cancelled"}).content.decode(),
        )
        self.assertEqual(
            self.client.get(reverse("invoices:list"), {"status": "bogus"}).status_code, 200
        )  # ignored

    def test_pagination_keeps_filter(self):
        for i in range(17):
            Invoice.objects.create(
                number=f"INV-9{i:04d}",
                subscription=self.sub,
                customer=self.customer,
                period_start=self.today + datetime.timedelta(days=100 + i),
                period_end=self.today + datetime.timedelta(days=100 + i),
                issue_date=self.today,
                due_date=self.today,
                bill_to_name="x",
                plan_name="p",
                payment_term_name="t",
                subtotal=0,
                total=0,
            )
        html = self.client.get(reverse("invoices:list"), {"status": "confirmed"}).content.decode()
        self.assertIn("Page 1 of 2", html)
        self.assertIn("status=confirmed&page=2", html)

    def test_list_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("invoices:list"))
            return len(ctx)

        baseline = count()
        for i in range(10):
            Invoice.objects.create(
                number=f"INV-8{i:04d}",
                subscription=self.sub,
                customer=self.customer,
                period_start=self.today + datetime.timedelta(days=200 + i),
                period_end=self.today + datetime.timedelta(days=200 + i),
                issue_date=self.today,
                due_date=self.today,
                bill_to_name="x",
                plan_name="p",
                payment_term_name="t",
                subtotal=0,
                total=0,
            )
        self.assertEqual(count(), baseline)

    def test_detail_and_print_show_the_numbers(self):
        for name in ("invoices:detail", "invoices:print"):
            html = self.client.get(reverse(name, args=[self.inv.pk])).content.decode()
            for text in (
                self.inv.number,
                "₹1,000.00",
                "₹180.00",
                "₹1,180.00",
                "GST 18",
                "Cara Customer Co",
            ):
                self.assertIn(text, html, f"{text} missing in {name}")
        self.assertIn(
            "Balance due",
            self.client.get(reverse("invoices:print", args=[self.inv.pk])).content.decode(),
        )

    def test_detail_buttons(self):
        html = self.client.get(reverse("invoices:detail", args=[self.inv.pk])).content.decode()
        self.assertIn("Send to customer", html)
        self.assertNotIn("Cancel invoice", html)  # staff cannot cancel
        self.client.force_login(self.admin)
        self.assertIn(
            "Cancel invoice",
            self.client.get(reverse("invoices:detail", args=[self.inv.pk])).content.decode(),
        )


class ScopingTests(InvoiceTestCase):
    def test_customer_sees_only_their_own_invoices(self):
        other = make_user("other@x.com", Role.CUSTOMER)
        other_sub = self.move(
            self.make_sub(customer=other.profile), "quotation", "confirmed", "active"
        )
        engine.run(today=self.today)
        theirs = Invoice.objects.get(subscription=other_sub)
        self.assertEqual(
            set(selectors.invoice_queryset(self.customer_user, self.today)), {self.inv}
        )
        self.assertEqual(set(selectors.invoice_queryset(other, self.today)), {theirs})
        self.assertEqual(
            set(selectors.invoice_queryset(self.staff, self.today)), {self.inv, theirs}
        )
        self.assertIsNone(selectors.get_invoice(self.customer_user, theirs.pk))


class SendTests(InvoiceTestCase):
    def test_send_emails_the_customer_and_records_it(self):
        services.send_invoice(actor=self.staff, invoice=self.inv)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["cust@x.com"])
        self.assertIn(self.inv.number, message.subject)
        self.assertIn("1,180.00", message.body)
        self.inv.refresh_from_db()
        self.assertIsNotNone(self.inv.sent_at)
        self.assertTrue(AuditLog.objects.filter(action="invoice.sent").exists())

    def test_send_via_view(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse("invoices:send", args=[self.inv.pk]), follow=True)
        self.assertContains(r, "was emailed to cust@x.com")
        self.assertContains(r, "Send again")

    def test_cancelled_invoice_cannot_be_sent(self):
        services.cancel_invoice(actor=self.admin, invoice=self.inv)
        self.inv.refresh_from_db()
        with self.assertRaises(services.InvoiceError):
            services.send_invoice(actor=self.staff, invoice=self.inv)
        self.assertEqual(len(mail.outbox), 0)

    def test_customer_cannot_send(self):
        with self.assertRaises(PermissionDenied):
            services.send_invoice(actor=self.customer_user, invoice=self.inv)


class CancelAndConfirmTests(InvoiceTestCase):
    def test_admin_cancels_and_it_is_audited(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("invoices:cancel", args=[self.inv.pk]), {"reason": "Wrong plan"}, follow=True
        )
        self.assertContains(r, "was cancelled")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "cancelled")
        self.assertEqual(
            AuditLog.objects.get(action="invoice.cancelled").details["reason"], "Wrong plan"
        )

    def test_cannot_cancel_after_a_payment(self):
        Invoice.objects.filter(pk=self.inv.pk).update(amount_paid=D("10.00"))
        self.inv.refresh_from_db()
        with self.assertRaises(services.InvoiceError):
            services.cancel_invoice(actor=self.admin, invoice=self.inv)

    def test_cannot_cancel_twice_or_cancel_paid(self):
        services.cancel_invoice(actor=self.admin, invoice=self.inv)
        with self.assertRaises(services.InvoiceError):
            services.cancel_invoice(actor=self.admin, invoice=self.inv)
        Invoice.objects.filter(pk=self.inv.pk).update(status="paid", amount_paid=self.inv.total)
        with self.assertRaises(services.InvoiceError):
            services.cancel_invoice(actor=self.admin, invoice=self.inv)

    def test_staff_cannot_cancel_in_the_service_either(self):
        with self.assertRaises(PermissionDenied):
            services.cancel_invoice(actor=self.staff, invoice=self.inv)

    def test_draft_can_be_confirmed(self):
        Invoice.objects.filter(pk=self.inv.pk).update(status="draft")
        self.inv.refresh_from_db()
        services.confirm_invoice(actor=self.staff, invoice=self.inv)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "confirmed")
        with self.assertRaises(services.InvoiceError):
            services.confirm_invoice(actor=self.staff, invoice=self.inv)


class BillingPageTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_page_lists_what_is_due(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        html = self.client.get(reverse("billing:home")).content.decode()
        self.assertIn(sub.number, html)
        self.assertIn("1</span>", html)

    def test_run_creates_invoices_and_shows_the_summary_once(self):
        self.move(self.make_sub(), "quotation", "confirmed", "active")
        r = self.client.post(reverse("billing:run"), follow=True)
        self.assertContains(r, "Billing finished: 1 invoice(s) created")
        self.assertContains(r, "INV-00001")
        self.assertContains(r, "Result of the last run")
        again = self.client.get(reverse("billing:home"))
        self.assertNotContains(again, "Result of the last run")  # shown once
        self.assertEqual(Invoice.objects.count(), 1)

    def test_run_twice_from_the_button_does_not_double_bill(self):
        self.move(self.make_sub(), "quotation", "confirmed", "active")
        self.client.post(reverse("billing:run"))
        r = self.client.post(reverse("billing:run"), follow=True)
        self.assertContains(r, "0 invoice(s) created")
        self.assertEqual(Invoice.objects.count(), 1)

    def test_failures_are_shown_in_the_summary(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        sub.lines.all().delete()
        r = self.client.post(reverse("billing:run"), follow=True)
        self.assertContains(r, "problem(s)")
        self.assertContains(r, "no product lines")

    def test_nothing_due(self):
        r = self.client.post(reverse("billing:run"), follow=True)
        self.assertContains(r, "Nothing was due")


class CommandTests(SubsTestCase):
    def run_cmd(self, *args):
        out, err = StringIO(), StringIO()
        call_command("run_billing", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_command_bills_and_reports(self):
        self.move(self.make_sub(), "quotation", "confirmed", "active")
        out, _ = self.run_cmd()
        self.assertIn("INV-00001", out)
        self.assertIn("1 invoice(s)", out)
        out, _ = self.run_cmd()
        self.assertIn("0 invoice(s)", out)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_date_option(self):
        self.move(self.make_sub(), "quotation", "confirmed", "active")
        out, _ = self.run_cmd("--date", (self.today + datetime.timedelta(days=75)).isoformat())
        self.assertEqual(Invoice.objects.count(), 3)  # this month and the next two

    def test_dry_run_changes_nothing(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        out, _ = self.run_cmd("--dry-run")
        self.assertIn(sub.number, out)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_bad_date(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--date", "31/12/2026")


class SubscriptionPageTests(InvoiceTestCase):
    def test_subscription_page_links_to_its_invoices(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("subscriptions:detail", args=[self.sub.pk])).content.decode()
        self.assertIn(f'href="/invoices/{self.inv.pk}/"', html)
        self.assertIn(self.inv.number, html)
