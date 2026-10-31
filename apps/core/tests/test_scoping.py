from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.accounts.models import CustomerProfile, Role, User
from apps.core.scoping import scope_to_customer


class ScopeToCustomerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def make(email, role):
            user = User.objects.create_user(email, "Str0ng!Pass", full_name=email, role=role)
            if role == Role.CUSTOMER:
                CustomerProfile.objects.create(user=user)
            return user

        cls.admin = make("admin@x.com", Role.ADMIN)
        cls.staff = make("staff@x.com", Role.STAFF)
        cls.alice = make("alice@x.com", Role.CUSTOMER)
        cls.bob = make("bob@x.com", Role.CUSTOMER)

    def owners(self, user):
        qs = scope_to_customer(CustomerProfile.objects.all(), user, "user")
        return set(qs.values_list("user__email", flat=True))

    def test_customer_sees_only_their_own_rows(self):
        self.assertEqual(self.owners(self.alice), {"alice@x.com"})
        self.assertEqual(self.owners(self.bob), {"bob@x.com"})

    def test_internal_users_see_all_rows(self):
        everyone = {"alice@x.com", "bob@x.com"}
        self.assertEqual(self.owners(self.admin), everyone)
        self.assertEqual(self.owners(self.staff), everyone)

    def test_anonymous_and_inactive_users_see_nothing(self):
        self.assertEqual(self.owners(AnonymousUser()), set())
        self.alice.is_active = False
        self.assertEqual(self.owners(self.alice), set())


class ErrorPageTests(TestCase):
    def test_500_page_is_standalone_and_friendly(self):
        from django.test import RequestFactory
        from django.views.defaults import server_error

        response = server_error(RequestFactory().get("/boom/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.content)
