from django.db.models import Count, Q

from .models import Discount


def discount_queryset(q: str = ""):
    qs = Discount.objects.annotate(target_count=Count("products", distinct=True))
    if q:
        qs = qs.filter(name__icontains=q)
    return qs.order_by("-is_active", "name", "id")


def candidate_discounts(on_date):
    """Discounts the billing engine should consider: switched on and inside their date range."""
    return (
        Discount.objects.filter(is_active=True)
        .filter(Q(start_date__isnull=True) | Q(start_date__lte=on_date))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=on_date))
        .prefetch_related("products")
        .order_by("id")
    )
