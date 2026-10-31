from django.http import Http404, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.permissions import internal_required
from apps.catalog.models import Plan
from apps.core import audit

from . import exports
from . import filters as report_filters
from . import metrics


@internal_required
@require_GET
def internal_dashboard(request):
    """The landing page for admin and staff (this month, all plans)."""
    today = timezone.localdate()
    filters = report_filters.parse({}, today)
    return render(
        request,
        "reports/dashboard.html",
        {
            "page_title": "Dashboard",
            "today": today,
            "filters": filters,
            "r": metrics.build_report(filters, today),
        },
    )


@internal_required
@require_GET
def report(request):
    today = timezone.localdate()
    filters = report_filters.parse(request.GET, today)
    return render(
        request,
        "reports/report.html",
        {
            "page_title": "Reports",
            "today": today,
            "filters": filters,
            "r": metrics.build_report(filters, today),
            "presets": report_filters.PRESETS,
            "plans": Plan.objects.order_by("name"),
            "exports": [
                ("invoices", "Invoices"),
                ("payments", "Payments"),
                ("subscriptions", "Subscriptions"),
                ("overdue", "Overdue invoices"),
            ],
            "from_value": request.GET.get("from", ""),
            "to_value": request.GET.get("to", ""),
        },
    )


@internal_required
@require_GET
def export(request, kind):
    if kind not in exports.KINDS:
        raise Http404
    today = timezone.localdate()
    filters = report_filters.parse(request.GET, today)
    audit.record(
        actor=request.user,
        action="report.exported",
        details={"kind": kind, "range": filters.label, "plan": filters.plan_id},
    )
    response = StreamingHttpResponse(
        exports.build(kind, filters, today), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="brightloop-{kind}-{today.isoformat()}.csv"'
    )
    response["Cache-Control"] = "no-store"
    return response
