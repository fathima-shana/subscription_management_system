import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_GET, require_POST

from apps.core.http import is_htmx, paginate

from . import selectors, services
from .forms import (
    EmailLoginForm,
    SignupForm,
    StaffCreateForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .models import Role, User
from .permissions import admin_required, internal_required


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.register_customer(
                email=data["email"],
                password=data["password1"],
                full_name=data["full_name"],
                company_name=data["company_name"],
            )
        except services.DuplicateEmailError:
            form.add_error("email", "An account with this email already exists.")
        else:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, f"Welcome to Brightloop, {user.get_short_name()}!")
            return redirect("core:home")
    return render(request, "accounts/signup.html", {"form": form, "page_title": "Create account"})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@method_decorator(login_required, name="dispatch")
class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("core:home")
    success_message = "Your password has been changed."

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title="Change password", **kwargs)


# --- Staff management (admin only) ------------------------------------------------

@admin_required
@require_GET
def staff_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.staff_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Staff", "target": "staff-results"}
    template = "accounts/_staff_results.html" if is_htmx(request) else "accounts/staff_list.html"
    return render(request, template, ctx)


@admin_required
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.create_staff(
                actor=request.user,
                email=data["email"],
                password=data["password"],
                full_name=data["full_name"],
            )
        except services.DuplicateEmailError:
            form.add_error("email", "An account with this email already exists.")
        else:
            messages.success(request, f"Staff member {user.full_name} was added.")
            return redirect("staff:list")
    return render(request, "accounts/staff_form.html", {"form": form, "page_title": "Add staff member"})


@admin_required
@require_POST
def staff_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk, role=Role.STAFF)  # admins/customers are not reachable here
    active = request.POST.get("active") == "1"
    services.set_staff_active(actor=request.user, user=user, active=active)
    verb = "reactivated" if active else "deactivated"
    if not is_htmx(request):
        messages.success(request, f"{user.full_name} was {verb}.")
        return redirect("staff:list")
    response = render(request, "accounts/_staff_row.html", {"u": user})
    response["HX-Trigger"] = json.dumps(
        {"toast": {"message": f"{user.full_name} was {verb}.", "level": "success"}}
    )
    return response


# --- Customer directory (admin + staff) --------------------------------------------

@internal_required
@require_GET
def customer_list(request):
    q = request.GET.get("q", "").strip()
    page = paginate(request, selectors.customer_queryset(q))
    ctx = {"page_obj": page, "q": q, "page_title": "Customers", "target": "customer-results"}
    template = "accounts/_customer_results.html" if is_htmx(request) else "accounts/customer_list.html"
    return render(request, template, ctx)
