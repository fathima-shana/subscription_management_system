import copy
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.core.errors import DuplicateError
from apps.core.http import is_htmx, paginate

from . import selectors, services
from .forms import PlanForm, ProductForm, VariantForm
from .models import Plan, Product, ProductVariant


def _toast(response, message, level="success"):
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "level": level}})
    return response


# --- Products ------------------------------------------------------------------------


@internal_required
@require_GET
def product_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.product_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Products", "target": "product-results"}
    template = "catalog/_product_results.html" if is_htmx(request) else "catalog/product_list.html"
    return render(request, template, ctx)


@admin_required
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            product = services.create_product(actor=request.user, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A product with this name already exists.")
        else:
            messages.success(
                request,
                f"Product {product.name} was created. Add variants below if it has options.",
            )
            return redirect("catalog:product_detail", pk=product.pk)
    return render(request, "catalog/product_form.html", {"form": form, "page_title": "Add product"})


def _variants_ctx(product, form):
    return {
        "product": product,
        "variants": product.variants.order_by("attribute", "value"),
        "variant_form": form,
    }


@internal_required
@require_GET
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("default_tax"), pk=pk)
    ctx = {"page_title": product.name, **_variants_ctx(product, VariantForm(product=product))}
    return render(request, "catalog/product_detail.html", ctx)


@admin_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=copy.copy(product))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_product(actor=request.user, product=product, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A product with this name already exists.")
        else:
            messages.success(request, f"Product {form.cleaned_data['name']} was updated.")
            return redirect("catalog:product_detail", pk=pk)
    return render(
        request,
        "catalog/product_form.html",
        {"form": form, "product": product, "page_title": f"Edit {product.name}"},
    )


@admin_required
@require_POST
def product_toggle_active(request, pk):
    product = get_object_or_404(Product, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_product_active(actor=request.user, product=product, active=active)
    messages.success(request, f"{product.name} was {'activated' if active else 'deactivated'}.")
    return redirect("catalog:product_detail", pk=pk)


# --- Variants (inline on the product page) -------------------------------------------


@admin_required
@require_POST
def variant_create(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = VariantForm(request.POST, product=product)
    if form.is_valid():
        try:
            variant = services.add_variant(actor=request.user, product=product, **form.cleaned_data)
        except DuplicateError:
            form.add_error("value", "This option already exists for the product.")
        else:
            if is_htmx(request):
                response = render(
                    request,
                    "catalog/_variants.html",
                    _variants_ctx(product, VariantForm(product=product)),
                )
                return _toast(response, f"Added {variant.label}.")
            messages.success(request, f"Added {variant.label}.")
            return redirect("catalog:product_detail", pk=pk)
    if is_htmx(request):  # show the errors in place
        return render(request, "catalog/_variants.html", _variants_ctx(product, form))
    return render(
        request,
        "catalog/product_detail.html",
        {"page_title": product.name, **_variants_ctx(product, form)},
    )


@admin_required
def variant_edit(request, pk):
    variant = get_object_or_404(ProductVariant.objects.select_related("product"), pk=pk)
    product = variant.product
    form = VariantForm(request.POST or None, instance=copy.copy(variant), product=product)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_variant(actor=request.user, variant=variant, **form.cleaned_data)
        except DuplicateError:
            form.add_error("value", "This option already exists for the product.")
        else:
            messages.success(request, "Variant updated.")
            return redirect("catalog:product_detail", pk=product.pk)
    return render(
        request,
        "catalog/variant_form.html",
        {
            "form": form,
            "variant": variant,
            "product": product,
            "page_title": f"Edit {variant.label}",
        },
    )


@admin_required
@require_POST
def variant_toggle_active(request, pk):
    variant = get_object_or_404(ProductVariant.objects.select_related("product"), pk=pk)
    active = request.POST.get("active") == "1"
    services.set_variant_active(actor=request.user, variant=variant, active=active)
    verb = "activated" if active else "deactivated"
    if is_htmx(request):
        product = variant.product
        response = render(
            request, "catalog/_variants.html", _variants_ctx(product, VariantForm(product=product))
        )
        return _toast(response, f"{variant.label} was {verb}.")
    messages.success(request, f"{variant.label} was {verb}.")
    return redirect("catalog:product_detail", pk=variant.product_id)


# --- Plans ---------------------------------------------------------------------------


@internal_required
@require_GET
def plan_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.plan_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Plans", "target": "plan-results"}
    template = "catalog/_plan_results.html" if is_htmx(request) else "catalog/plan_list.html"
    return render(request, template, ctx)


@admin_required
def plan_create(request):
    form = PlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            plan = services.create_plan(actor=request.user, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A plan with this name already exists.")
        else:
            messages.success(request, f"Plan {plan.name} was created.")
            return redirect("catalog:plan_list")
    return render(request, "catalog/plan_form.html", {"form": form, "page_title": "Add plan"})


@admin_required
def plan_edit(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    form = PlanForm(request.POST or None, instance=copy.copy(plan))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_plan(actor=request.user, plan=plan, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A plan with this name already exists.")
        else:
            messages.success(request, f"Plan {form.cleaned_data['name']} was updated.")
            return redirect("catalog:plan_list")
    return render(
        request,
        "catalog/plan_form.html",
        {"form": form, "plan": plan, "page_title": f"Edit {plan.name}"},
    )


@admin_required
@require_POST
def plan_toggle_active(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_plan_active(actor=request.user, plan=plan, active=active)
    messages.success(request, f"{plan.name} was {'activated' if active else 'deactivated'}.")
    return redirect("catalog:plan_list")
