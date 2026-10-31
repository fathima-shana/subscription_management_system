"""Read queries for subscriptions. Customers are limited to their own rows here, in one place."""

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.core.scoping import scope_to_customer
from apps.discounts.selectors import candidate_discounts

from .models import QuotationTemplate, Status, Subscription
from .pricing import price_lines

_AMOUNT = ExpressionWrapper(
    F("lines__quantity") * F("lines__unit_price"),
    output_field=DecimalField(max_digits=16, decimal_places=2),
)


def subscription_queryset(user, q: str = "", status: str = ""):
    qs = scope_to_customer(Subscription.objects.all(), user, lookup="customer__user")
    qs = qs.select_related("customer__user", "plan", "payment_term").annotate(
        subtotal=Coalesce(
            Sum(_AMOUNT), Value(0), output_field=DecimalField(max_digits=16, decimal_places=2)
        )
    )
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(customer__user__full_name__icontains=q)
            | Q(customer__user__email__icontains=q)
            | Q(customer__company_name__icontains=q)
        )
    if status in Status.values:
        qs = qs.filter(status=status)
    return qs.order_by("-id")


def get_subscription(user, pk):
    """One subscription the user may see, or None."""
    return (
        scope_to_customer(Subscription.objects.all(), user, lookup="customer__user")
        .select_related("customer__user", "plan", "payment_term", "template", "renewed_from")
        .filter(pk=pk)
        .first()
    )


def priced_lines(subscription, on_date):
    lines = subscription.lines.select_related("product", "variant", "tax").order_by("id")
    return price_lines(lines, candidate_discounts(on_date), on_date)


def template_queryset(q: str = ""):
    from django.db.models import Count

    qs = QuotationTemplate.objects.select_related("plan").annotate(line_count=Count("lines"))
    if q:
        qs = qs.filter(name__icontains=q)
    return qs.order_by("-is_active", "name", "id")
