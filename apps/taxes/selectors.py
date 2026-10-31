from .models import Tax


def tax_queryset(q: str = ""):
    qs = Tax.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    return qs.order_by("-is_active", "name", "id")


def active_taxes():
    return Tax.objects.filter(is_active=True).order_by("name")
