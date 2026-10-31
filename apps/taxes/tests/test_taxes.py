from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.core.errors import DuplicateError
from apps.core.models import AuditLog
from apps.core.testing import make_people
from apps.taxes import services
from apps.taxes.models import Tax


class TaxTestCase(TestCase):
    def setUp(self):
        self.admin, self.staff, self.customer = make_people()
        self.gst = Tax.objects.create(name="GST 18", tax_type="percentage", rate=Decimal("18"))


class TaxModelTests(TaxTestCase):
    def test_name_unique_case_insensitive_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tax.objects.create(name="gst 18", tax_type="percentage", rate=Decimal("5"))

    def test_db_rejects_percentage_over_100_and_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tax.objects.create(name="Big", tax_type="percentage", rate=Decimal("150"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tax.objects.create(name="Neg", tax_type="fixed", rate=Decimal("-1"))

    def test_fixed_may_exceed_100(self):
        Tax.objects.create(name="Levy", tax_type="fixed", rate=Decimal("250"))

    def test_label(self):
        self.assertEqual(self.gst.label, "GST 18 (18%)")


class TaxAccessTests(TaxTestCase):
    def code(self, user, name, method="get", *args, data=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_view_is_internal_only(self):
        self.assertEqual(self.code(None, "taxes:list"), 302)
        self.assertEqual(self.code(self.customer, "taxes:list"), 403)
        self.assertEqual(self.code(self.staff, "taxes:list"), 200)
        self.assertEqual(self.code(self.admin, "taxes:list"), 200)

    def test_changes_are_admin_only(self):
        for name, args in [("taxes:create", ()), ("taxes:edit", (self.gst.pk,))]:
            self.assertEqual(self.code(self.staff, name, "get", *args), 403)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200)
        self.assertEqual(
            self.code(self.staff, "taxes:toggle_active", "post", self.gst.pk, data={"active": "0"}),
            403,
        )
        self.gst.refresh_from_db()
        self.assertTrue(self.gst.is_active)

    def test_staff_do_not_see_edit_buttons(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("taxes:list")).content.decode()
        self.assertNotIn("Add tax", html)
        self.assertNotIn("Deactivate", html)


class TaxCrudTests(TaxTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_tax_and_audit(self):
        r = self.client.post(
            reverse("taxes:create"), {"name": "VAT 5", "tax_type": "percentage", "rate": "5"}
        )
        self.assertRedirects(r, reverse("taxes:list"))
        tax = Tax.objects.get(name="VAT 5")
        self.assertEqual(tax.rate, Decimal("5.00"))
        self.assertTrue(
            AuditLog.objects.filter(action="tax.created", entity_id=str(tax.pk)).exists()
        )

    def test_duplicate_name_shows_error(self):
        r = self.client.post(
            reverse("taxes:create"), {"name": "gst 18", "tax_type": "percentage", "rate": "5"}
        )
        self.assertContains(r, "already exists")
        self.assertEqual(Tax.objects.count(), 1)

    def test_percentage_over_100_rejected(self):
        r = self.client.post(
            reverse("taxes:create"), {"name": "Big", "tax_type": "percentage", "rate": "101"}
        )
        self.assertContains(r, "more than 100")

    def test_negative_rate_rejected(self):
        r = self.client.post(
            reverse("taxes:create"), {"name": "N", "tax_type": "fixed", "rate": "-3"}
        )
        self.assertContains(r, "negative")

    def test_edit_records_before_and_after(self):
        self.client.post(
            reverse("taxes:edit", args=[self.gst.pk]),
            {"name": "GST 18", "tax_type": "percentage", "rate": "12"},
        )
        self.gst.refresh_from_db()
        self.assertEqual(self.gst.rate, Decimal("12.00"))
        entry = AuditLog.objects.get(action="tax.updated")
        self.assertEqual(entry.details["changed"]["rate"], ["18.00", "12.00"])

    def test_edit_without_changes_writes_no_audit(self):
        self.client.post(
            reverse("taxes:edit", args=[self.gst.pk]),
            {"name": "GST 18", "tax_type": "percentage", "rate": "18"},
        )
        self.assertFalse(AuditLog.objects.filter(action="tax.updated").exists())

    def test_deactivate_and_reactivate(self):
        self.client.post(reverse("taxes:toggle_active", args=[self.gst.pk]), {"active": "0"})
        self.gst.refresh_from_db()
        self.assertFalse(self.gst.is_active)
        self.client.post(reverse("taxes:toggle_active", args=[self.gst.pk]), {"active": "1"})
        self.gst.refresh_from_db()
        self.assertTrue(self.gst.is_active)
        self.assertEqual(AuditLog.objects.filter(action__startswith="tax.").count(), 2)

    def test_search(self):
        Tax.objects.create(name="Service Fee", tax_type="fixed", rate=Decimal("10"))
        html = self.client.get(reverse("taxes:list"), {"q": "fee"}).content.decode()
        self.assertIn("Service Fee", html)
        self.assertNotIn("GST 18", html)


class TaxPreviewTests(TaxTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_preview_applies_active_taxes_only(self):
        Tax.objects.create(name="Old", tax_type="percentage", rate=Decimal("50"), is_active=False)
        html = self.client.get(reverse("taxes:preview"), {"amount": "1000"}).content.decode()
        self.assertIn("₹180.00", html)
        self.assertIn("₹1,180.00", html)
        self.assertNotIn("Old", html)

    def test_preview_rejects_garbage(self):
        self.assertContains(
            self.client.get(reverse("taxes:preview"), {"amount": "abc"}), "positive amount"
        )
        self.assertContains(
            self.client.get(reverse("taxes:preview"), {"amount": "-5"}), "positive amount"
        )

    def test_preview_blocked_for_customers(self):
        self.client.force_login(self.customer)
        self.assertEqual(
            self.client.get(reverse("taxes:preview"), {"amount": "5"}).status_code, 403
        )


class TaxServiceGuardTests(TaxTestCase):
    def test_service_rechecks_role(self):
        with self.assertRaises(PermissionDenied):
            services.create_tax(
                actor=self.staff, name="X", tax_type="percentage", rate=Decimal("1")
            )
        with self.assertRaises(PermissionDenied):
            services.set_tax_active(actor=self.customer, tax=self.gst, active=False)

    def test_duplicate_raises_domain_error(self):
        with self.assertRaises(DuplicateError):
            services.create_tax(
                actor=self.admin, name="GST 18", tax_type="percentage", rate=Decimal("1")
            )
