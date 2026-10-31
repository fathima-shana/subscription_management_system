from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.permissions import customer_required
from apps.core.http import is_htmx, paginate
from apps.subscriptions.models import Status
from apps.subscriptions.selectors import priced_lines

from . import selectors


@customer_required
@require_GET
def customer_dashboard(request):
    today = timezone.localdate()
    return render(
        request,
        "portal/dashboard.html",
        {
            "page_title": "Dashboard",
            "today": today,
            "d": selectors.dashboard(request.user, today),
        },
    )


@customer_required
@require_GET
def subscription_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.subscriptions(request.user, q))
    ctx = {
        "page_obj": page,
        "q": q,
        "page_title": "My subscriptions",
        "target": "portal-subscription-results",
    }
    template = (
        "portal/_subscription_results.html" if is_htmx(request) else "portal/subscription_list.html"
    )
    return render(request, template, ctx)


@customer_required
@require_GET
def subscription_detail(request, pk):
    sub = selectors.get_subscription(request.user, pk)
    if sub is None:
        raise Http404
    today = timezone.localdate()
    return render(
        request,
        "portal/subscription_detail.html",
        {
            "sub": sub,
            "page_title": sub.number,
            "priced": priced_lines(sub, today),
            "invoices": sub.invoices.exclude(status="draft").order_by("-period_start")[:12],
            "is_quotation": sub.status == Status.QUOTATION,
        },
    )


@customer_required
@require_GET
def invoice_list(request):
    today = timezone.localdate()
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if status not in dict(selectors.INVOICE_FILTERS):
        status = ""
    page = paginate(request, selectors.invoices(request.user, today, q, status))
    ctx = {
        "page_obj": page,
        "q": q,
        "status": status,
        "today": today,
        "page_title": "My invoices",
        "target": "portal-invoice-results",
        "filters": selectors.INVOICE_FILTERS,
    }
    template = "portal/_invoice_results.html" if is_htmx(request) else "portal/invoice_list.html"
    return render(request, template, ctx)


def _invoice_or_404(request, pk):
    invoice = selectors.get_invoice(request.user, pk)
    if invoice is None:
        raise Http404
    return invoice


@customer_required
@require_GET
def invoice_detail(request, pk):
    invoice = _invoice_or_404(request, pk)
    today = timezone.localdate()
    return render(
        request,
        "portal/invoice_detail.html",
        {
            "invoice": invoice,
            "page_title": invoice.number,
            "display_status": invoice.display_status(today),
            "payments": selectors.live_payments(invoice),
        },
    )


@customer_required
@require_GET
def invoice_print(request, pk):
    invoice = _invoice_or_404(request, pk)
    return render(
        request,
        "billing/invoice_print.html",
        {
            "invoice": invoice,
            "display_status": invoice.display_status(timezone.localdate()),
        },
    )
