from django.core.paginator import Paginator


def is_htmx(request) -> bool:
    """True for an HTMX partial request (not for a history-restore, which needs the full page)."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def paginate(request, queryset, per_page: int = 15):
    """Server-side pagination; an invalid or out-of-range page falls back safely."""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))
