import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.catalog import services
from apps.catalog.models import Plan, Product, ProductVariant
from apps.core.errors import DuplicateError
from apps.core.models import AuditLog
from apps.core.testing import make_people
from apps.taxes.models import Tax


class CatalogTestCase(TestCase):
    def setUp(self):
        self.admin, self.staff, self.customer = make_people()
        self.gst = Tax.objects.create(name="GST 18", tax_type="percentage", rate=Decimal("18"))
        self.crm = Product.objects.create(
            name="CRM Suite",
            sales_price=Decimal("1000"),
            cost_price=Decimal("400"),
            default_tax=self.gst,
        )
        self.plan = Plan.objects.create(name="Monthly Basic", billing_period="monthly")

    def product_data(self, **over):
        data = {
            "name": "Helpdesk",
            "product_type": "service",
            "sales_price": "500",
            "cost_price": "100",
            "default_tax": "",
            "notes": "",
        }
        data.update(over)
        return data

    def plan_data(self, **over):
        data = {
            "name": "Yearly Pro",
            "billing_period": "yearly",
            "min_quantity": "1",
            "start_date": "",
            "end_date": "",
            "closable": "on",
            "renewable": "on",
        }
        data.update(over)
        return data


class ModelTests(CatalogTestCase):
    def test_variant_price_adds_extra(self):
        v = ProductVariant.objects.create(
            product=self.crm, attribute="Brand", value="Odoo", extra_price=Decimal("560")
        )
        self.assertEqual(v.price, Decimal("1560.00"))
        self.assertEqual(self.crm.price_for(v), Decimal("1560.00"))
        self.assertEqual(self.crm.price_for(), Decimal("1000.00"))

    def test_margin(self):
        self.assertEqual(self.crm.margin, Decimal("600.00"))

    def test_db_blocks_duplicate_names_ignoring_case(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(name="crm suite", sales_price=Decimal("1"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.create(name="MONTHLY BASIC")

    def test_db_blocks_negative_prices(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(name="Bad", sales_price=Decimal("-1"))

    def test_db_blocks_duplicate_variant_option(self):
        ProductVariant.objects.create(product=self.crm, attribute="Brand", value="Odoo")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(product=self.crm, attribute="brand", value="ODOO")

    def test_same_option_allowed_on_another_product(self):
        other = Product.objects.create(name="Other", sales_price=Decimal("1"))
        ProductVariant.objects.create(product=self.crm, attribute="Brand", value="Odoo")
        ProductVariant.objects.create(product=other, attribute="Brand", value="Odoo")

    def test_db_blocks_plan_end_before_start(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.create(
                name="Bad", start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 4, 1)
            )

    def test_db_blocks_zero_min_quantity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.create(name="Zero", min_quantity=0)


class AccessTests(CatalogTestCase):
    def code(self, user, name, method="get", *args, data=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_read_pages_internal_only(self):
        pages = [
            ("catalog:product_list", ()),
            ("catalog:product_detail", (self.crm.pk,)),
            ("catalog:plan_list", ()),
        ]
        for name, args in pages:
            self.assertEqual(self.code(None, name, "get", *args), 302, name)
            self.assertEqual(self.code(self.customer, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.staff, name, "get", *args), 200, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)

    def test_write_pages_admin_only(self):
        v = ProductVariant.objects.create(product=self.crm, attribute="Size", value="L")
        gets = [
            ("catalog:product_create", ()),
            ("catalog:product_edit", (self.crm.pk,)),
            ("catalog:plan_create", ()),
            ("catalog:plan_edit", (self.plan.pk,)),
            ("catalog:variant_edit", (v.pk,)),
        ]
        for name, args in gets:
            self.assertEqual(self.code(self.staff, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.customer, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)
        posts = [
            ("catalog:product_toggle_active", (self.crm.pk,)),
            ("catalog:plan_toggle_active", (self.plan.pk,)),
            ("catalog:variant_toggle_active", (v.pk,)),
            ("catalog:variant_create", (self.crm.pk,)),
        ]
        for name, args in posts:
            self.assertEqual(
                self.code(self.staff, name, "post", *args, data={"active": "0"}), 403, name
            )

    def test_staff_forged_post_changes_nothing(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("catalog:product_create"), self.product_data())
        self.client.post(
            reverse("catalog:product_edit", args=[self.crm.pk]), self.product_data(name="Hacked")
        )
        self.assertEqual(Product.objects.count(), 1)
        self.crm.refresh_from_db()
        self.assertEqual(self.crm.name, "CRM Suite")

    def test_staff_see_no_edit_buttons(self):
        self.client.force_login(self.staff)
        html = self.client.get(
            reverse("catalog:product_detail", args=[self.crm.pk])
        ).content.decode()
        self.assertNotIn("Add a variant", html)
        self.assertNotIn(">Edit<", html)
        self.assertNotIn(
            "Add product", self.client.get(reverse("catalog:product_list")).content.decode()
        )

    def test_sidebar_links(self):
        for user in (self.admin, self.staff):
            self.client.force_login(user)
            html = self.client.get(reverse("core:home")).content.decode()
            for href in ("/catalog/products/", "/catalog/plans/", "/taxes/"):
                self.assertIn(f'href="{href}"', html)
        self.client.force_login(self.customer)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertNotIn('href="/catalog/products/"', html)


class ProductTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_product_and_audit(self):
        r = self.client.post(
            reverse("catalog:product_create"), self.product_data(default_tax=self.gst.pk)
        )
        product = Product.objects.get(name="Helpdesk")
        self.assertRedirects(r, reverse("catalog:product_detail", args=[product.pk]))
        self.assertEqual(product.default_tax, self.gst)
        self.assertTrue(
            AuditLog.objects.filter(action="product.created", entity_id=str(product.pk)).exists()
        )

    def test_duplicate_name_rejected(self):
        r = self.client.post(reverse("catalog:product_create"), self.product_data(name="crm suite"))
        self.assertContains(r, "already exists")
        self.assertEqual(Product.objects.count(), 1)

    def test_negative_prices_rejected(self):
        r = self.client.post(
            reverse("catalog:product_create"), self.product_data(sales_price="-5", cost_price="-1")
        )
        self.assertContains(r, "price cannot be negative")
        self.assertContains(r, "cost cannot be negative")

    def test_inactive_tax_cannot_be_chosen(self):
        old = Tax.objects.create(
            name="Old", tax_type="percentage", rate=Decimal("5"), is_active=False
        )
        r = self.client.post(
            reverse("catalog:product_create"), self.product_data(default_tax=old.pk)
        )
        self.assertContains(r, "Select a valid choice")

    def test_edit_audits_price_change(self):
        self.client.post(
            reverse("catalog:product_edit", args=[self.crm.pk]),
            self.product_data(
                name="CRM Suite", sales_price="1200", cost_price="400", default_tax=self.gst.pk
            ),
        )
        self.crm.refresh_from_db()
        self.assertEqual(self.crm.sales_price, Decimal("1200.00"))
        entry = AuditLog.objects.get(action="product.updated")
        self.assertEqual(entry.details["changed"]["sales_price"], ["1000.00", "1200.00"])

    def test_edit_can_keep_an_inactive_tax(self):
        self.gst.is_active = False
        self.gst.save()
        r = self.client.post(
            reverse("catalog:product_edit", args=[self.crm.pk]),
            self.product_data(
                name="CRM Suite", sales_price="1000", cost_price="400", default_tax=self.gst.pk
            ),
        )
        self.assertRedirects(r, reverse("catalog:product_detail", args=[self.crm.pk]))

    def test_toggle_active_is_audited(self):
        self.client.post(
            reverse("catalog:product_toggle_active", args=[self.crm.pk]), {"active": "0"}
        )
        self.crm.refresh_from_db()
        self.assertFalse(self.crm.is_active)
        self.assertTrue(AuditLog.objects.filter(action="product.deactivated").exists())

    def test_search_matches_name_and_variant_value(self):
        ProductVariant.objects.create(product=self.crm, attribute="Brand", value="Zoho")
        Product.objects.create(name="Billing App", sales_price=Decimal("10"))
        by_name = self.client.get(
            reverse("catalog:product_list"), {"q": "billing"}
        ).content.decode()
        self.assertIn("Billing App", by_name)
        self.assertNotIn("CRM Suite", by_name)
        by_variant = self.client.get(
            reverse("catalog:product_list"), {"q": "zoho"}
        ).content.decode()
        self.assertIn("CRM Suite", by_variant)
        self.assertNotIn("Billing App", by_variant)

    def test_list_paginates(self):
        for i in range(20):
            Product.objects.create(name=f"Bulk {i:02d}", sales_price=Decimal("1"))
        page1 = self.client.get(reverse("catalog:product_list")).content.decode()
        self.assertIn("Page 1 of 2", page1)
        page2 = self.client.get(reverse("catalog:product_list"), {"page": 2})
        self.assertEqual(page2.status_code, 200)

    def test_list_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("catalog:product_list"))
            return len(ctx)

        baseline = count()
        for i in range(10):
            p = Product.objects.create(
                name=f"Bulk {i}", sales_price=Decimal("1"), default_tax=self.gst
            )
            ProductVariant.objects.create(product=p, attribute="A", value="B")
        self.assertEqual(count(), baseline)  # no N+1 on tax or variant count


class VariantTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("catalog:variant_create", args=[self.crm.pk])

    def test_add_variant_htmx_returns_partial_and_toast(self):
        r = self.client.post(
            self.url,
            {"attribute": "Brand", "value": "Odoo", "extra_price": "560"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Brand: Odoo")
        self.assertContains(r, "₹1,560.00")
        self.assertNotContains(r, "<html")
        self.assertIn("toast", r["HX-Trigger"])
        self.assertEqual(self.crm.variants.count(), 1)

    def test_add_variant_without_js_redirects(self):
        r = self.client.post(self.url, {"attribute": "Size", "value": "L", "extra_price": "0"})
        self.assertRedirects(r, reverse("catalog:product_detail", args=[self.crm.pk]))

    def test_duplicate_variant_shows_error_in_place(self):
        ProductVariant.objects.create(product=self.crm, attribute="Brand", value="Odoo")
        r = self.client.post(
            self.url,
            {"attribute": "brand", "value": "odoo", "extra_price": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(r, "already exists")
        self.assertEqual(self.crm.variants.count(), 1)

    def test_negative_extra_price_rejected(self):
        r = self.client.post(
            self.url, {"attribute": "A", "value": "B", "extra_price": "-5"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(r, "cannot be negative")
        self.assertEqual(self.crm.variants.count(), 0)

    def test_toggle_variant_via_htmx(self):
        v = ProductVariant.objects.create(product=self.crm, attribute="Size", value="L")
        r = self.client.post(
            reverse("catalog:variant_toggle_active", args=[v.pk]),
            {"active": "0"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        v.refresh_from_db()
        self.assertFalse(v.is_active)
        self.assertTrue(AuditLog.objects.filter(action="variant.deactivated").exists())

    def test_edit_variant_price(self):
        v = ProductVariant.objects.create(
            product=self.crm, attribute="Size", value="L", extra_price=Decimal("10")
        )
        r = self.client.post(
            reverse("catalog:variant_edit", args=[v.pk]),
            {"attribute": "Size", "value": "L", "extra_price": "25"},
        )
        self.assertRedirects(r, reverse("catalog:product_detail", args=[self.crm.pk]))
        v.refresh_from_db()
        self.assertEqual(v.extra_price, Decimal("25.00"))

    def test_editing_a_variant_to_itself_is_not_a_duplicate(self):
        v = ProductVariant.objects.create(product=self.crm, attribute="Size", value="L")
        r = self.client.post(
            reverse("catalog:variant_edit", args=[v.pk]),
            {"attribute": "Size", "value": "L", "extra_price": "0"},
        )
        self.assertEqual(r.status_code, 302)


class PlanTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_plan(self):
        r = self.client.post(reverse("catalog:plan_create"), self.plan_data(pausable="on"))
        self.assertRedirects(r, reverse("catalog:plan_list"))
        plan = Plan.objects.get(name="Yearly Pro")
        self.assertEqual(plan.billing_period, "yearly")
        self.assertTrue(plan.pausable and plan.closable and plan.renewable)
        self.assertFalse(plan.auto_close)
        self.assertTrue(
            AuditLog.objects.filter(action="plan.created", entity_id=str(plan.pk)).exists()
        )

    def test_end_before_start_rejected(self):
        r = self.client.post(
            reverse("catalog:plan_create"),
            self.plan_data(start_date="2026-05-01", end_date="2026-04-01"),
        )
        self.assertContains(r, "cannot be before the start date")

    def test_auto_close_needs_end_date(self):
        r = self.client.post(reverse("catalog:plan_create"), self.plan_data(auto_close="on"))
        self.assertContains(r, "Set an end date")
        r = self.client.post(
            reverse("catalog:plan_create"), self.plan_data(auto_close="on", end_date="2027-01-01")
        )
        self.assertEqual(r.status_code, 302)

    def test_min_quantity_must_be_positive(self):
        r = self.client.post(reverse("catalog:plan_create"), self.plan_data(min_quantity="0"))
        self.assertContains(r, "at least 1")

    def test_duplicate_plan_name_rejected(self):
        r = self.client.post(reverse("catalog:plan_create"), self.plan_data(name="monthly basic"))
        self.assertContains(r, "already exists")

    def test_edit_plan_audits_changes(self):
        self.client.post(
            reverse("catalog:plan_edit", args=[self.plan.pk]),
            self.plan_data(name="Monthly Basic", billing_period="weekly", pausable="on"),
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.billing_period, "weekly")
        changed = AuditLog.objects.get(action="plan.updated").details["changed"]
        self.assertEqual(changed["billing_period"], ["monthly", "weekly"])
        self.assertEqual(changed["pausable"], [False, True])

    def test_toggle_plan(self):
        self.client.post(
            reverse("catalog:plan_toggle_active", args=[self.plan.pk]), {"active": "0"}
        )
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_active)

    def test_date_input_round_trips_on_edit(self):
        self.plan.start_date = datetime.date(2026, 1, 2)
        self.plan.save()
        r = self.client.get(reverse("catalog:plan_edit", args=[self.plan.pk]))
        self.assertContains(r, 'value="2026-01-02"')


class ServiceGuardTests(CatalogTestCase):
    def test_services_recheck_admin(self):
        for actor in (self.staff, self.customer):
            with self.assertRaises(PermissionDenied):
                services.create_product(actor=actor, name="X", sales_price=Decimal("1"))
            with self.assertRaises(PermissionDenied):
                services.set_plan_active(actor=actor, plan=self.plan, active=False)
            with self.assertRaises(PermissionDenied):
                services.add_variant(
                    actor=actor,
                    product=self.crm,
                    attribute="a",
                    value="b",
                    extra_price=Decimal("0"),
                )

    def test_duplicates_raise_domain_error(self):
        with self.assertRaises(DuplicateError):
            services.create_product(actor=self.admin, name="crm suite", sales_price=Decimal("1"))
        with self.assertRaises(DuplicateError):
            services.create_plan(actor=self.admin, name="MONTHLY BASIC")
