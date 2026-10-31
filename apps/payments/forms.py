import datetime
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import TailwindFormMixin

from .models import Method


class PaymentForm(TailwindFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(choices=[("", "Choose a method")] + list(Method.choices))
    payment_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    note = forms.CharField(
        max_length=200,
        required=False,
        label="Reference / note",
        widget=forms.TextInput(attrs={"placeholder": "Cheque no., UPI ref, bank reference..."}),
    )

    def __init__(self, *args, invoice, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        self.fields["amount"].initial = invoice.balance
        self.fields["payment_date"].initial = timezone.localdate()
        self.fields["amount"].help_text = (
            f"Balance due: ₹{invoice.balance:,.2f}. Enter less for a part payment."
        )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount > self.invoice.balance:
            raise forms.ValidationError(
                f"That is more than the balance due (₹{self.invoice.balance:,.2f})."
            )
        return amount

    def clean_payment_date(self):
        value = self.cleaned_data["payment_date"]
        if value > timezone.localdate():
            raise forms.ValidationError("The payment date cannot be in the future.")
        return value
