from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.billing import selectors as billing_selectors
from apps.core.http import is_htmx, paginate

from . import selectors, services
from .forms import PaymentForm
from .models import Method, Payment


@internal_required
@require_GET
def payment_list(request):
    today = timezone.localdate()
    q = request.GET.get("q", "").strip()
    method = request.GET.get("method", "").strip()
    if method not in Method.values:
        method = ""
    page = paginate(request, selectors.payment_queryset(request.user, q, method))
    ctx = {
        "page_obj": page,
        "q": q,
        "method": method,
        "page_title": "Payments",
        "target": "payment-results",
        "methods": Method.choices,
    }
    if is_htmx(request):
        return render(request, "payments/_payment_results.html", ctx)
    ctx["totals"] = selectors.outstanding_totals(today)
    ctx["collected_month"] = selectors.collected(today.replace(day=1), today)
    ctx["today"] = today
    return render(request, "payments/payment_list.html", ctx)


@internal_required
def payment_create(request, invoice_pk):
    invoice = billing_selectors.get_invoice(request.user, invoice_pk)
    if invoice is None:
        raise Http404
    if invoice.status != "confirmed":
        messages.error(
            request, f"A {invoice.get_status_display().lower()} invoice cannot receive payments."
        )
        return redirect("invoices:detail", pk=invoice.pk)
    form = PaymentForm(request.POST or None, invoice=invoice)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            payment = services.record_payment(
                actor=request.user,
                invoice=invoice,
                amount=d["amount"],
                method=d["method"],
                payment_date=d["payment_date"],
                note=d["note"],
            )
        except services.PaymentError as exc:
            form.add_error(None, str(exc))
        else:
            if invoice.status == "paid":
                messages.success(
                    request, f"Payment recorded. {invoice.number} is now paid in full."
                )
            else:
                messages.success(
                    request,
                    f"Payment recorded. ₹{invoice.balance:,.2f} is still due on {invoice.number}.",
                )
            return redirect("invoices:detail", pk=invoice.pk)
    return render(
        request,
        "payments/payment_form.html",
        {
            "form": form,
            "invoice": invoice,
            "page_title": f"Record payment for {invoice.number}",
        },
    )


@admin_required
@require_POST
def payment_void(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("invoice"), pk=pk)
    try:
        services.void_payment(
            actor=request.user, payment=payment, reason=request.POST.get("reason", "")
        )
    except services.PaymentError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"The payment of ₹{payment.amount:,.2f} was voided.")
    return redirect("invoices:detail", pk=payment.invoice_id)
