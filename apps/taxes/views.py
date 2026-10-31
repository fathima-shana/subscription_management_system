import copy

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.core.errors import DuplicateError
from apps.core.http import is_htmx, paginate
from apps.core.money import D

from . import selectors, services
from .calculator import compute_taxes
from .forms import TaxForm
from .models import Tax


@internal_required
@require_GET
def tax_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.tax_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Taxes", "target": "tax-results"}
    template = "taxes/_tax_results.html" if is_htmx(request) else "taxes/tax_list.html"
    return render(request, template, ctx)


@admin_required
def tax_create(request):
    form = TaxForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            tax = services.create_tax(actor=request.user, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A tax with this name already exists.")
        else:
            messages.success(request, f"Tax {tax.name} was created.")
            return redirect("taxes:list")
    return render(request, "taxes/tax_form.html", {"form": form, "page_title": "Add tax"})


@admin_required
def tax_edit(request, pk):
    tax = get_object_or_404(Tax, pk=pk)
    # The form edits a copy, so `tax` stays pristine and the service can diff old vs new.
    form = TaxForm(request.POST or None, instance=copy.copy(tax))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_tax(actor=request.user, tax=tax, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A tax with this name already exists.")
        else:
            messages.success(request, f"Tax {form.cleaned_data['name']} was updated.")
            return redirect("taxes:list")
    return render(
        request, "taxes/tax_form.html", {"form": form, "tax": tax, "page_title": f"Edit {tax.name}"}
    )


@admin_required
@require_POST
def tax_toggle_active(request, pk):
    tax = get_object_or_404(Tax, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_tax_active(actor=request.user, tax=tax, active=active)
    messages.success(request, f"{tax.name} was {'activated' if active else 'deactivated'}.")
    return redirect("taxes:list")


@internal_required
@require_GET
def tax_preview(request):
    """Small calculator card: shows what every active tax adds to an amount."""
    raw = request.GET.get("amount", "").strip()
    ctx = {"raw": raw}
    if raw:
        try:
            amount = D(raw)
            if amount < 0 or amount > D("1000000000"):
                raise ValueError
            ctx["result"] = compute_taxes(amount, selectors.active_taxes())
        except ValueError:
            ctx["error"] = "Enter a positive amount, for example 1000."
    return render(request, "taxes/_preview.html", ctx)
