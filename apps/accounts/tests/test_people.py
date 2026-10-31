from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.accounts import services
from apps.accounts.models import CustomerProfile, Role, User
from apps.core.models import AuditLog

PW = "Str0ng!Pass"


def make_user(email, role, name=None, **extra):
    user = User.objects.create_user(email, PW, full_name=name or email.split("@")[0], role=role, **extra)
    if role == Role.CUSTOMER:
        CustomerProfile.objects.create(user=user, company_name=f"{user.full_name} Co")
    return user


class PeopleTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("admin@x.com", Role.ADMIN, "Ada Admin")
        self.staff = make_user("staff@x.com", Role.STAFF, "Sam Staff")
        self.customer = make_user("cust@x.com", Role.CUSTOMER, "Cara Customer")


class AccessControlTests(PeopleTestCase):
    def status(self, user, url_name, method="get", *args):
        if user:
            self.client.force_login(user)
        else:
            self.client.logout()
        return getattr(self.client, method)(reverse(url_name, args=args)).status_code

    def test_staff_pages_are_admin_only(self):
        self.assertEqual(self.status(None, "staff:list"), 302)  # to login
        self.assertEqual(self.status(self.customer, "staff:list"), 403)
        self.assertEqual(self.status(self.staff, "staff:list"), 403)
        self.assertEqual(self.status(self.staff, "staff:create"), 403)
        self.assertEqual(self.status(self.admin, "staff:list"), 200)
        self.assertEqual(self.status(self.admin, "staff:create"), 200)

    def test_staff_cannot_toggle_staff_accounts(self):
        other = make_user("other@x.com", Role.STAFF)
        self.assertEqual(self.status(self.staff, "staff:toggle_active", "post", other.pk), 403)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_customer_directory_is_internal_only(self):
        self.assertEqual(self.status(None, "customers:list"), 302)
        self.assertEqual(self.status(self.customer, "customers:list"), 403)
        self.assertEqual(self.status(self.staff, "customers:list"), 200)
        self.assertEqual(self.status(self.admin, "customers:list"), 200)

    def test_403_page_is_friendly(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("staff:list"))
        self.assertContains(response, "don't have access", status_code=403)

    def test_404_page_renders_for_anonymous(self):
        response = self.client.get("/definitely/not/here/")
        self.assertContains(response, "Page not found", status_code=404)


class SidebarTests(PeopleTestCase):
    def labels(self, user):
        self.client.force_login(user)
        html = self.client.get(reverse("core:home")).content.decode()
        return {
            "Customers": 'href="/customers/"' in html,
            "Staff": 'href="/staff/"' in html,
        }

    def test_admin_sees_everything(self):
        self.assertEqual(self.labels(self.admin), {"Customers": True, "Staff": True})

    def test_staff_sees_customers_not_staff(self):
        self.assertEqual(self.labels(self.staff), {"Customers": True, "Staff": False})

    def test_customer_sees_neither(self):
        self.assertEqual(self.labels(self.customer), {"Customers": False, "Staff": False})

    def test_current_section_is_highlighted(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("staff:list")).content.decode()
        self.assertEqual(html.count('aria-current="page"'), 1)


class StaffCreationTests(PeopleTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def post(self, **overrides):
        data = {"full_name": "New Person", "email": "new@x.com", "password": "Temp#Pass99"}
        data.update(overrides)
        return self.client.post(reverse("staff:create"), data)

    def test_admin_creates_staff_member(self):
        response = self.post()
        self.assertRedirects(response, reverse("staff:list"))
        user = User.objects.get(email="new@x.com")
        self.assertEqual(user.role, Role.STAFF)
        self.assertTrue(user.password.startswith("argon2"))
        self.assertTrue(user.check_password("Temp#Pass99"))
        entry = AuditLog.objects.get(action="staff.created")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.entity_id, str(user.pk))
        self.assertNotIn("password", str(entry.details))

    def test_role_cannot_be_forged_in_the_request(self):
        self.post(role="admin", is_superuser="on", is_staff="on")
        user = User.objects.get(email="new@x.com")
        self.assertEqual(user.role, Role.STAFF)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_duplicate_email_rejected_case_insensitively(self):
        response = self.post(email="STAFF@x.com")
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.filter(email__iexact="staff@x.com").count(), 1)

    def test_password_policy_is_enforced(self):
        response = self.post(password="weak")
        self.assertContains(response, "uppercase")
        self.assertFalse(User.objects.filter(email="new@x.com").exists())

    def test_service_refuses_non_admin_actor(self):
        for actor in (self.staff, self.customer):
            with self.assertRaises(PermissionDenied):
                services.create_staff(actor=actor, email="z@x.com", password=PW, full_name="Z")
        self.assertFalse(User.objects.filter(email="z@x.com").exists())


class StaffActivationTests(PeopleTestCase):
    def toggle(self, user, active, htmx=False):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(
            reverse("staff:toggle_active", args=[user.pk]), {"active": "1" if active else "0"}, **headers
        )

    def test_admin_deactivates_and_reactivates(self):
        self.client.force_login(self.admin)
        self.toggle(self.staff, False)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)
        self.toggle(self.staff, True)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["staff.deactivated", "staff.activated"])

    def test_repeating_the_same_state_writes_no_extra_audit_entry(self):
        self.client.force_login(self.admin)
        self.toggle(self.staff, False)
        self.toggle(self.staff, False)
        self.assertEqual(AuditLog.objects.filter(action="staff.deactivated").count(), 1)

    def test_htmx_returns_row_and_toast_trigger(self):
        self.client.force_login(self.admin)
        response = self.toggle(self.staff, False, htmx=True)
        self.assertContains(response, "Inactive")
        self.assertIn("toast", response["HX-Trigger"])

    def test_deactivated_user_is_signed_out_immediately(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("customers:list")).status_code, 200)
        services.set_staff_active(actor=self.admin, user=self.staff, active=False)
        response = self.client.get(reverse("customers:list"))
        self.assertEqual(response.status_code, 302)  # session no longer valid

    def test_admins_and_customers_are_not_reachable_through_this_endpoint(self):
        self.client.force_login(self.admin)
        for target in (self.admin, self.customer):
            self.assertEqual(self.toggle(target, False).status_code, 404)
            target.refresh_from_db()
            self.assertTrue(target.is_active)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff:toggle_active", args=[self.staff.pk]))
        self.assertEqual(response.status_code, 405)


class DirectoryTests(PeopleTestCase):
    def test_search_filters_customers_by_company(self):
        self.client.force_login(self.staff)
        make_user("other@x.com", Role.CUSTOMER, "Zed Other")
        response = self.client.get(reverse("customers:list"), {"q": "Cara"})
        self.assertContains(response, "Cara Customer")
        self.assertNotContains(response, "Zed Other")

    def test_htmx_request_gets_only_the_fragment(self):
        self.client.force_login(self.staff)
        full = self.client.get(reverse("customers:list"))
        part = self.client.get(reverse("customers:list"), HTTP_HX_REQUEST="true")
        self.assertContains(full, "<aside")
        self.assertNotContains(part, "<aside")
        self.assertContains(part, "Cara Customer")

    def test_pagination(self):
        self.client.force_login(self.admin)
        for i in range(20):
            make_user(f"bulk{i:02d}@x.com", Role.CUSTOMER, f"Bulk {i:02d}")
        page1 = self.client.get(reverse("customers:list"))
        page2 = self.client.get(reverse("customers:list"), {"page": 2})
        self.assertContains(page1, "Page 1 of 2")
        self.assertContains(page2, "Page 2 of 2")
        self.assertContains(self.client.get(reverse("customers:list"), {"page": 999}), "Page 2 of 2")

    def test_search_term_is_escaped(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("customers:list"), {"q": "<script>alert(1)</script>"})
        self.assertNotContains(response, "<script>alert(1)</script>")


class PasswordChangeTests(PeopleTestCase):
    def test_user_changes_own_password(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("accounts:password_change"),
            {"old_password": PW, "new_password1": "Newer#Pass77", "new_password2": "Newer#Pass77"},
        )
        self.assertRedirects(response, reverse("core:home"))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("Newer#Pass77"))

    def test_requires_login(self):
        self.assertEqual(self.client.get(reverse("accounts:password_change")).status_code, 302)
