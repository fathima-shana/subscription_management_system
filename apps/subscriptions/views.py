import copy
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, internal_required
from apps.core.errors import DuplicateError
from apps.core.http import is_htmx, paginate

from . import selectors, services
from .forms import (
    QuotationTemplateForm,
    SubscriptionCreateForm,
    SubscriptionEditForm,
    SubscriptionLineForm,
    TemplateLineForm,
)
from .models import QuotationTemplate, QuotationTemplateLine, Status, Subscription, SubscriptionLine
from .workflow import STEPPER, available_transitions


def _toast(response, message, level="success"):
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "level": level}})
    return response


def _get_or_404(user, pk):
    sub = selectors.get_subscription(user, pk)
    if sub is None:
        from django.http import Http404

        raise Http404
    return sub


# --- Subscriptions --------------------------------------------------------------------


@internal_required
@require_GET
def subscription_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    page = paginate(request, selectors.subscription_queryset(request.user, q, status))
    ctx = {
        "page_obj": page,
        "q": q,
        "status": status,
        "page_title": "Subscriptions",
        "target": "subscription-results",
        "statuses": Status.choices,
    }
    template = (
        "subscriptions/_subscription_results.html"
        if is_htmx(request)
        else "subscriptions/subscription_list.html"
    )
    return render(request, template, ctx)


@internal_required
def subscription_create(request):
    form = SubscriptionCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sub = services.create_subscription(actor=request.user, **form.cleaned_data)
        messages.success(
            request, f"{sub.number} was created. Add products, then send the quotation."
        )
        return redirect("subscriptions:detail", pk=sub.pk)
    return render(
        request,
        "subscriptions/subscription_form.html",
        {"form": form, "page_title": "New subscription"},
    )


def _detail_ctx(request, sub, line_form=None):
    today = timezone.localdate()
    priced = selectors.priced_lines(sub, today)
    return {
        "sub": sub,
        "page_title": sub.number,
        "priced": priced,
        "line_form": line_form or SubscriptionLineForm(),
        "actions": available_transitions(sub),
        "stepper": STEPPER,
        "history": sub.history.select_related("changed_by"),
        "invoices": sub.invoices.order_by("-period_start")[:12],
        "can_renew": sub.status in (Status.ACTIVE, Status.CLOSED) and sub.plan.renewable,
        "today": today,
    }


@internal_required
@require_GET
def subscription_detail(request, pk):
    sub = _get_or_404(request.user, pk)
    return render(request, "subscriptions/subscription_detail.html", _detail_ctx(request, sub))


@internal_required
def subscription_edit(request, pk):
    sub = _get_or_404(request.user, pk)
    if not sub.is_editable:
        messages.error(request, "Only draft and quotation subscriptions can be edited.")
        return redirect("subscriptions:detail", pk=pk)
    form = SubscriptionEditForm(request.POST or None, instance=copy.copy(sub))
    if request.method == "POST" and form.is_valid():
        services.update_subscription(actor=request.user, subscription=sub, **form.cleaned_data)
        messages.success(request, f"{sub.number} was updated.")
        return redirect("subscriptions:detail", pk=pk)
    return render(
        request,
        "subscriptions/subscription_form.html",
        {"form": form, "sub": sub, "page_title": f"Edit {sub.number}"},
    )


@internal_required
@require_POST
def subscription_transition(request, pk):
    sub = _get_or_404(request.user, pk)
    try:
        updated = services.transition(
            actor=request.user, subscription=sub, to=request.POST.get("to", "")
        )
    except services.TransitionError as exc:
        for problem in exc.problems:
            messages.error(request, problem)
    else:
        messages.success(
            request, f"{updated.number} is now {updated.get_status_display().lower()}."
        )
    return redirect("subscriptions:detail", pk=pk)


@internal_required
@require_POST
def subscription_renew(request, pk):
    sub = _get_or_404(request.user, pk)
    try:
        new = services.renew(actor=request.user, subscription=sub)
    except services.TransitionError as exc:
        for problem in exc.problems:
            messages.error(request, problem)
        return redirect("subscriptions:detail", pk=pk)
    messages.success(request, f"Renewal {new.number} was created as a draft.")
    return redirect("subscriptions:detail", pk=new.pk)


def _lines_response(request, sub, form, toast=None, level="success"):
    ctx = _detail_ctx(request, sub, form)
    if is_htmx(request):
        response = render(request, "subscriptions/_lines.html", ctx)
        return _toast(response, toast, level) if toast else response
    if toast:
        messages.add_message(
            request, messages.SUCCESS if level == "success" else messages.ERROR, toast
        )
    return render(request, "subscriptions/subscription_detail.html", ctx)


@internal_required
@require_POST
def line_create(request, pk):
    sub = _get_or_404(request.user, pk)
    if not sub.is_editable:
        return _lines_response(request, sub, None, "This subscription is locked.", "error")
    form = SubscriptionLineForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        services.add_line(
            actor=request.user,
            subscription=sub,
            product=d["product"],
            variant=d["variant"],
            quantity=d["quantity"],
            tax=d["tax"],
        )
        if is_htmx(request):
            return _lines_response(request, sub, None, "Line added.")
        messages.success(request, "Line added.")
        return redirect("subscriptions:detail", pk=pk)
    return _lines_response(request, sub, form)


@internal_required
@require_POST
def line_delete(request, pk):
    line = get_object_or_404(SubscriptionLine.objects.select_related("subscription"), pk=pk)
    sub = _get_or_404(request.user, line.subscription_id)
    try:
        services.remove_line(actor=request.user, line=line)
    except services.LockedError:
        return _lines_response(request, sub, None, "This subscription is locked.", "error")
    if is_htmx(request):
        return _lines_response(request, sub, None, "Line removed.")
    messages.success(request, "Line removed.")
    return redirect("subscriptions:detail", pk=sub.pk)


# --- Quotation templates --------------------------------------------------------------


@internal_required
@require_GET
def template_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.template_queryset(q))
    ctx = {
        "page_obj": page,
        "q": q,
        "page_title": "Quotation templates",
        "target": "template-results",
    }
    template = (
        "subscriptions/_template_results.html"
        if is_htmx(request)
        else "subscriptions/template_list.html"
    )
    return render(request, template, ctx)


@admin_required
def template_create(request):
    form = QuotationTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            template = services.create_template(actor=request.user, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A template with this name already exists.")
        else:
            messages.success(
                request, f"Template {template.name} was created. Add its product lines below."
            )
            return redirect("qtemplates:detail", pk=template.pk)
    return render(
        request, "subscriptions/template_form.html", {"form": form, "page_title": "Add template"}
    )


def _template_ctx(template, line_form=None):
    lines = template.lines.select_related("product", "variant")
    return {
        "template": template,
        "page_title": template.name,
        "lines": lines,
        "total": sum((l.amount for l in lines), 0),
        "line_form": line_form or TemplateLineForm(),
    }


@internal_required
@require_GET
def template_detail(request, pk):
    template = get_object_or_404(QuotationTemplate.objects.select_related("plan"), pk=pk)
    return render(request, "subscriptions/template_detail.html", _template_ctx(template))


@admin_required
def template_edit(request, pk):
    template = get_object_or_404(QuotationTemplate, pk=pk)
    form = QuotationTemplateForm(request.POST or None, instance=copy.copy(template))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_template(actor=request.user, template=template, **form.cleaned_data)
        except DuplicateError:
            form.add_error("name", "A template with this name already exists.")
        else:
            messages.success(request, f"Template {form.cleaned_data['name']} was updated.")
            return redirect("qtemplates:detail", pk=pk)
    return render(
        request,
        "subscriptions/template_form.html",
        {"form": form, "template": template, "page_title": f"Edit {template.name}"},
    )


@admin_required
@require_POST
def template_toggle_active(request, pk):
    template = get_object_or_404(QuotationTemplate, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_template_active(actor=request.user, template=template, active=active)
    messages.success(request, f"{template.name} was {'activated' if active else 'deactivated'}.")
    return redirect("qtemplates:detail", pk=pk)


def _template_lines_response(request, template, form, toast=None):
    ctx = _template_ctx(template, form)
    if is_htmx(request):
        response = render(request, "subscriptions/_template_lines.html", ctx)
        return _toast(response, toast) if toast else response
    return render(request, "subscriptions/template_detail.html", ctx)


@admin_required
@require_POST
def template_line_create(request, pk):
    template = get_object_or_404(QuotationTemplate.objects.select_related("plan"), pk=pk)
    form = TemplateLineForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        services.add_template_line(
            actor=request.user,
            template=template,
            product=d["product"],
            variant=d["variant"],
            quantity=d["quantity"],
        )
        if is_htmx(request):
            return _template_lines_response(request, template, None, "Line added.")
        messages.success(request, "Line added.")
        return redirect("qtemplates:detail", pk=pk)
    return _template_lines_response(request, template, form)


@admin_required
@require_POST
def template_line_delete(request, pk):
    line = get_object_or_404(QuotationTemplateLine.objects.select_related("template__plan"), pk=pk)
    template = line.template
    services.remove_template_line(actor=request.user, line=line)
    if is_htmx(request):
        return _template_lines_response(request, template, None, "Line removed.")
    messages.success(request, "Line removed.")
    return redirect("qtemplates:detail", pk=template.pk)
