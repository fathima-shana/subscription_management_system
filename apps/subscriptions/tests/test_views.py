from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import Role
from apps.core.testing import make_user
from apps.discounts.models import Discount
from apps.subscriptions import services
from apps.subscriptions.models import QuotationTemplate, Subscription

from .helpers import SubsTestCase


class AccessTests(SubsTestCase):
    def code(self, user, name, method="get", *args, data=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_internal_pages(self):
        sub = self.make_sub()
        t = services.create_template(actor=self.admin, name="T", plan=self.plan)
        pages = [
            ("subscriptions:list", ()),
            ("subscriptions:create", ()),
            ("subscriptions:detail", (sub.pk,)),
            ("subscriptions:edit", (sub.pk,)),
            ("qtemplates:list", ()),
            ("qtemplates:detail", (t.pk,)),
        ]
        for name, args in pages:
            self.assertEqual(self.code(None, name, "get", *args), 302, name)
            self.assertEqual(self.code(self.customer_user, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.staff, name, "get", *args), 200, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)

    def test_customer_cannot_post_actions(self):
        sub = self.make_sub()
        for name in (
            "subscriptions:transition",
            "subscriptions:renew",
            "subscriptions:line_create",
        ):
            self.assertEqual(
                self.code(self.customer_user, name, "post", sub.pk, data={"to": "quotation"}),
                403,
                name,
            )
        sub.refresh_from_db()
        self.assertEqual(sub.status, "draft")

    def test_template_changes_are_admin_only(self):
        t = services.create_template(actor=self.admin, name="T", plan=self.plan)
        line = services.add_template_line(
            actor=self.admin, template=t, product=self.crm, variant=None, quantity=1
        )
        for name, args in [("qtemplates:create", ()), ("qtemplates:edit", (t.pk,))]:
            self.assertEqual(self.code(self.staff, name, "get", *args), 403, name)
            self.assertEqual(self.code(self.admin, name, "get", *args), 200, name)
        for name, args in [
            ("qtemplates:toggle_active", (t.pk,)),
            ("qtemplates:line_create", (t.pk,)),
            ("qtemplates:line_delete", (line.pk,)),
        ]:
            self.assertEqual(
                self.code(self.staff, name, "post", *args, data={"active": "0"}), 403, name
            )
        self.assertEqual(t.lines.count(), 1)

    def test_staff_do_not_see_template_edit_buttons(self):
        t = services.create_template(actor=self.admin, name="T", plan=self.plan)
        self.client.force_login(self.staff)
        html = self.client.get(reverse("qtemplates:detail", args=[t.pk])).content.decode()
        self.assertNotIn("Add a product", html)
        self.assertNotIn("Deactivate", html)

    def test_sidebar_links(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn('href="/subscriptions/"', html)
        self.assertIn('href="/quotation-templates/"', html)
        self.client.force_login(self.customer_user)
        self.assertNotIn(
            'href="/subscriptions/"', self.client.get(reverse("core:home")).content.decode()
        )


class ListTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_status_filter_and_search(self):
        a = self.make_sub()
        b = self.move(self.make_sub(), "quotation")
        html = self.client.get(
            reverse("subscriptions:list"), {"status": "quotation"}
        ).content.decode()
        self.assertIn(b.number, html)
        self.assertNotIn(a.number, html)
        html = self.client.get(reverse("subscriptions:list"), {"q": "cara"}).content.decode()
        self.assertIn(a.number, html)
        html = self.client.get(reverse("subscriptions:list"), {"q": "nobody"}).content.decode()
        self.assertIn("No subscriptions match", html)

    def test_pagination_keeps_status_filter(self):
        for _ in range(17):
            self.move(self.make_sub(), "quotation")
        html = self.client.get(
            reverse("subscriptions:list"), {"status": "quotation"}
        ).content.decode()
        self.assertIn("Page 1 of 2", html)
        self.assertIn("status=quotation&page=2", html)

    def test_htmx_returns_fragment(self):
        self.make_sub()
        r = self.client.get(reverse("subscriptions:list"), HTTP_HX_REQUEST="true")
        self.assertNotContains(r, "<html")


class CreateAndEditTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def data(self, **over):
        data = {
            "customer": self.customer.pk,
            "template": "",
            "plan": self.plan.pk,
            "payment_term": self.term.pk,
            "start_date": self.today.isoformat(),
            "expiry_date": "",
            "notes": "Hello",
        }
        data.update(over)
        return data

    def test_create_plain(self):
        r = self.client.post(reverse("subscriptions:create"), self.data())
        sub = Subscription.objects.get()
        self.assertRedirects(r, reverse("subscriptions:detail", args=[sub.pk]))
        self.assertEqual((sub.status, sub.created_by), ("draft", self.staff))

    def test_create_from_template_needs_no_plan(self):
        t = services.create_template(actor=self.admin, name="T", plan=self.plan)
        services.add_template_line(
            actor=self.admin, template=t, product=self.pos, variant=None, quantity=2
        )
        self.client.post(reverse("subscriptions:create"), self.data(template=t.pk, plan=""))
        sub = Subscription.objects.get()
        self.assertEqual(sub.lines.get().quantity, 2)

    def test_plan_or_template_required(self):
        r = self.client.post(reverse("subscriptions:create"), self.data(plan=""))
        self.assertContains(r, "Pick a plan, or choose a template")

    def test_expiry_before_start_rejected(self):
        r = self.client.post(
            reverse("subscriptions:create"), self.data(expiry_date=self.days(-1).isoformat())
        )
        self.assertContains(r, "cannot be before the start date")

    def test_inactive_customer_and_plan_not_offered(self):
        self.customer_user.is_active = False
        self.customer_user.save()
        self.assertContains(
            self.client.post(reverse("subscriptions:create"), self.data()), "Select a valid choice"
        )
        self.customer_user.is_active = True
        self.customer_user.save()
        self.plan.is_active = False
        self.plan.save()
        self.assertContains(
            self.client.post(reverse("subscriptions:create"), self.data()), "Select a valid choice"
        )

    def test_only_customers_are_offered(self):
        r = self.client.post(reverse("subscriptions:create"), self.data(customer=self.staff.pk))
        self.assertContains(r, "Select a valid choice")

    def test_edit_while_draft_and_locked_after_confirm(self):
        sub = self.make_sub()
        r = self.client.post(
            reverse("subscriptions:edit", args=[sub.pk]),
            {
                "plan": self.plan.pk,
                "payment_term": self.term.pk,
                "start_date": self.today.isoformat(),
                "expiry_date": "",
                "quote_valid_until": "",
                "notes": "Changed",
            },
        )
        self.assertRedirects(r, reverse("subscriptions:detail", args=[sub.pk]))
        sub.refresh_from_db()
        self.assertEqual(sub.notes, "Changed")
        self.move(sub, "quotation", "confirmed")
        r = self.client.get(reverse("subscriptions:edit", args=[sub.pk]), follow=True)
        self.assertContains(r, "Only draft and quotation subscriptions can be edited")


class DetailAndActionTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.sub = self.make_sub()
        self.detail = reverse("subscriptions:detail", args=[self.sub.pk])

    def test_detail_shows_lines_totals_and_history(self):
        html = self.client.get(self.detail).content.decode()
        self.assertIn(self.sub.number, html)
        self.assertIn("₹1,000.00", html)  # amount
        self.assertIn("₹1,180.00", html)  # with 18% GST
        self.assertIn("Send quotation", html)
        self.assertNotIn(">Renew<", html)

    def test_detail_shows_best_discount(self):
        Discount.objects.create(name="Welcome", discount_type="percentage", value=Decimal("10"))
        html = self.client.get(self.detail).content.decode()
        self.assertIn("Discount: Welcome", html)
        self.assertIn("₹1,062.00", html)  # (1000 - 100) + 18% of 900

    def test_transition_success_and_failure_messages(self):
        r = self.client.post(
            reverse("subscriptions:transition", args=[self.sub.pk]),
            {"to": "quotation"},
            follow=True,
        )
        self.assertContains(r, "is now quotation")
        self.plan.min_quantity = 9
        self.plan.save()
        r = self.client.post(
            reverse("subscriptions:transition", args=[self.sub.pk]),
            {"to": "confirmed"},
            follow=True,
        )
        self.assertContains(r, "at least 9")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "quotation")

    def test_get_on_transition_is_not_allowed(self):
        self.assertEqual(
            self.client.get(reverse("subscriptions:transition", args=[self.sub.pk])).status_code,
            405,
        )

    def test_renew_button_and_flow(self):
        self.move(self.sub, "quotation", "confirmed", "active")
        self.assertIn(">Renew<", self.client.get(self.detail).content.decode())
        r = self.client.post(reverse("subscriptions:renew", args=[self.sub.pk]), follow=True)
        self.assertContains(r, "was created as a draft")
        self.assertEqual(Subscription.objects.count(), 2)

    def test_paused_shows_resume_and_stepper_note(self):
        self.move(self.sub, "quotation", "confirmed", "active", "paused")
        html = self.client.get(self.detail).content.decode()
        self.assertIn("Resume", html)
        self.assertIn("(paused)", html)

    def test_unknown_subscription_is_404(self):
        self.assertEqual(
            self.client.get(reverse("subscriptions:detail", args=[99999])).status_code, 404
        )


class LineViewTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.sub = self.make_sub(with_line=False)
        self.url = reverse("subscriptions:line_create", args=[self.sub.pk])

    def test_add_line_htmx_returns_partial_with_totals(self):
        r = self.client.post(
            self.url,
            {"item": f"p{self.crm.pk}", "quantity": "2", "tax": ""},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "<html")
        self.assertContains(r, "₹2,360.00")
        self.assertIn("toast", r["HX-Trigger"])
        self.assertEqual(self.sub.lines.get().tax, self.gst)  # product default

    def test_variant_price_and_tax_choices(self):
        self.client.post(
            self.url,
            {"item": f"v{self.odoo.pk}", "quantity": "1", "tax": "none"},
            HTTP_HX_REQUEST="true",
        )
        line = self.sub.lines.get()
        self.assertEqual(
            (line.variant, line.unit_price, line.tax), (self.odoo, Decimal("1560.00"), None)
        )
        self.client.post(
            self.url,
            {"item": f"p{self.pos.pk}", "quantity": "1", "tax": str(self.gst.pk)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(self.sub.lines.get(product=self.pos).tax, self.gst)

    def test_bad_input_shows_errors_in_place(self):
        r = self.client.post(self.url, {"item": "", "quantity": "0"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "required")
        self.assertContains(r, "greater than or equal to 1")
        self.assertEqual(self.sub.lines.count(), 0)
        r = self.client.post(self.url, {"item": "p99999", "quantity": "1"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "Select a valid choice")

    def test_inactive_products_not_offered(self):
        self.pos.is_active = False
        self.pos.save()
        r = self.client.post(
            self.url, {"item": f"p{self.pos.pk}", "quantity": "1"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(r, "Select a valid choice")

    def test_add_without_js_redirects(self):
        r = self.client.post(self.url, {"item": f"p{self.pos.pk}", "quantity": "1", "tax": ""})
        self.assertRedirects(r, reverse("subscriptions:detail", args=[self.sub.pk]))

    def test_remove_line(self):
        services.add_line(
            actor=self.staff,
            subscription=self.sub,
            product=self.pos,
            variant=None,
            quantity=1,
            tax=None,
        )
        line = self.sub.lines.get()
        r = self.client.post(
            reverse("subscriptions:line_delete", args=[line.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sub.lines.count(), 0)

    def test_locked_subscription_refuses_line_changes(self):
        services.add_line(
            actor=self.staff,
            subscription=self.sub,
            product=self.pos,
            variant=None,
            quantity=1,
            tax=None,
        )
        self.move(self.sub, "quotation", "confirmed")
        r = self.client.post(
            self.url, {"item": f"p{self.crm.pk}", "quantity": "1"}, HTTP_HX_REQUEST="true"
        )
        self.assertIn("locked", r["HX-Trigger"])  # shown as an error toast
        self.assertNotContains(r, "Add a product")  # the add form is gone
        line = self.sub.lines.get()
        self.client.post(
            reverse("subscriptions:line_delete", args=[line.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(self.sub.lines.count(), 1)

    def test_line_of_another_subscription_cannot_be_reached_by_customer(self):
        services.add_line(
            actor=self.staff,
            subscription=self.sub,
            product=self.pos,
            variant=None,
            quantity=1,
            tax=None,
        )
        line = self.sub.lines.get()
        self.client.force_login(self.customer_user)
        self.assertEqual(
            self.client.post(reverse("subscriptions:line_delete", args=[line.pk])).status_code, 403
        )


class TemplateViewTests(SubsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_edit_toggle_template(self):
        r = self.client.post(
            reverse("qtemplates:create"),
            {"name": "Starter", "plan": self.plan.pk, "validity_days": "14", "notes": ""},
        )
        t = QuotationTemplate.objects.get()
        self.assertRedirects(r, reverse("qtemplates:detail", args=[t.pk]))
        self.client.post(
            reverse("qtemplates:edit", args=[t.pk]),
            {"name": "Starter", "plan": self.plan.pk, "validity_days": "21", "notes": ""},
        )
        t.refresh_from_db()
        self.assertEqual(t.validity_days, 21)
        self.client.post(reverse("qtemplates:toggle_active", args=[t.pk]), {"active": "0"})
        t.refresh_from_db()
        self.assertFalse(t.is_active)

    def test_validation(self):
        services.create_template(actor=self.admin, name="Starter", plan=self.plan)
        r = self.client.post(
            reverse("qtemplates:create"),
            {"name": "starter", "plan": self.plan.pk, "validity_days": "0"},
        )
        self.assertContains(r, "already exists")
        self.assertContains(r, "at least 1")

    def test_inactive_template_not_offered_on_new_subscription(self):
        t = services.create_template(actor=self.admin, name="Old", plan=self.plan)
        services.set_template_active(actor=self.admin, template=t, active=False)
        r = self.client.post(
            reverse("subscriptions:create"),
            {
                "customer": self.customer.pk,
                "template": t.pk,
                "plan": "",
                "payment_term": self.term.pk,
                "start_date": self.today.isoformat(),
            },
        )
        self.assertContains(r, "Select a valid choice")

    def test_template_line_add_merge_remove_htmx(self):
        t = services.create_template(actor=self.admin, name="T", plan=self.plan)
        url = reverse("qtemplates:line_create", args=[t.pk])
        self.client.post(url, {"item": f"p{self.crm.pk}", "quantity": "1"}, HTTP_HX_REQUEST="true")
        r = self.client.post(
            url, {"item": f"p{self.crm.pk}", "quantity": "2"}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(t.lines.get().quantity, 3)
        self.assertContains(r, "₹3,000.00")
        self.client.post(
            reverse("qtemplates:line_delete", args=[t.lines.get().pk]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(t.lines.count(), 0)


class PaymentTermTests(SubsTestCase):
    def test_default_terms_are_seeded(self):
        from apps.subscriptions.models import PaymentTerm

        days = dict(PaymentTerm.objects.values_list("name", "days"))
        self.assertEqual(
            days, {"Immediate payment": 0, "15 days": 15, "30 days": 30, "45 days": 45}
        )
