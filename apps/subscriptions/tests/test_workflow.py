import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Role
from apps.catalog.models import Product
from apps.core.models import AuditLog, NumberSequence
from apps.core.sequences import next_number
from apps.core.testing import make_user
from apps.subscriptions import selectors, services
from apps.subscriptions.models import Status, Subscription, SubscriptionStatusHistory
from apps.subscriptions.workflow import available_transitions

from .helpers import SubsTestCase


class CreationTests(SubsTestCase):
    def test_numbers_are_sequential_and_formatted(self):
        a, b = self.make_sub(with_line=False), self.make_sub(with_line=False)
        self.assertEqual((a.number, b.number), ("SUB-00001", "SUB-00002"))

    def test_new_subscription_is_draft_with_history_and_audit(self):
        sub = self.make_sub(with_line=False)
        self.assertEqual(sub.status, Status.DRAFT)
        self.assertEqual(sub.history.get().to_status, "draft")
        self.assertTrue(
            AuditLog.objects.filter(action="subscription.created", entity_id=str(sub.pk)).exists()
        )

    def test_created_from_template_copies_plan_lines_and_validity(self):
        from apps.subscriptions.models import QuotationTemplate

        other_plan = self.plan.__class__.objects.create(name="Yearly", billing_period="yearly")
        t = services.create_template(
            actor=self.admin, name="Starter", plan=other_plan, validity_days=10
        )
        services.add_template_line(
            actor=self.admin, template=t, product=self.crm, variant=self.odoo, quantity=3
        )
        services.add_template_line(
            actor=self.admin, template=t, product=self.pos, variant=None, quantity=1
        )
        sub = services.create_subscription(
            actor=self.staff,
            customer=self.customer,
            plan=self.plan,
            payment_term=self.term,
            start_date=self.today,
            template=t,
        )
        self.assertEqual(sub.plan, other_plan)  # the template's plan wins
        self.assertEqual(sub.quote_valid_until, self.days(10))
        lines = {l.product.name: l for l in sub.lines.all()}
        self.assertEqual(lines["CRM"].quantity, 3)
        self.assertEqual(lines["CRM"].unit_price, Decimal("1560.00"))  # product + variant extra
        self.assertEqual(lines["CRM"].tax, self.gst)  # product default tax
        self.assertIsNone(lines["POS"].tax)

    def test_staff_and_admin_may_create_customer_may_not(self):
        self.make_sub(actor=self.admin, with_line=False)
        self.make_sub(actor=self.staff, with_line=False)
        with self.assertRaises(PermissionDenied):
            self.make_sub(actor=self.customer_user, with_line=False)


class NumberSequenceTests(TestCase):
    def test_rolled_back_number_is_reused(self):
        try:
            with transaction.atomic():
                self.assertEqual(next_number("x", "X"), "X-00001")
                raise RuntimeError
        except RuntimeError:
            pass
        self.assertEqual(next_number("x", "X"), "X-00001")  # no gap
        self.assertEqual(NumberSequence.objects.get(key="x").next_value, 2)


class LineTests(SubsTestCase):
    def test_price_is_a_snapshot(self):
        sub = self.make_sub()
        self.crm.sales_price = Decimal("2000")
        self.crm.save()
        self.assertEqual(sub.lines.get().unit_price, Decimal("1000.00"))

    def test_same_item_added_twice_increases_quantity(self):
        sub = self.make_sub()
        services.add_line(
            actor=self.staff, subscription=sub, product=self.crm, variant=None, quantity=2, tax=None
        )
        line = sub.lines.get()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.tax, self.gst)  # existing line keeps its tax

    def test_variant_is_a_separate_line(self):
        sub = self.make_sub()
        services.add_line(
            actor=self.staff,
            subscription=sub,
            product=self.crm,
            variant=self.odoo,
            quantity=1,
            tax=None,
        )
        self.assertEqual(sub.lines.count(), 2)

    def test_lines_locked_after_confirmation(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed")
        with self.assertRaises(services.LockedError):
            services.add_line(
                actor=self.staff,
                subscription=sub,
                product=self.pos,
                variant=None,
                quantity=1,
                tax=None,
            )
        with self.assertRaises(services.LockedError):
            services.remove_line(actor=self.staff, line=sub.lines.get())
        with self.assertRaises(services.LockedError):
            services.update_subscription(actor=self.staff, subscription=sub, notes="x")

    def test_remove_line_while_draft(self):
        sub = self.make_sub()
        services.remove_line(actor=self.staff, line=sub.lines.get())
        self.assertEqual(sub.lines.count(), 0)


class HappyPathTests(SubsTestCase):
    def test_full_lifecycle_with_history(self):
        sub = self.make_sub(start_date=self.days(-1))
        sub = self.move(sub, "quotation", "confirmed")
        self.assertIsNone(sub.next_billing_date)  # nothing to bill until active
        sub = self.move(sub, "active")
        self.assertEqual(sub.next_billing_date, self.days(-1))  # due on the start date
        sub = self.move(sub, "closed")
        self.assertIsNone(sub.next_billing_date)
        trail = [(h.from_status, h.to_status) for h in sub.history.all()]
        self.assertEqual(
            trail,
            [
                ("", "draft"),
                ("draft", "quotation"),
                ("quotation", "confirmed"),
                ("confirmed", "active"),
                ("active", "closed"),
            ],
        )
        self.assertEqual(AuditLog.objects.filter(action="subscription.status_changed").count(), 4)

    def test_quotation_can_go_back_to_draft(self):
        sub = self.move(self.make_sub(), "quotation", "draft")
        self.assertEqual(sub.status, "draft")


class InvalidMoveTests(SubsTestCase):
    def test_cannot_skip_steps(self):
        sub = self.make_sub()
        for bad in ("confirmed", "active", "paused", "closed"):
            with self.assertRaises(services.TransitionError, msg=bad):
                services.transition(actor=self.staff, subscription=sub, to=bad)
        sub.refresh_from_db()
        self.assertEqual(sub.status, "draft")

    def test_closed_is_final(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active", "closed")
        for to in Status.values:
            with self.assertRaises(services.TransitionError):
                services.transition(actor=self.staff, subscription=sub, to=to)

    def test_unknown_status(self):
        with self.assertRaises(services.TransitionError):
            services.transition(actor=self.staff, subscription=self.make_sub(), to="banana")

    def test_stale_object_cannot_repeat_a_move(self):
        sub = self.make_sub()
        stale = Subscription.objects.get(pk=sub.pk)
        services.transition(actor=self.staff, subscription=sub, to="quotation")
        with self.assertRaises(services.TransitionError):  # the row is re-read under lock
            services.transition(actor=self.staff, subscription=stale, to="quotation")
        self.assertEqual(
            SubscriptionStatusHistory.objects.filter(
                subscription=sub, to_status="quotation"
            ).count(),
            1,
        )

    def test_customer_cannot_transition(self):
        with self.assertRaises(PermissionDenied):
            services.transition(
                actor=self.customer_user, subscription=self.make_sub(), to="quotation"
            )

    def test_quotation_needs_a_line(self):
        sub = self.make_sub(with_line=False)
        with self.assertRaises(services.TransitionError) as ctx:
            services.transition(actor=self.staff, subscription=sub, to="quotation")
        self.assertIn("at least one product", ctx.exception.problems[0])


class ConfirmationGuardTests(SubsTestCase):
    def problems(self, sub):
        sub = self.move(sub, "quotation")
        with self.assertRaises(services.TransitionError) as ctx:
            services.transition(actor=self.staff, subscription=sub, to="confirmed")
        return " | ".join(ctx.exception.problems)

    def test_plan_minimum_quantity(self):
        self.plan.min_quantity = 5
        self.plan.save()
        self.assertIn("at least 5", self.problems(self.make_sub()))

    def test_inactive_plan(self):
        sub = self.make_sub()
        self.plan.is_active = False
        self.plan.save()
        self.assertIn("no longer active", self.problems(sub))

    def test_start_before_plan_window(self):
        self.plan.start_date = self.days(10)
        self.plan.save()
        self.assertIn("starts on", self.problems(self.make_sub()))

    def test_start_after_plan_window(self):
        self.plan.end_date = self.days(-10)
        self.plan.save()
        self.assertIn("ended on", self.problems(self.make_sub(start_date=self.today)))

    def test_expired_quotation(self):
        sub = self.make_sub(quote_valid_until=self.days(-1))
        self.assertIn("expired", self.problems(sub))

    def test_inactive_customer(self):
        sub = self.make_sub()
        self.customer_user.is_active = False
        self.customer_user.save()
        self.assertIn("deactivated", self.problems(sub))

    def test_all_problems_reported_together(self):
        self.plan.min_quantity = 5
        self.plan.save()
        sub = self.make_sub(quote_valid_until=self.days(-1))
        text = self.problems(sub)
        self.assertIn("at least 5", text)
        self.assertIn("expired", text)

    def test_quote_valid_until_is_inclusive(self):
        sub = self.move(self.make_sub(quote_valid_until=self.today), "quotation", "confirmed")
        self.assertEqual(sub.status, "confirmed")


class PlanRuleTests(SubsTestCase):
    def active(self):
        return self.move(self.make_sub(), "quotation", "confirmed", "active")

    def test_pause_and_resume_when_pausable(self):
        sub = self.active()
        self.assertIn(("paused", "Pause"), available_transitions(sub))
        sub = self.move(sub, "paused")
        self.assertIn(("active", "Resume"), available_transitions(sub))
        self.move(sub, "active")

    def test_pause_refused_when_not_pausable(self):
        self.plan.pausable = False
        self.plan.save()
        sub = self.active()
        self.assertNotIn("paused", [t for t, _ in available_transitions(sub)])
        with self.assertRaises(services.TransitionError):
            services.transition(actor=self.staff, subscription=sub, to="paused")

    def test_early_close_refused_when_not_closable_but_system_may_close(self):
        self.plan.closable = False
        self.plan.save()
        sub = self.active()
        self.assertNotIn("closed", [t for t, _ in available_transitions(sub)])
        with self.assertRaises(services.TransitionError):
            services.transition(actor=self.staff, subscription=sub, to="closed")
        closed = services.transition(
            actor=None, subscription=sub, to="closed", system=True, note="Reached end date"
        )
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.history.last().note, "Reached end date")

    def test_resume_does_not_back_bill_paused_time(self):
        sub = self.make_sub(start_date=self.days(-30))
        sub = self.move(sub, "quotation", "confirmed", "active", "paused")
        self.assertEqual(sub.next_billing_date, self.days(-30))
        sub = self.move(sub, "active")
        self.assertEqual(sub.next_billing_date, self.today)

    def test_resume_keeps_a_future_billing_date(self):
        sub = self.make_sub(start_date=self.days(5))
        sub = self.move(sub, "quotation", "confirmed", "active", "paused", "active")
        self.assertEqual(sub.next_billing_date, self.days(5))

    def test_buttons_per_status(self):
        sub = self.make_sub()
        self.assertEqual([t for t, _ in available_transitions(sub)], ["quotation"])
        sub = self.move(sub, "quotation")
        self.assertEqual({t for t, _ in available_transitions(sub)}, {"confirmed", "draft"})


class RenewTests(SubsTestCase):
    def test_renew_active_subscription(self):
        sub = self.make_sub(start_date=self.days(-29), expiry_date=self.today)
        sub = self.move(sub, "quotation", "confirmed", "active")
        self.crm.sales_price = Decimal("1200")
        self.crm.save()
        new = services.renew(actor=self.staff, subscription=sub)
        self.assertEqual(new.status, "draft")
        self.assertEqual(new.renewed_from, sub)
        self.assertEqual(new.start_date, self.days(1))
        self.assertEqual(new.expiry_date, self.days(1) + datetime.timedelta(days=29))  # same length
        self.assertEqual(new.lines.get().unit_price, Decimal("1200.00"))  # today's price
        self.assertIn("Renewed from", new.history.get().note)

    def test_open_ended_renewal_starts_today(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        new = services.renew(actor=self.staff, subscription=sub)
        self.assertEqual((new.start_date, new.expiry_date), (self.today, None))

    def test_not_renewable_plan(self):
        self.plan.renewable = False
        self.plan.save()
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        with self.assertRaises(services.TransitionError):
            services.renew(actor=self.staff, subscription=sub)

    def test_draft_cannot_be_renewed(self):
        with self.assertRaises(services.TransitionError):
            services.renew(actor=self.staff, subscription=self.make_sub())

    def test_inactive_products_are_skipped_and_reported(self):
        sub = self.make_sub()
        services.add_line(
            actor=self.staff, subscription=sub, product=self.pos, variant=None, quantity=1, tax=None
        )
        sub = self.move(sub, "quotation", "confirmed", "active")
        self.pos.is_active = False
        self.pos.save()
        new = services.renew(actor=self.staff, subscription=sub)
        self.assertEqual([l.product.name for l in new.lines.all()], ["CRM"])
        self.assertIn("not for sale any more: POS", new.history.get().note)

    def test_all_products_gone(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        Product.objects.update(is_active=False)
        with self.assertRaises(services.TransitionError):
            services.renew(actor=self.staff, subscription=sub)


class ScopingTests(SubsTestCase):
    def test_customer_sees_only_their_own_subscriptions(self):
        mine = self.make_sub()
        other_user = make_user("other@x.com", Role.CUSTOMER)
        theirs = self.make_sub(customer=other_user.profile)
        self.assertEqual(set(selectors.subscription_queryset(self.customer_user)), {mine})
        self.assertEqual(set(selectors.subscription_queryset(other_user)), {theirs})
        self.assertEqual(set(selectors.subscription_queryset(self.staff)), {mine, theirs})
        self.assertIsNone(selectors.get_subscription(self.customer_user, theirs.pk))

    def test_list_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                list(selectors.subscription_queryset(self.staff))
            return len(ctx)

        self.make_sub()
        baseline = count()
        for _ in range(8):
            self.make_sub()
        self.assertEqual(count(), baseline)

    def test_subtotal_annotation(self):
        sub = self.make_sub()
        services.add_line(
            actor=self.staff, subscription=sub, product=self.pos, variant=None, quantity=3, tax=None
        )
        self.assertEqual(
            selectors.subscription_queryset(self.staff).get(pk=sub.pk).subtotal, Decimal("2500.00")
        )
