from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.accounts.validators import StrongPasswordValidator


class StrongPasswordValidatorTests(SimpleTestCase):
    def setUp(self):
        self.validator = StrongPasswordValidator()

    def codes(self, password):
        try:
            self.validator.validate(password)
        except ValidationError as exc:
            return {e.code for e in exc.error_list}
        return set()

    def test_accepts_valid_password(self):
        self.assertEqual(self.codes("Abcdef1!x"), set())  # 9 chars

    def test_exactly_eight_characters_is_rejected(self):
        self.assertIn("password_too_short", self.codes("Abcde1!x"))  # 8 chars

    def test_requires_uppercase(self):
        self.assertEqual(self.codes("abcdefgh1!"), {"password_no_upper"})

    def test_requires_lowercase(self):
        self.assertEqual(self.codes("ABCDEFGH1!"), {"password_no_lower"})

    def test_requires_special_character(self):
        self.assertEqual(self.codes("Abcdefgh12"), {"password_no_special"})

    def test_space_is_not_a_special_character(self):
        self.assertIn("password_no_special", self.codes("Abcdefgh 12"))

    def test_reports_every_failed_rule_at_once(self):
        self.assertEqual(
            self.codes("abc"),
            {"password_too_short", "password_no_upper", "password_no_special"},
        )
