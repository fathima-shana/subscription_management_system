"""Subscription state machine: one table of allowed moves, plus the guard rules.

    Draft -> Quotation -> Confirmed -> Active -> Closed
                 |                       |  ^
                 +-> Draft               v  |
                                       Paused  (-> Closed)

Plan rules decide which optional moves exist: Pause needs plan.pausable, early Close needs
plan.closable. The system itself (auto-close at the end date) bypasses `closable`.
"""

from .models import Status

TRANSITIONS = {
    Status.DRAFT: {Status.QUOTATION},
    Status.QUOTATION: {Status.CONFIRMED, Status.DRAFT},
    Status.CONFIRMED: {Status.ACTIVE},
    Status.ACTIVE: {Status.PAUSED, Status.CLOSED},
    Status.PAUSED: {Status.ACTIVE, Status.CLOSED},
    Status.CLOSED: set(),
}

BUTTON_LABELS = {
    (Status.DRAFT, Status.QUOTATION): "Send quotation",
    (Status.QUOTATION, Status.CONFIRMED): "Confirm",
    (Status.QUOTATION, Status.DRAFT): "Back to draft",
    (Status.CONFIRMED, Status.ACTIVE): "Activate",
    (Status.ACTIVE, Status.PAUSED): "Pause",
    (Status.PAUSED, Status.ACTIVE): "Resume",
    (Status.ACTIVE, Status.CLOSED): "Close",
    (Status.PAUSED, Status.CLOSED): "Close",
}

# Shown as the progress bar on the detail page.
STEPPER = [Status.DRAFT, Status.QUOTATION, Status.CONFIRMED, Status.ACTIVE, Status.CLOSED]


def rule_blocks(subscription, to, system=False):
    """A reason string if the plan forbids this move, else None."""
    plan = subscription.plan
    if to == Status.PAUSED and not plan.pausable:
        return "This plan does not allow pausing."
    if to == Status.CLOSED and not plan.closable and not system:
        return "This plan does not allow closing early."
    return None


def available_transitions(subscription):
    """[(to_status, button_label)] that make sense to offer right now."""
    options = []
    for to in TRANSITIONS.get(subscription.status, ()):
        if rule_blocks(subscription, to) is None:
            options.append((to.value, BUTTON_LABELS[(Status(subscription.status), to)]))
    order = {"quotation": 0, "confirmed": 0, "active": 0, "paused": 1, "closed": 2, "draft": 3}
    return sorted(options, key=lambda o: order.get(o[0], 9))


def confirmation_problems(subscription, today):
    """Everything that stops a quotation from being confirmed (empty list = OK)."""
    problems = []
    plan = subscription.plan
    lines = list(subscription.lines.all())
    if not lines:
        problems.append("Add at least one product line.")
    if not plan.is_active:
        problems.append(f"The plan {plan.name} is no longer active.")
    if plan.start_date and subscription.start_date < plan.start_date:
        problems.append(
            f"The plan starts on {plan.start_date:%d %b %Y}; the subscription starts earlier."
        )
    if plan.end_date and subscription.start_date > plan.end_date:
        problems.append(
            f"The plan ended on {plan.end_date:%d %b %Y}; the subscription starts later."
        )
    for line in lines:
        if line.quantity < plan.min_quantity:
            problems.append(
                f"{line.label}: the plan needs a quantity of at least {plan.min_quantity}."
            )
    if subscription.quote_valid_until and today > subscription.quote_valid_until:
        problems.append(f"The quotation expired on {subscription.quote_valid_until:%d %b %Y}.")
    if not subscription.customer.user.is_active:
        problems.append("The customer account is deactivated.")
    return problems
