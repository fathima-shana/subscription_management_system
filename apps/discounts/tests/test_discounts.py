import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.errors import DuplicateError
from apps.core.models import AuditLog
from apps.core.testing import make_people
from apps.discounts import services
from apps.discounts.models import Discount


class DiscountTestCase(TestCase):
    def setUp(self):
        self.admin, self.staff, self.customer = make_people()
        self.crm = Product.objects.create(name="CRM", sales_price=Decimal("1000"))
        self.pos = Product.objects.create(name="POS", sales_price=Decimal("500"))
        self.ten = Discount.objects.create(
            name="Ten off", discount_type="percentage", value=Decimal("10")
        )

    def data(self, **over):
        data = {
            "name": "Bulk deal",
            "discount_type": "percentage",
            "value": "15",
            "applies_to": "subscription",
            "min_purchase": "",
            "min_quantity": "",
            "start_date": "",
            "end_date": "",
            "usage_limit": "",
        }
        data.update(over)
        return data


class AccessTests(DiscountTestCase):
    def code(self, user, name, method="get", *args, data=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_list_internal_only(self):
        self.assertEqual(self.code(None, "discounts:list"), 302)
        self.assertEqual(self.code(self.customer, "discounts:list"), 403)
        self.assertEqual(self.code(self.staff, "discounts:list"), 200)
        self.assertEqual(self.code(self.admin, "discounts:list"), 200)

    def test_changes_admin_only(self):
        for name, args in [("discounts:create", ()), ("discounts:edit", (self.ten.pk,))]:
            self.assertEqual(self.code(self.staff, name, "get", *args), 403)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200)
        self.assertEqual(
            self.code(
                self.staff, "discounts:toggle_active", "post", self.ten.pk, data={"active": "0"}
            ),
            403,
        )
        self.ten.refresh_from_db()
        self.assertTrue(self.ten.is_active)

    def test_staff_forged_post_changes_nothing(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("discounts:create"), self.data())
        self.assertEqual(Discount.objects.count(), 1)

    def test_staff_see_no_edit_buttons_and_sidebar_link_exists(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("discounts:list")).content.decode()
        self.assertNotIn("Add discount", html)
        self.assertNotIn("Deactivate", html)
        self.assertIn('href="/discounts/"', self.client.get(reverse("core:home")).content.decode())


class CrudTests(DiscountTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_whole_subscription_discount(self):
        r = self.client.post(
            reverse("discounts:create"),
            self.data(
                min_purchase="2000",
                min_quantity="3",
                usage_limit="50",
                start_date="2026-01-01",
                end_date="2026-12-31",
            ),
        )
        self.assertRedirects(r, reverse("discounts:list"))
        d = Discount.objects.get(name="Bulk deal")
        self.assertEqual(
            (d.min_purchase, d.min_quantity, d.usage_limit), (Decimal("2000.00"), 3, 50)
        )
        self.assertTrue(
            AuditLog.objects.filter(action="discount.created", entity_id=str(d.pk)).exists()
        )

    def test_blank_minimums_default_to_none(self):
        self.client.post(reverse("discounts:create"), self.data())
        d = Discount.objects.get(name="Bulk deal")
        self.assertEqual(
            (d.min_purchase, d.min_quantity, d.usage_limit), (Decimal("0.00"), 1, None)
        )

    def test_create_product_discount_with_targets(self):
        self.client.post(
            reverse("discounts:create"),
            self.data(name="CRM promo", applies_to="product", products=[self.crm.pk, self.pos.pk]),
        )
        d = Discount.objects.get(name="CRM promo")
        self.assertEqual(set(d.products.all()), {self.crm, self.pos})

    def test_product_scope_requires_a_product(self):
        r = self.client.post(reverse("discounts:create"), self.data(applies_to="product"))
        self.assertContains(r, "Select at least one product")

    def test_subscription_scope_ignores_posted_products(self):
        self.client.post(reverse("discounts:create"), self.data(products=[self.crm.pk]))
        self.assertEqual(Discount.objects.get(name="Bulk deal").products.count(), 0)

    def test_validation_messages(self):
        cases = [
            (dict(value="101"), "more than 100"),
            (dict(value="0"), "more than 0"),
            (dict(discount_type="fixed", value="-5"), "more than 0"),
            (dict(min_quantity="0"), "at least 1"),
            (dict(min_purchase="-1"), "cannot be negative"),
            (dict(usage_limit="0"), "at least 1"),
            (dict(start_date="2026-05-01", end_date="2026-04-01"), "before the start date"),
            (dict(name="ten OFF"), "already exists"),
        ]
        for over, text in cases:
            self.assertContains(
                self.client.post(reverse("discounts:create"), self.data(**over)),
                text,
                msg_prefix=str(over),
            )
        self.assertEqual(Discount.objects.count(), 1)

    def test_fixed_discount_may_be_over_100(self):
        r = self.client.post(
            reverse("discounts:create"), self.data(discount_type="fixed", value="500")
        )
        self.assertEqual(r.status_code, 302)

    def test_inactive_products_cannot_be_targeted(self):
        self.pos.is_active = False
        self.pos.save()
        r = self.client.post(
            reverse("discounts:create"), self.data(applies_to="product", products=[self.pos.pk])
        )
        self.assertContains(r, "Select a valid choice")

    def test_edit_audits_before_after_including_targets(self):
        d = services.create_discount(
            actor=self.admin,
            name="Promo",
            discount_type="percentage",
            value=Decimal("5"),
            applies_to="product",
            products=[self.crm],
        )
        self.client.post(
            reverse("discounts:edit", args=[d.pk]),
            self.data(name="Promo", value="8", applies_to="product", products=[self.pos.pk]),
        )
        d.refresh_from_db()
        self.assertEqual(d.value, Decimal("8.00"))
        changed = AuditLog.objects.get(action="discount.updated").details["changed"]
        self.assertEqual(changed["value"], ["5.00", "8.00"])
        self.assertEqual(changed["products"], [[self.crm.pk], [self.pos.pk]])

    def test_switching_to_subscription_scope_clears_targets(self):
        d = services.create_discount(
            actor=self.admin,
            name="Promo",
            discount_type="percentage",
            value=Decimal("5"),
            applies_to="product",
            products=[self.crm],
        )
        self.client.post(reverse("discounts:edit", args=[d.pk]), self.data(name="Promo", value="5"))
        self.assertEqual(d.products.count(), 0)

    def test_cannot_set_limit_below_times_already_used(self):
        Discount.objects.filter(pk=self.ten.pk).update(used_count=5)
        r = self.client.post(
            reverse("discounts:edit", args=[self.ten.pk]),
            self.data(name="Ten off", value="10", usage_limit="3"),
        )
        self.assertContains(r, "already been used 5 times")

    def test_used_count_cannot_be_forged_through_the_form(self):
        self.client.post(
            reverse("discounts:edit", args=[self.ten.pk]),
            self.data(name="Ten off", value="10", used_count="99"),
        )
        self.ten.refresh_from_db()
        self.assertEqual(self.ten.used_count, 0)

    def test_toggle_and_audit(self):
        self.client.post(reverse("discounts:toggle_active", args=[self.ten.pk]), {"active": "0"})
        self.ten.refresh_from_db()
        self.assertFalse(self.ten.is_active)
        self.assertTrue(AuditLog.objects.filter(action="discount.deactivated").exists())

    def test_list_shows_status_and_usage(self):
        Discount.objects.create(
            name="Old",
            discount_type="fixed",
            value=Decimal("5"),
            end_date=datetime.date(2020, 1, 1),
            usage_limit=10,
            used_count=4,
        )
        html = self.client.get(reverse("discounts:list")).content.decode()
        self.assertIn("expired", html)
        self.assertIn("4 / 10", html)

    def test_search(self):
        html = self.client.get(reverse("discounts:list"), {"q": "zzz"}).content.decode()
        self.assertIn("No discounts match", html)

    def test_edit_form_prechecks_existing_targets(self):
        d = services.create_discount(
            actor=self.admin,
            name="Promo",
            discount_type="percentage",
            value=Decimal("5"),
            applies_to="product",
            products=[self.crm],
        )
        html = self.client.get(reverse("discounts:edit", args=[d.pk])).content.decode()
        self.assertIn(f'<option value="{self.crm.pk}" selected>', html)


class ServiceGuardTests(DiscountTestCase):
    def test_services_recheck_admin(self):
        with self.assertRaises(PermissionDenied):
            services.create_discount(
                actor=self.staff, name="X", discount_type="fixed", value=Decimal("1")
            )
        with self.assertRaises(PermissionDenied):
            services.set_discount_active(actor=self.customer, discount=self.ten, active=False)

    def test_duplicate_raises_domain_error(self):
        with self.assertRaises(DuplicateError):
            services.create_discount(
                actor=self.admin, name="TEN OFF", discount_type="fixed", value=Decimal("1")
            )
