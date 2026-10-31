from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.core.http import is_htmx, paginate
from apps.subscriptions.models import Subscription

from . import engine, selectors, services
from .models import InvoiceStatus

SESSION_KEY = "billing_last_run"


def _get_or_404(user, pk):
    invoice = selectors.get_invoice(user, pk)
    if invoice is None:
        raise Http404
    return invoice


@internal_required
@require_GET
def invoice_list(request):
    today = timezone.localdate()
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if status not in selectors.FILTERS:
        status = ""
    page = paginate(request, selectors.invoice_queryset(request.user, today, q, status))
    ctx = {
        "page_obj": page,
        "q": q,
        "status": status,
        "today": today,
        "page_title": "Invoices",
        "target": "invoice-results",
        "filters": selectors.FILTERS,
    }
    template = "billing/_invoice_results.html" if is_htmx(request) else "billing/invoice_list.html"
    return render(request, template, ctx)


@internal_required
@require_GET
def invoice_detail(request, pk):
    invoice = _get_or_404(request.user, pk)
    today = timezone.localdate()
    return render(
        request,
        "billing/invoice_detail.html",
        {
            "invoice": invoice,
            "today": today,
            "display_status": invoice.display_status(today),
            "page_title": invoice.number,
        },
    )


@internal_required
@require_GET
def invoice_print(request, pk):
    invoice = _get_or_404(request.user, pk)
    today = timezone.localdate()
    return render(
        request,
        "billing/invoice_print.html",
        {
            "invoice": invoice,
            "display_status": invoice.display_status(today),
        },
    )


@internal_required
@require_POST
def invoice_send(request, pk):
    invoice = _get_or_404(request.user, pk)
    try:
        services.send_invoice(actor=request.user, invoice=invoice)
    except services.InvoiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{invoice.number} was emailed to {invoice.bill_to_email}.")
    return redirect("invoices:detail", pk=pk)


@admin_required
@require_POST
def invoice_cancel(request, pk):
    invoice = _get_or_404(request.user, pk)
    try:
        services.cancel_invoice(
            actor=request.user, invoice=invoice, reason=request.POST.get("reason", "")[:200]
        )
    except services.InvoiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{invoice.number} was cancelled.")
    return redirect("invoices:detail", pk=pk)


# --- Billing run (admin) --------------------------------------------------------------


@admin_required
@require_GET
def billing_home(request):
    today = timezone.localdate()
    due = (
        engine.due_subscriptions(today)
        .select_related("customer__user", "plan")
        .order_by("next_billing_date", "id")
    )
    return render(
        request,
        "billing/billing_home.html",
        {
            "today": today,
            "due_count": due.count(),
            "due": due[:15],
            "last_run": request.session.pop(SESSION_KEY, None),
            "page_title": "Billing",
        },
    )


@admin_required
@require_POST
def billing_run(request):
    result = engine.run(actor=request.user)
    request.session[SESSION_KEY] = result.as_dict()
    if result.failed:
        messages.warning(
            request, f"Billing finished with {len(result.failed)} problem(s). See the summary."
        )
    else:
        messages.success(request, f"Billing finished: {len(result.created)} invoice(s) created.")
    return redirect("billing:home")
