import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Plan, Product, ProductVariant
from apps.core.testing import make_people, make_user
from apps.subscriptions import services
from apps.subscriptions.models import PaymentTerm
from apps.taxes.models import Tax


class SubsTestCase(TestCase):
    def setUp(self):
        self.admin, self.staff, self.customer_user = make_people()
        self.customer = self.customer_user.profile
        self.today = timezone.localdate()
        self.gst = Tax.objects.create(name="GST 18", tax_type="percentage", rate=Decimal("18"))
        self.crm = Product.objects.create(
            name="CRM", sales_price=Decimal("1000"), default_tax=self.gst
        )
        self.pos = Product.objects.create(name="POS", sales_price=Decimal("500"))
        self.odoo = ProductVariant.objects.create(
            product=self.crm, attribute="Brand", value="Odoo", extra_price=Decimal("560")
        )
        self.plan = Plan.objects.create(name="Monthly", billing_period="monthly", pausable=True)
        self.term = PaymentTerm.objects.get(name="30 days")

    def make_sub(self, actor=None, with_line=True, **over):
        fields = dict(
            customer=self.customer, plan=self.plan, payment_term=self.term, start_date=self.today
        )
        fields.update(over)
        sub = services.create_subscription(actor=actor or self.staff, **fields)
        if with_line:
            services.add_line(
                actor=self.staff,
                subscription=sub,
                product=self.crm,
                variant=None,
                quantity=1,
                tax=self.gst,
            )
        return sub

    def move(self, sub, *statuses, actor=None):
        for status in statuses:
            sub = services.transition(actor=actor or self.staff, subscription=sub, to=status)
        return sub

    def days(self, n):
        return self.today + datetime.timedelta(days=n)
