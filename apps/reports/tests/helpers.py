import datetime
from decimal import Decimal

from apps.billing import engine
from apps.catalog.models import Plan
from apps.payments import services as payment_services
from apps.subscriptions import services as subscription_services
from apps.subscriptions.tests.helpers import SubsTestCase

D = Decimal


class ReportTestCase(SubsTestCase):
    def active(self, plan=None, quantity=1, **over):
        """An Active subscription whose line is CRM (1000/unit, GST 18)."""
        sub = self.make_sub(plan=plan or self.plan, **over)
        if quantity > 1:
            subscription_services.add_line(
                actor=self.staff,
                subscription=sub,
                product=self.crm,
                variant=None,
                quantity=quantity - 1,
                tax=None,
            )
        return self.move(sub, "quotation", "confirmed", "active")

    def plan_of(self, period, name=None):
        return Plan.objects.create(name=name or f"{period.title()} plan", billing_period=period)

    def bill(self, today=None):
        return engine.run(today=today or self.today)

    def pay(self, invoice, amount, method="cash", date=None):
        return payment_services.record_payment(
            actor=self.staff,
            invoice=invoice,
            amount=amount,
            method=method,
            payment_date=date or self.today,
        )

    def days(self, n):
        return self.today + datetime.timedelta(days=n)
