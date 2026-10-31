import datetime
from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import Role
from apps.billing import engine
from apps.billing.models import Invoice
from apps.core.testing import make_user
from apps.payments import services as payment_services
from apps.portal import selectors
from apps.reports.tests.helpers import ReportTestCase
from apps.subscriptions import services as subscription_services

D = Decimal


class PortalTestCase(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.mine = self.active()
        self.other_user = make_user("other@x.com", Role.CUSTOMER, "Olive Other")
        self.theirs = self.active(customer=self.other_user.profile)
        self.bill()
        self.my_inv = Invoice.objects.get(subscription=self.mine)
        self.their_inv = Invoice.objects.get(subscription=self.theirs)
        self.client.force_login(self.customer_user)


class AccessTests(PortalTestCase):
    PAGES = [("portal:subscriptions", ()), ("portal:invoices", ())]

    def code(self, user, name, *args):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(name, args=args)).status_code

    def test_customers_only(self):
        pages = self.PAGES + [
            ("portal:subscription", (self.mine.pk,)),
            ("portal:invoice", (self.my_inv.pk,)),
            ("portal:invoice_print", (self.my_inv.pk,)),
        ]
        for name, args in pages:
            self.assertEqual(self.code(None, name, *args), 302, name)
            self.assertEqual(self.code(self.staff, name, *args), 403, name)
            self.assertEqual(self.code(self.admin, name, *args), 403, name)
            self.assertEqual(self.code(self.customer_user, name, *args), 200, name)

    def test_other_customers_records_are_404(self):
        for name, pk in [
            ("portal:subscription", self.theirs.pk),
            ("portal:invoice", self.their_inv.pk),
            ("portal:invoice_print", self.their_inv.pk),
        ]:
            self.assertEqual(self.code(self.customer_user, name, pk), 404, name)

    def test_unknown_ids_are_404(self):
        self.assertEqual(self.code(self.customer_user, "portal:invoice", 99999), 404)

    def test_internal_pages_stay_closed_to_customers(self):
        for name in (
            "subscriptions:list",
            "invoices:list",
            "payments:list",
            "reports:report",
            "billing:home",
            "customers:list",
        ):
            self.assertEqual(self.code(self.customer_user, name), 403, name)
        self.assertEqual(self.code(self.customer_user, "invoices:detail", self.my_inv.pk), 403)
        self.assertEqual(self.code(self.customer_user, "subscriptions:detail", self.mine.pk), 403)

    def test_customers_cannot_post_internal_actions(self):
        for name, args in [
            ("invoices:send", (self.my_inv.pk,)),
            ("invoices:cancel", (self.my_inv.pk,)),
            ("payments:void", (1,)),
            ("subscriptions:transition", (self.mine.pk,)),
            ("billing:run", ()),
        ]:
            self.assertEqual(
                self.client.post(reverse(name, args=args), {"to": "closed"}).status_code, 403, name
            )
        self.my_inv.refresh_from_db()
        self.assertEqual(self.my_inv.status, "confirmed")


class SidebarTests(PortalTestCase):
    def test_customer_sidebar(self):
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn('href="/portal/subscriptions/"', html)
        self.assertIn('href="/portal/invoices/"', html)
        for hidden in (
            "/customers/",
            "/staff/",
            "/reports/",
            "/billing/",
            "/payments/",
            "/catalog/products/",
            "/taxes/",
        ):
            self.assertNotIn(f'href="{hidden}"', html, hidden)

    def test_internal_sidebar_has_no_portal_links(self):
        self.client.force_login(self.staff)
        self.assertNotIn("/portal/", self.client.get(reverse("core:home")).content.decode())


class DashboardTests(PortalTestCase):
    def test_numbers(self):
        self.pay(self.my_inv, "180")
        d = selectors.dashboard(self.customer_user, self.today)
        self.assertEqual(d["active_count"], 1)
        self.assertEqual(d["outstanding"], {"amount": D("1000.00"), "count": 1})
        self.assertEqual(d["overdue"], {"amount": D("0"), "count": 0})
        self.assertEqual(
            d["next_billing"], self.mine.__class__.objects.get(pk=self.mine.pk).next_billing_date
        )

    def test_only_own_data_in_the_numbers(self):
        d = selectors.dashboard(self.other_user, self.today)
        self.assertEqual(d["outstanding"]["amount"], D("1180.00"))
        mine = selectors.dashboard(self.customer_user, self.today)
        self.assertEqual(mine["outstanding"]["amount"], D("1180.00"))
        self.pay(self.my_inv, "1180")
        self.assertEqual(
            selectors.dashboard(self.customer_user, self.today)["outstanding"]["count"], 0
        )
        self.assertEqual(
            selectors.dashboard(self.other_user, self.today)["outstanding"]["count"], 1
        )

    def test_overdue_card(self):
        Invoice.objects.filter(pk=self.my_inv.pk).update(
            due_date=self.today - datetime.timedelta(days=3)
        )
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn("1 invoice", html)
        self.assertIn("/portal/invoices/?status=overdue", html)

    def test_dashboard_lists_own_items_only(self):
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn(self.mine.number, html)
        self.assertNotIn(self.theirs.number, html)
        self.assertIn(self.my_inv.number, html)
        self.assertNotIn(self.their_inv.number, html)

    def test_empty_customer(self):
        newbie = make_user("new@x.com", Role.CUSTOMER)
        self.client.force_login(newbie)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn("You have no subscriptions yet", html)
        self.assertIn("No invoices yet", html)


class SubscriptionPagesTests(PortalTestCase):
    def test_list_shows_only_own_non_draft_subscriptions(self):
        draft = self.make_sub()
        quote = self.move(self.make_sub(), "quotation")
        html = self.client.get(reverse("portal:subscriptions")).content.decode()
        self.assertIn(self.mine.number, html)
        self.assertIn(quote.number, html)
        self.assertNotIn(draft.number, html)  # internal work in progress
        self.assertNotIn(self.theirs.number, html)

    def test_draft_subscription_is_not_reachable(self):
        draft = self.make_sub()
        self.assertEqual(
            self.client.get(reverse("portal:subscription", args=[draft.pk])).status_code, 404
        )
        self.assertIsNone(selectors.get_subscription(self.customer_user, draft.pk))

    def test_detail_is_read_only_and_shows_figures(self):
        html = self.client.get(reverse("portal:subscription", args=[self.mine.pk])).content.decode()
        for text in (
            self.mine.number,
            "₹1,000.00",
            "₹1,180.00",
            "Total per period",
            self.my_inv.number,
        ):
            self.assertIn(text, html)
        for button in (
            "Pause",
            "Close",
            "Renew",
            "Send quotation",
            "Confirm",
            "Remove",
            "Add a product",
            "Edit",
        ):
            self.assertNotIn(f">{button}<", html, button)

    def test_quotation_message(self):
        quote = self.move(
            self.make_sub(quote_valid_until=self.today + datetime.timedelta(days=5)), "quotation"
        )
        html = self.client.get(reverse("portal:subscription", args=[quote.pk])).content.decode()
        self.assertIn("This is a quotation", html)

    def test_search(self):
        self.assertIn(
            "No subscriptions match",
            self.client.get(reverse("portal:subscriptions"), {"q": "zzz"}).content.decode(),
        )
        self.assertIn(
            self.mine.number,
            self.client.get(
                reverse("portal:subscriptions"), {"q": self.mine.number}
            ).content.decode(),
        )

    def test_htmx_fragment(self):
        self.assertNotContains(
            self.client.get(reverse("portal:subscriptions"), HTTP_HX_REQUEST="true"), "<html"
        )

    def test_draft_internal_notes_never_leak(self):
        sub = self.make_sub(notes="INTERNAL: customer is difficult")
        self.move(sub, "quotation")
        html = self.client.get(reverse("portal:subscription", args=[sub.pk])).content.decode()
        self.assertNotIn("INTERNAL", html)


class InvoicePagesTests(PortalTestCase):
    def test_list_only_own_invoices_and_no_drafts(self):
        Invoice.objects.filter(pk=self.my_inv.pk).update(status="draft")
        self.assertNotIn(
            self.my_inv.number, self.client.get(reverse("portal:invoices")).content.decode()
        )
        self.assertEqual(
            self.client.get(reverse("portal:invoice", args=[self.my_inv.pk])).status_code, 404
        )
        Invoice.objects.filter(pk=self.my_inv.pk).update(status="confirmed")
        html = self.client.get(reverse("portal:invoices")).content.decode()
        self.assertIn(self.my_inv.number, html)
        self.assertNotIn(self.their_inv.number, html)

    def test_filters(self):
        self.pay(self.my_inv, "1180")
        self.assertIn(
            self.my_inv.number,
            self.client.get(reverse("portal:invoices"), {"status": "paid"}).content.decode(),
        )
        self.assertNotIn(
            self.my_inv.number,
            self.client.get(reverse("portal:invoices"), {"status": "confirmed"}).content.decode(),
        )
        Invoice.objects.filter(pk=self.my_inv.pk).update(
            status="confirmed", amount_paid=0, due_date=self.today - datetime.timedelta(days=2)
        )
        self.assertIn(
            self.my_inv.number,
            self.client.get(reverse("portal:invoices"), {"status": "overdue"}).content.decode(),
        )
        self.assertEqual(
            self.client.get(reverse("portal:invoices"), {"status": "bogus"}).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("portal:invoices"), {"status": "draft"}).status_code, 200
        )  # not offered

    def test_detail_shows_figures_and_live_payments_only(self):
        a = self.pay(self.my_inv, "500", "upi")
        b = payment_services.record_payment(
            actor=self.staff, invoice=self.my_inv, amount="100", method="cash", note="VOIDME-77"
        )
        payment_services.void_payment(actor=self.admin, payment=b)
        html = self.client.get(reverse("portal:invoice", args=[self.my_inv.pk])).content.decode()
        for text in (
            "₹1,180.00",
            "GST 18",
            "Payments received",
            "UPI",
            "₹500.00",
            "₹680.00",
            "Print or save as PDF",
        ):
            self.assertIn(text, html)
        self.assertNotIn("VOIDME-77", html)

    def test_detail_has_no_internal_actions(self):
        html = self.client.get(reverse("portal:invoice", args=[self.my_inv.pk])).content.decode()
        for text in ("Send to customer", "Cancel invoice", "Record payment", ">Void<"):
            self.assertNotIn(text, html)

    def test_print_view(self):
        html = self.client.get(
            reverse("portal:invoice_print", args=[self.my_inv.pk])
        ).content.decode()
        self.assertIn(self.my_inv.number, html)
        self.assertIn("Balance due", html)

    def test_overdue_badge(self):
        Invoice.objects.filter(pk=self.my_inv.pk).update(
            due_date=self.today - datetime.timedelta(days=2)
        )
        html = self.client.get(reverse("portal:invoice", args=[self.my_inv.pk])).content.decode()
        self.assertIn(">overdue<", html)

    def test_pagination_and_search(self):
        for i in range(17):
            Invoice.objects.create(
                number=f"INV-6{i:04d}",
                subscription=self.mine,
                customer=self.customer,
                period_start=self.today + datetime.timedelta(days=400 + i),
                period_end=self.today + datetime.timedelta(days=400 + i),
                issue_date=self.today,
                due_date=self.today,
                bill_to_name="x",
                plan_name="p",
                payment_term_name="t",
                subtotal=D("1"),
                total=D("1"),
            )
        self.assertIn("Page 1 of 2", self.client.get(reverse("portal:invoices")).content.decode())
        self.assertIn(
            "INV-60003",
            self.client.get(reverse("portal:invoices"), {"q": "60003"}).content.decode(),
        )
        self.assertIn(
            "No invoices match",
            self.client.get(reverse("portal:invoices"), {"q": "zzz"}).content.decode(),
        )


class HomeDispatchTests(PortalTestCase):
    def test_each_role_lands_on_its_own_dashboard(self):
        self.client.force_login(self.customer_user)
        self.assertIn(
            "Your subscriptions and invoices at a glance",
            self.client.get(reverse("core:home")).content.decode(),
        )
        for user in (self.admin, self.staff):
            self.client.force_login(user)
            self.assertIn(
                "Monthly recurring revenue", self.client.get(reverse("core:home")).content.decode()
            )
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 302)
