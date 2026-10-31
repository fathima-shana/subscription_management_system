from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)

from apps.core.forms import TailwindFormMixin

from .models import User


class SignupForm(TailwindFormMixin, forms.Form):
    full_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name", "autofocus": True})
    )
    email = forms.EmailField(max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    company_name = forms.CharField(
        max_length=150, required=False, label="Company (optional)",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "x-model": "pw"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class EmailLoginForm(TailwindFormMixin, AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Incorrect email or password.",
    }


class StyledPasswordResetForm(TailwindFormMixin, PasswordResetForm):
    email = forms.EmailField(
        label="Email", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )


class StyledSetPasswordForm(TailwindFormMixin, SetPasswordForm):
    """Used by the reset link. Runs the same password policy as signup."""


class StyledPasswordChangeForm(TailwindFormMixin, PasswordChangeForm):
    """For signed-in users changing their own password."""


class StaffCreateForm(TailwindFormMixin, forms.Form):
    """Admin adds a staff member. There is deliberately no role field: it is always 'staff'."""

    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autofocus": True}))
    email = forms.EmailField(max_length=254)
    password = forms.CharField(
        label="Temporary password",
        strip=False,
        help_text="Share it securely. The staff member can change it after signing in.",
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "x-model": "pw", ":type": "show ? 'text' : 'password'"}
        ),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(password, candidate)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return cleaned
