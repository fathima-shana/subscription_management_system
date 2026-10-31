"""The billing engine: turns due subscriptions into invoices.

Rules
* Due = Active and next_billing_date <= today. Billing is in advance, one invoice per period.
* Each invoice is created in its own transaction together with the next_billing_date advance and
  the discount redemption, so a failure leaves no half-billed subscription behind.
* The subscription row is locked with SELECT ... FOR UPDATE SKIP LOCKED: two runs at the same
  moment never bill the same subscription, and neither waits for the other.
* UNIQUE(subscription, period_start) in the database is the last line of defence, and the engine
  also checks for an existing invoice first (and just moves the date on), so running it twice,
  or after a crash, can never bill a period twice.
* A subscription that is several periods behind is caught up, one invoice per period (at most
  MAX_PERIODS_PER_RUN per subscription per run).
* After the last period (next start is past the expiry date) the plan decides: auto_close plans
  are closed by the system; others stay Active with billing stopped, to be closed by a person.
* One failing subscription never stops the run; it is reported and logged.
* No proration: billing is always for whole periods.
"""

import datetime
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.money import ZERO
from apps.core.sequences import next_number
from apps.discounts.selectors import candidate_discounts
from apps.discounts.services import redeem
from apps.subscriptions import services as subscription_services
from apps.subscriptions.models import Status, Subscription
from apps.subscriptions.pricing import price_lines

from .models import Invoice, InvoiceLine
from .periods import next_period_start, period_end

logger = logging.getLogger("billing")

MAX_PERIODS_PER_RUN = 24


class BillingError(Exception):
    """A subscription cannot be billed (for example it has no lines)."""


@dataclass
class RunResult:
    today: object
    created: list = field(
        default_factory=list
    )  # (invoice_id, invoice_number, subscription_number, total)
    healed: list = field(
        default_factory=list
    )  # subscription numbers whose date was moved on, no invoice needed
    ended: list = field(
        default_factory=list
    )  # billing stopped, still active (plan does not auto-close)
    closed: list = field(default_factory=list)  # closed by the system at the end date
    failed: list = field(default_factory=list)  # (subscription_number, message)

    @property
    def total_billed(self):
        return sum((row[3] for row in self.created), ZERO)

    def as_dict(self):
        return {
            "today": self.today.isoformat(),
            "created": [[i, n, s, str(t)] for i, n, s, t in self.created],
            "healed": self.healed,
            "ended": self.ended,
            "closed": self.closed,
            "failed": [list(f) for f in self.failed],
            "total_billed": str(self.total_billed),
        }


def due_subscriptions(today):
    return Subscription.objects.filter(status=Status.ACTIVE, next_billing_date__lte=today)


def _price(lines, on_date, cache):
    """Price the lines with the best discount that can actually be redeemed right now."""
    candidates = cache.setdefault(on_date, list(candidate_discounts(on_date)))
    while True:
        priced = price_lines(lines, candidates, on_date)
        if priced.applied is None:
            return priced
        if redeem(priced.applied.discount.pk):
            return priced
        # Somebody used the last redemption: drop that discount and try the next best.
        gone = priced.applied.discount.pk
        candidates[:] = [d for d in candidates if d.pk != gone]


def _finish(sub, result):
    """The last period has been billed (or none is left): close or stop billing."""
    if sub.plan.auto_close:
        subscription_services.transition(
            actor=None,
            subscription=sub,
            to=Status.CLOSED,
            system=True,
            note="Reached end date",
            today=result.today,
        )
        result.closed.append(sub.number)
    else:
        sub.next_billing_date = None
        sub.save(update_fields=["next_billing_date", "updated_at"])
        result.ended.append(sub.number)


def _bill_one(pk, today, result, cache):
    """Bill the next due period of one subscription. Returns 'billed', 'ended' or 'skip'."""
    sub = (
        Subscription.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("plan", "payment_term", "customer__user")
        .filter(pk=pk, status=Status.ACTIVE, next_billing_date__lte=today)
        .first()
    )
    if sub is None:  # not due any more, or another worker has it
        return "skip"

    start = sub.next_billing_date
    if sub.expiry_date and start > sub.expiry_date:
        _finish(sub, result)
        return "ended"

    plan_period = sub.plan.billing_period
    next_start = next_period_start(start, plan_period, sub.start_date)

    if Invoice.objects.filter(subscription=sub, period_start=start).exists():
        result.healed.append(sub.number)  # already billed; only the date was not advanced
    else:
        lines = list(sub.lines.select_related("product", "variant", "tax").order_by("id"))
        if not lines:
            raise BillingError("The subscription has no product lines.")
        priced = _price(lines, start, cache)
        invoice = _create_invoice(sub, priced, start, plan_period, today)
        result.created.append((invoice.pk, invoice.number, sub.number, invoice.total))
        logger.info(
            "Invoice %s created for %s, period starting %s", invoice.number, sub.number, start
        )

    sub.next_billing_date = next_start
    sub.save(update_fields=["next_billing_date", "updated_at"])
    if sub.expiry_date and next_start > sub.expiry_date:
        _finish(sub, result)
        return "ended"
    return "billed"


def _create_invoice(sub, priced, start, plan_period, today):
    customer = sub.customer
    invoice = Invoice.objects.create(
        number=next_number("invoice", "INV"),
        subscription=sub,
        customer=customer,
        period_start=start,
        period_end=period_end(start, plan_period, sub.start_date),
        issue_date=today,
        due_date=today + datetime.timedelta(days=sub.payment_term.days),
        bill_to_name=customer.company_name or customer.user.full_name,
        bill_to_email=customer.user.email,
        bill_to_address=customer.billing_address,
        bill_to_tax_id=customer.tax_id,
        plan_name=sub.plan.name,
        payment_term_name=sub.payment_term.name,
        discount=priced.applied.discount if priced.applied else None,
        discount_name=priced.applied.discount.name if priced.applied else "",
        subtotal=priced.subtotal,
        discount_total=priced.discount_total,
        tax_total=priced.tax_total,
        total=priced.total,
    )
    rows = []
    for p in priced.lines:
        tax = p.line.tax
        rows.append(
            InvoiceLine(
                invoice=invoice,
                product=p.line.product,
                description=p.line.label,
                quantity=p.line.quantity,
                unit_price=p.line.unit_price,
                amount=p.amount,
                discount_amount=p.discount,
                tax_name=tax.name if tax else "",
                tax_type=tax.tax_type if tax else "",
                tax_rate=tax.rate if tax else 0,
                tax_amount=p.tax,
                total=p.total,
            )
        )
    InvoiceLine.objects.bulk_create(rows)
    return invoice


def close_expired(today, result):
    """Auto-close plans: close active or paused subscriptions whose expiry date has passed."""
    expired = Subscription.objects.filter(
        status__in=[Status.ACTIVE, Status.PAUSED], expiry_date__lt=today, plan__auto_close=True
    ).select_related("plan")
    for sub in expired:
        try:
            with transaction.atomic():
                subscription_services.transition(
                    actor=None,
                    subscription=sub,
                    to=Status.CLOSED,
                    system=True,
                    note="Reached end date",
                    today=today,
                )
            result.closed.append(sub.number)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            logger.exception("Could not auto-close %s", sub.number)
            result.failed.append((sub.number, str(exc)))


def run(today=None, actor=None) -> RunResult:
    """Bill everything that is due. Safe to run repeatedly and concurrently."""
    today = today or timezone.localdate()
    result = RunResult(today=today)
    cache = {}
    numbers = dict(
        Subscription.objects.filter(status=Status.ACTIVE, next_billing_date__lte=today).values_list(
            "pk", "number"
        )
    )
    for pk in (
        due_subscriptions(today).order_by("next_billing_date", "id").values_list("pk", flat=True)
    ):
        for _ in range(MAX_PERIODS_PER_RUN):
            attempt = RunResult(today=today)  # merged only if the transaction commits
            try:
                with transaction.atomic():
                    outcome = _bill_one(pk, today, attempt, cache)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Billing failed for subscription %s", numbers.get(pk, pk))
                result.failed.append((numbers.get(pk, str(pk)), str(exc)))
                break
            result.created += attempt.created
            result.healed += attempt.healed
            result.ended += attempt.ended
            result.closed += attempt.closed
            if outcome != "billed":
                break
    close_expired(today, result)
    audit.record(
        actor=actor,
        action="billing.run",
        entity=None,
        details={
            "date": today.isoformat(),
            "invoices": len(result.created),
            "failed": len(result.failed),
            "closed": len(result.closed),
            "total": str(result.total_billed),
        },
    )
    return result
