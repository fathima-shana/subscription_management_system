import csv
import datetime
import io
from decimal import Decimal

from django.test import TransactionTestCase
from django.urls import reverse

from apps.billing.models import Invoice
from apps.core.models import AuditLog
from apps.payments import services as payment_services

from .helpers import ReportTestCase

D = Decimal


def read_csv(response):
    text = b"".join(response.streaming_content).decode("utf-8")
    assert text.startswith("\ufeff"), "missing BOM"
    return list(csv.reader(io.StringIO(text[1:])))


class AccessTests(ReportTestCase):
    def code(self, user, name, *args, **params):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(name, args=args), params).status_code

    def test_internal_only(self):
        for name, args in [("reports:report", ()), ("reports:export", ("invoices",))]:
            self.assertEqual(self.code(None, name, *args), 302, name)
            self.assertEqual(self.code(self.customer_user, name, *args), 403, name)
            self.assertEqual(self.code(self.staff, name, *args), 200, name)
            self.assertEqual(self.code(self.admin, name, *args), 200, name)

    def test_unknown_export_kind(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("reports:export", args=["secrets"])).status_code, 404
        )

    def test_sidebar_link(self):
        self.client.force_login(self.staff)
        self.assertIn('href="/reports/"', self.client.get(reverse("core:home")).content.decode())


class DashboardTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.active()
        self.bill()
        self.inv = Invoice.objects.get()
        self.pay(self.inv, "180", "upi")

    def test_admin_and_staff_see_the_business_dashboard_on_home(self):
        for user in (self.admin, self.staff):
            self.client.force_login(user)
            html = self.client.get(reverse("core:home")).content.decode()
            for text in (
                "Monthly recurring revenue",
                "₹1,000.00",
                "₹1,180.00",
                "Payments by method",
                "UPI",
                "Full reports",
            ):
                self.assertIn(text, html, f"{text} missing for {user.role}")

    def test_customers_never_see_business_figures(self):
        self.client.force_login(self.customer_user)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertNotIn("Monthly recurring revenue", html)
        self.assertNotIn("Collected", html)
        self.assertIn("Amount due", html)

    def test_overdue_shown_with_days(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=4)
        )
        self.client.force_login(self.admin)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn(self.inv.number, html)
        self.assertIn("1 invoice past due", html)

    def test_nothing_overdue_message(self):
        self.client.force_login(self.admin)
        self.assertIn("Nothing is overdue", self.client.get(reverse("core:home")).content.decode())


class ReportPageTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.active()
        self.bill()
        self.pay(Invoice.objects.get(), "500", "bank")

    def test_page_renders_all_sections(self):
        html = self.client.get(reverse("reports:report")).content.decode()
        for text in (
            "Recurring revenue by plan",
            "Export to CSV",
            "How the figures are worked out",
            "Total MRR",
            "Collected per month",
            "Overdue invoices",
            "Bank transfer",
        ):
            self.assertIn(text, html)

    def test_date_filter_changes_collected(self):
        html = self.client.get(
            reverse("reports:report"),
            {"preset": "custom", "from": "2020-01-01", "to": "2020-12-31"},
        ).content.decode()
        self.assertIn("01 Jan 2020 to 31 Dec 2020", html)
        self.assertNotIn("₹500.00 <span", html)
        self.assertIn("₹0.00", html)

    def test_plan_filter(self):
        other = self.plan_of("monthly", "Other")
        html = self.client.get(reverse("reports:report"), {"plan": other.pk}).content.decode()
        self.assertIn("No active subscriptions on this plan", html)
        html = self.client.get(reverse("reports:report"), {"plan": self.plan.pk}).content.decode()
        self.assertIn("₹1,000.00", html)

    def test_bad_custom_range_shows_a_message_and_still_renders(self):
        r = self.client.get(
            reverse("reports:report"),
            {"preset": "custom", "from": "2026-05-01", "to": "2026-04-01"},
        )
        self.assertContains(r, "Enter a valid date range")

    def test_export_links_carry_the_filters(self):
        html = self.client.get(
            reverse("reports:report"), {"preset": "year", "plan": self.plan.pk}
        ).content.decode()
        self.assertIn(f"/reports/export/invoices/?preset=year&amp;plan={self.plan.pk}", html)

    def test_all_time_preset(self):
        self.assertContains(
            self.client.get(reverse("reports:report"), {"preset": "all"}), "All time"
        )


class ExportTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.active()
        self.bill()
        self.inv = Invoice.objects.get()
        self.pay(self.inv, "300", "upi")

    def get(self, kind, **params):
        r = self.client.get(reverse("reports:export", args=[kind]), params)
        self.assertEqual(r.status_code, 200)
        return r

    def test_headers_and_download_name(self):
        r = self.get("invoices")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(f"brightloop-invoices-{self.today.isoformat()}.csv", r["Content-Disposition"])
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_invoices_csv(self):
        rows = read_csv(self.get("invoices"))
        self.assertEqual(rows[0][:3], ["Invoice", "Subscription", "Customer"])
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(
            (row["Invoice"], row["Total"], row["Paid"], row["Balance"], row["Status"]),
            (self.inv.number, "1180.00", "300.00", "880.00", "confirmed"),
        )
        self.assertEqual((row["Issue date"], row["Email"]), (self.today.isoformat(), "cust@x.com"))
        self.assertEqual(len(rows), 2)

    def test_invoices_csv_honours_filters(self):
        rows = read_csv(
            self.get("invoices", preset="custom", **{"from": "2020-01-01", "to": "2020-02-01"})
        )
        self.assertEqual(len(rows), 1)  # header only
        other = self.plan_of("monthly", "Other")
        self.assertEqual(len(read_csv(self.get("invoices", plan=other.pk))), 1)
        self.assertEqual(len(read_csv(self.get("invoices", plan=self.plan.pk))), 2)

    def test_overdue_status_is_derived_in_the_export(self):
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=2)
        )
        row = dict(zip(*read_csv(self.get("invoices"))[:2]))
        self.assertEqual(row["Status"], "overdue")

    def test_payments_csv_with_voided(self):
        voided = self.pay(self.inv, "50", "cash")
        payment_services.void_payment(actor=self.admin, payment=voided)
        rows = read_csv(self.get("payments"))
        self.assertEqual(
            rows[0],
            [
                "Date",
                "Invoice",
                "Customer",
                "Method",
                "Amount",
                "Reference",
                "Recorded by",
                "Voided",
            ],
        )
        by_amount = {r[4]: r for r in rows[1:]}
        self.assertEqual((by_amount["300.00"][3], by_amount["300.00"][7]), ("UPI", ""))
        self.assertEqual(by_amount["50.00"][7], "yes")

    def test_overdue_csv(self):
        self.assertEqual(len(read_csv(self.get("overdue"))), 1)
        Invoice.objects.filter(pk=self.inv.pk).update(
            due_date=self.today - datetime.timedelta(days=9)
        )
        rows = read_csv(self.get("overdue"))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual((row["Days overdue"], row["Balance"]), ("9", "880.00"))

    def test_subscriptions_csv(self):
        row = dict(zip(*read_csv(self.get("subscriptions"))[:2]))
        self.assertEqual(
            (row["Status"], row["Recurring subtotal"], row["Plan"], row["Billing period"]),
            ("Active", "1000.00", "Monthly", "Monthly"),
        )

    def test_spreadsheet_formulas_are_neutralised(self):
        self.customer.company_name = '=HYPERLINK("http://evil","x")'
        self.customer.save()
        self.pay_note = payment_services.record_payment(
            actor=self.staff, invoice=self.inv, amount="1", method="cash", note="+cmd|' /C calc'!A0"
        )
        Invoice.objects.filter(pk=self.inv.pk).update(bill_to_name="@SUM(1+1)")
        inv_row = dict(zip(*read_csv(self.get("invoices"))[:2]))
        self.assertEqual(inv_row["Customer"], "'@SUM(1+1)")
        pay_rows = read_csv(self.get("payments"))
        notes = [r[5] for r in pay_rows[1:]]
        self.assertIn("'+cmd|' /C calc'!A0", notes)

    def test_commas_and_quotes_are_escaped(self):
        Invoice.objects.filter(pk=self.inv.pk).update(bill_to_name='Smith, "Jones" & Co')
        row = dict(zip(*read_csv(self.get("invoices"))[:2]))
        self.assertEqual(row["Customer"], 'Smith, "Jones" & Co')

    def test_export_is_audited(self):
        self.get("payments", preset="year")
        entry = AuditLog.objects.get(action="report.exported")
        self.assertEqual((entry.actor, entry.details["kind"]), (self.staff, "payments"))

    def test_many_rows_stream_correctly(self):
        for i in range(25):
            Invoice.objects.create(
                number=f"INV-7{i:04d}",
                subscription=self.inv.subscription,
                customer=self.customer,
                period_start=self.today + datetime.timedelta(days=300 + i),
                period_end=self.today + datetime.timedelta(days=300 + i),
                issue_date=self.today,
                due_date=self.today,
                bill_to_name=f"Name {i}",
                plan_name="p",
                payment_term_name="t",
                subtotal=D("1"),
                total=D("1"),
            )
        self.assertEqual(len(read_csv(self.get("invoices"))), 27)


class StreamingOutsideTestTransactionTests(TransactionTestCase):
    """Runs without the test-wide transaction, the way production does (ATOMIC_REQUESTS on)."""

    def test_export_streams_after_the_request_transaction_closed(self):
        from apps.billing import engine
        from apps.catalog.models import Plan, Product
        from apps.core.testing import make_people
        from apps.subscriptions import services as subscription_services
        from apps.subscriptions.models import PaymentTerm

        admin, staff, customer_user = make_people()
        term, _ = PaymentTerm.objects.get_or_create(name="30 days", defaults={"days": 30})
        plan = Plan.objects.create(name="Monthly", billing_period="monthly")
        product = Product.objects.create(name="CRM", sales_price=D("100"))
        today = datetime.date(2026, 6, 15)
        for _ in range(3):
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
        self.client.force_login(staff)
        for kind in ("invoices", "payments", "subscriptions", "overdue"):
            response = self.client.get(reverse("reports:export", args=[kind]), {"preset": "all"})
            rows = read_csv(response)
            self.assertGreaterEqual(len(rows), 1, kind)
        self.assertEqual(
            len(
                read_csv(
                    self.client.get(reverse("reports:export", args=["invoices"]), {"preset": "all"})
                )
            ),
            4,
        )
