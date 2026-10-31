"""Business operations for subscriptions and quotation templates.

Subscriptions: admin and staff. Templates: admin only. Every service re-checks the role.
"""

import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import audit
from apps.core.errors import DuplicateError
from apps.core.permissions import ensure_admin, ensure_internal
from apps.core.sequences import next_number

from .models import (
    QuotationTemplate,
    QuotationTemplateLine,
    Status,
    Subscription,
    SubscriptionLine,
    SubscriptionStatusHistory,
)
from .workflow import TRANSITIONS, confirmation_problems, rule_blocks


class TransitionError(Exception):
    """A status change was refused. `problems` is a list of messages fit to show to the user."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


class LockedError(Exception):
    """The subscription can no longer be edited (it has been confirmed)."""


def _history(subscription, from_status, to_status, actor, note=""):
    SubscriptionStatusHistory.objects.create(
        subscription=subscription,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor if getattr(actor, "pk", None) else None,
        note=note[:200],
    )


def _require_editable(subscription):
    if not subscription.is_editable:
        raise LockedError("Only draft and quotation subscriptions can be edited.")


def _line_defaults(product, variant, tax):
    return {"unit_price": product.price_for(variant), "tax": tax}


# --- Subscriptions --------------------------------------------------------------------


@transaction.atomic
def create_subscription(
    *,
    actor,
    customer,
    plan,
    payment_term,
    start_date,
    expiry_date=None,
    template=None,
    quote_valid_until=None,
    notes="",
) -> Subscription:
    ensure_internal(actor)
    if template is not None:
        plan = template.plan
        if quote_valid_until is None:
            quote_valid_until = timezone.localdate() + datetime.timedelta(
                days=template.validity_days
            )
    sub = Subscription.objects.create(
        number=next_number("subscription", "SUB"),
        customer=customer,
        plan=plan,
        template=template,
        payment_term=payment_term,
        start_date=start_date,
        expiry_date=expiry_date,
        quote_valid_until=quote_valid_until,
        notes=notes,
        created_by=actor,
    )
    if template is not None:
        for tl in template.lines.select_related("product", "variant"):
            SubscriptionLine.objects.create(
                subscription=sub,
                product=tl.product,
                variant=tl.variant,
                quantity=tl.quantity,
                **_line_defaults(tl.product, tl.variant, tl.product.default_tax),
            )
    _history(
        sub,
        "",
        Status.DRAFT,
        actor,
        "Created" + (f" from template {template.name}" if template else ""),
    )
    audit.record(
        actor=actor,
        action="subscription.created",
        entity=sub,
        details={"number": sub.number, "customer": customer.pk, "plan": plan.name},
    )
    return sub


@transaction.atomic
def update_subscription(*, actor, subscription, **fields) -> Subscription:
    ensure_internal(actor)
    _require_editable(subscription)
    changed = audit.apply_changes(subscription, fields)
    if changed:
        audit.record(
            actor=actor,
            action="subscription.updated",
            entity=subscription,
            details={"changed": changed},
        )
    return subscription


@transaction.atomic
def add_line(*, actor, subscription, product, variant, quantity, tax) -> SubscriptionLine:
    """Add a product line. Adding the same product/variant again increases its quantity."""
    ensure_internal(actor)
    _require_editable(subscription)
    existing = subscription.lines.filter(product=product, variant=variant).first()
    if existing:
        existing.quantity += quantity
        existing.save(update_fields=["quantity"])
        return existing
    return SubscriptionLine.objects.create(
        subscription=subscription,
        product=product,
        variant=variant,
        quantity=quantity,
        **_line_defaults(product, variant, tax),
    )


@transaction.atomic
def remove_line(*, actor, line) -> None:
    ensure_internal(actor)
    _require_editable(line.subscription)
    line.delete()


@transaction.atomic
def transition(*, actor, subscription, to, note="", system=False, today=None) -> Subscription:
    """Move a subscription to a new status, enforcing the state machine, plan rules and guards.

    The row is locked first, so two people clicking at once cannot both win. `system=True` is
    for automated moves (auto-close at the end date) and skips the role check and `closable`.
    """
    if not system:
        ensure_internal(actor)
    sub = (
        Subscription.objects.select_for_update()
        .select_related("plan", "customer__user")
        .get(pk=subscription.pk)
    )
    try:
        target = Status(to)
    except ValueError as exc:
        raise TransitionError("Unknown status.") from exc
    current = Status(sub.status)
    if target not in TRANSITIONS[current]:
        raise TransitionError(
            f"A {current.label.lower()} subscription cannot become {target.label.lower()}."
        )
    blocked = rule_blocks(sub, target, system)
    if blocked:
        raise TransitionError(blocked)

    today = today or timezone.localdate()
    if target == Status.QUOTATION and not sub.lines.exists():
        raise TransitionError("Add at least one product line first.")
    if target == Status.CONFIRMED:
        problems = confirmation_problems(sub, today)
        if problems:
            raise TransitionError(problems)

    sub.status = target.value
    update = ["status", "updated_at"]
    if target == Status.ACTIVE:
        if current == Status.CONFIRMED:
            sub.next_billing_date = sub.start_date
        else:  # resuming: never bill for the paused time
            sub.next_billing_date = max(sub.next_billing_date or today, today)
        update.append("next_billing_date")
    elif target == Status.CLOSED:
        sub.next_billing_date = None
        update.append("next_billing_date")
    sub.save(update_fields=update)

    _history(sub, current, target, actor, note)
    audit.record(
        actor=actor if not system else None,
        action="subscription.status_changed",
        entity=sub,
        details={
            "number": sub.number,
            "from": current.value,
            "to": target.value,
            "system": system,
            "note": note,
        },
    )
    return sub


@transaction.atomic
def renew(*, actor, subscription) -> Subscription:
    """Start a new draft subscription that continues this one, at today's prices."""
    ensure_internal(actor)
    plan = subscription.plan
    if subscription.status not in (Status.ACTIVE, Status.CLOSED):
        raise TransitionError("Only active or closed subscriptions can be renewed.")
    if not plan.renewable:
        raise TransitionError("This plan does not allow renewal.")
    if not plan.is_active:
        raise TransitionError(f"The plan {plan.name} is no longer active.")

    old_lines = list(subscription.lines.select_related("product", "variant", "tax"))
    keep = [
        l for l in old_lines if l.product.is_active and (l.variant is None or l.variant.is_active)
    ]
    skipped = [l.label for l in old_lines if l not in keep]
    if not keep:
        raise TransitionError("None of the products on this subscription are still for sale.")

    if subscription.expiry_date:
        start = subscription.expiry_date + datetime.timedelta(days=1)
        expiry = start + (subscription.expiry_date - subscription.start_date)
    else:
        start, expiry = timezone.localdate(), None
    new = Subscription.objects.create(
        number=next_number("subscription", "SUB"),
        customer=subscription.customer,
        plan=plan,
        payment_term=subscription.payment_term,
        start_date=start,
        expiry_date=expiry,
        notes=subscription.notes,
        renewed_from=subscription,
        created_by=actor,
    )
    for l in keep:
        SubscriptionLine.objects.create(
            subscription=new,
            product=l.product,
            variant=l.variant,
            quantity=l.quantity,
            **_line_defaults(l.product, l.variant, l.tax),
        )
    note = f"Renewed from {subscription.number}" + (
        f" (not for sale any more: {', '.join(skipped)})" if skipped else ""
    )
    _history(new, "", Status.DRAFT, actor, note)
    audit.record(
        actor=actor,
        action="subscription.renewed",
        entity=new,
        details={"from": subscription.number, "skipped": skipped},
    )
    return new


# --- Quotation templates (admin only) -------------------------------------------------


@transaction.atomic
def create_template(*, actor, **fields) -> QuotationTemplate:
    ensure_admin(actor)
    try:
        with transaction.atomic():
            template = QuotationTemplate.objects.create(**fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    audit.record(
        actor=actor, action="template.created", entity=template, details={"name": template.name}
    )
    return template


@transaction.atomic
def update_template(*, actor, template, **fields) -> QuotationTemplate:
    ensure_admin(actor)
    try:
        with transaction.atomic():
            changed = audit.apply_changes(template, fields)
    except IntegrityError as exc:
        raise DuplicateError(fields.get("name")) from exc
    if changed:
        audit.record(
            actor=actor, action="template.updated", entity=template, details={"changed": changed}
        )
    return template


@transaction.atomic
def set_template_active(*, actor, template, active: bool) -> QuotationTemplate:
    ensure_admin(actor)
    if template.is_active != active:
        template.is_active = active
        template.save(update_fields=["is_active"])
        audit.record(
            actor=actor,
            action="template.activated" if active else "template.deactivated",
            entity=template,
            details={"name": template.name},
        )
    return template


@transaction.atomic
def add_template_line(*, actor, template, product, variant, quantity) -> QuotationTemplateLine:
    ensure_admin(actor)
    existing = template.lines.filter(product=product, variant=variant).first()
    if existing:
        existing.quantity += quantity
        existing.save(update_fields=["quantity"])
        return existing
    return QuotationTemplateLine.objects.create(
        template=template, product=product, variant=variant, quantity=quantity
    )


@transaction.atomic
def remove_template_line(*, actor, line) -> None:
    ensure_admin(actor)
    line.delete()
