import copy

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.core.errors import DuplicateError
from apps.core.http import is_htmx, paginate

from . import selectors, services
from .forms import DiscountForm
from .models import Discount


@internal_required
@require_GET
def discount_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.discount_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Discounts", "target": "discount-results"}
    template = (
        "discounts/_discount_results.html" if is_htmx(request) else "discounts/discount_list.html"
    )
    return render(request, template, ctx)


@admin_required
def discount_create(request):
    form = DiscountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            discount = services.create_discount(actor=request.user, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A discount with this name already exists.")
        else:
            messages.success(request, f"Discount {discount.name} was created.")
            return redirect("discounts:list")
    return render(
        request, "discounts/discount_form.html", {"form": form, "page_title": "Add discount"}
    )


@admin_required
def discount_edit(request, pk):
    discount = get_object_or_404(Discount, pk=pk)
    form = DiscountForm(request.POST or None, instance=copy.copy(discount))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_discount(actor=request.user, discount=discount, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A discount with this name already exists.")
        else:
            messages.success(request, f"Discount {form.cleaned_data['name']} was updated.")
            return redirect("discounts:list")
    return render(
        request,
        "discounts/discount_form.html",
        {"form": form, "discount": discount, "page_title": f"Edit {discount.name}"},
    )


@admin_required
@require_POST
def discount_toggle_active(request, pk):
    discount = get_object_or_404(Discount, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_discount_active(actor=request.user, discount=discount, active=active)
    messages.success(request, f"{discount.name} was {'activated' if active else 'deactivated'}.")
    return redirect("discounts:list")
