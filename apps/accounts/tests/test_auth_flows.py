import re

from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import CustomerProfile, Role, User

GOOD_PASSWORD = "Str0ng!Pass"


class AuthTestCase(TestCase):
    def setUp(self):
        cache.clear()  # rate-limit counters live in the cache


class SignupTests(AuthTestCase):
    def post(self, **overrides):
        data = {
            "full_name": "Asha Nair",
            "email": "asha@example.com",
            "company_name": "Nair Traders",
            "password1": GOOD_PASSWORD,
            "password2": GOOD_PASSWORD,
        }
        data.update(overrides)
        return self.client.post(reverse("accounts:signup"), data)

    def test_signup_creates_customer_with_profile_and_logs_in(self):
        response = self.post()
        self.assertRedirects(response, reverse("core:home"))
        user = User.objects.get(email="asha@example.com")
        self.assertEqual(user.role, Role.CUSTOMER)
        self.assertEqual(user.profile.company_name, "Nair Traders")
        self.assertNotEqual(user.password, GOOD_PASSWORD)  # hashed
        self.assertTrue(user.password.startswith("argon2"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        self.post()
        self.client.logout()
        response = self.post(email="ASHA@Example.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_is_rejected(self):
        response = self.post(password1="weakpass", password2="weakpass")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uppercase")
        self.assertFalse(User.objects.exists())

    def test_mismatched_passwords_are_rejected(self):
        response = self.post(password2="Different1!")
        self.assertContains(response, "do not match")
        self.assertFalse(User.objects.exists())

    def test_signup_cannot_choose_a_role(self):
        self.post(role="admin", is_staff="on")
        user = User.objects.get(email="asha@example.com")
        self.assertEqual(user.role, Role.CUSTOMER)
        self.assertFalse(user.is_staff)


class DatabaseConstraintTests(TestCase):
    def test_case_variant_emails_collide_in_the_database(self):
        User.objects.create_user("a@x.com", GOOD_PASSWORD, full_name="A")
        # Bypass save() normalisation entirely with a queryset update.
        other = User.objects.create_user("b@x.com", GOOD_PASSWORD, full_name="B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=other.pk).update(email="A@X.com")

    def test_invalid_role_is_rejected_by_the_database(self):
        user = User.objects.create_user("a@x.com", GOOD_PASSWORD, full_name="A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=user.pk).update(role="superhero")

    def test_deleting_user_removes_profile(self):
        user = User.objects.create_user("a@x.com", GOOD_PASSWORD, full_name="A")
        CustomerProfile.objects.create(user=user)
        user.delete()
        self.assertFalse(CustomerProfile.objects.exists())


class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("asha@example.com", GOOD_PASSWORD, full_name="Asha")

    def login(self, email="asha@example.com", password=GOOD_PASSWORD, **extra):
        return self.client.post(
            reverse("accounts:login"), {"username": email, "password": password, **extra}
        )

    def test_login_is_case_insensitive_on_email(self):
        response = self.login(email="ASHA@Example.COM")
        self.assertRedirects(response, reverse("core:home"), fetch_redirect_response=False)

    def test_wrong_password_shows_generic_error(self):
        response = self.login(password="nope")
        self.assertContains(response, "Incorrect email or password")

    def test_home_requires_login(self):
        response = self.client.get(reverse("core:home"))
        self.assertRedirects(
            response, f"{reverse('accounts:login')}?next={reverse('core:home')}",
            fetch_redirect_response=False,
        )

    def test_open_redirect_is_blocked(self):
        response = self.login(next="https://evil.example.com/")
        self.assertRedirects(response, reverse("core:home"), fetch_redirect_response=False)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_is_rate_limited(self):
        for _ in range(10):
            self.login(password="wrong")
        self.assertEqual(self.login(password="wrong").status_code, 429)


class PasswordResetTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("asha@example.com", GOOD_PASSWORD, full_name="Asha")

    def request_reset(self, email):
        return self.client.post(reverse("accounts:password_reset"), {"email": email})

    def test_link_is_emailed_to_the_registered_address(self):
        response = self.request_reset("asha@example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["asha@example.com"])
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    def test_unknown_email_gets_same_response_and_no_mail(self):
        response = self.request_reset("nobody@example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_flow_and_link_is_single_use(self):
        self.request_reset("asha@example.com")
        path = re.search(r"/accounts/reset/\S+", mail.outbox[0].body).group(0)

        page = self.client.get(path, follow=True)  # token is swapped for a session URL
        set_url = page.redirect_chain[-1][0]
        new_password = "Brand#New9pass"
        done = self.client.post(
            set_url, {"new_password1": new_password, "new_password2": new_password}
        )
        self.assertRedirects(done, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

        reused = self.client.get(path, follow=True)
        self.assertContains(reused, "Link no longer valid")

    def test_reset_enforces_password_policy(self):
        self.request_reset("asha@example.com")
        path = re.search(r"/accounts/reset/\S+", mail.outbox[0].body).group(0)
        set_url = self.client.get(path, follow=True).redirect_chain[-1][0]
        response = self.client.post(
            set_url, {"new_password1": "weakweak", "new_password2": "weakweak"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uppercase")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(GOOD_PASSWORD))

    def test_reset_requests_are_rate_limited(self):
        for _ in range(5):
            self.request_reset("asha@example.com")
        self.assertEqual(self.request_reset("asha@example.com").status_code, 429)
