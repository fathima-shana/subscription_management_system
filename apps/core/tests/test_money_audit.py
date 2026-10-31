from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Role
from apps.core import audit
from apps.core.models import AuditLog
from apps.core.money import D, quantize
from apps.core.testing import make_user


class MoneyTests(SimpleTestCase):
    def test_rounds_half_up_not_bankers(self):
        self.assertEqual(quantize("0.005"), Decimal("0.01"))
        self.assertEqual(quantize("0.015"), Decimal("0.02"))
        self.assertEqual(quantize("2.675"), Decimal("2.68"))  # float would give 2.67

    def test_floats_convert_through_text(self):
        self.assertEqual(D(0.1) + D(0.2), Decimal("0.3"))

    def test_invalid_amount_raises(self):
        with self.assertRaises(ValueError):
            D("abc")


class ApplyChangesTests(TestCase):
    def test_only_changed_fields_are_saved_and_reported(self):
        user = make_user("a@x.com", Role.STAFF, "Old Name")
        changed = audit.apply_changes(user, {"full_name": "New Name", "is_active": True})
        self.assertEqual(changed, {"full_name": ["Old Name", "New Name"]})
        user.refresh_from_db()
        self.assertEqual(user.full_name, "New Name")

    def test_no_change_returns_empty(self):
        user = make_user("b@x.com", Role.STAFF, "Same")
        self.assertEqual(audit.apply_changes(user, {"full_name": "Same"}), {})
        self.assertEqual(AuditLog.objects.count(), 0)
