"""Read queries for the catalog. Views never build these querysets themselves."""

from django.db.models import Count, Q

from .models import Plan, Product


def product_queryset(q: str = ""):
    qs = Product.objects.select_related("default_tax").annotate(
        variant_count=Count("variants", filter=Q(variants__is_active=True))
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(variants__value__icontains=q)).distinct()
    return qs.order_by("-is_active", "name", "id")


def plan_queryset(q: str = ""):
    qs = Plan.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    return qs.order_by("-is_active", "name", "id")
