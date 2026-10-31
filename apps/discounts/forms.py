from django import forms

from apps.catalog.models import Product
from apps.core.forms import TailwindFormMixin

from .models import AppliesTo, Discount, DiscountType


class DiscountForm(TailwindFormMixin, forms.ModelForm):
    products = forms.ModelMultipleChoiceField(
        queryset=Product.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Hold Ctrl (or Cmd) to select several.",
    )

    class Meta:
        model = Discount
        fields = [
            "name",
            "discount_type",
            "value",
            "applies_to",
            "products",
            "min_purchase",
            "min_quantity",
            "start_date",
            "end_date",
            "usage_limit",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "applies_to": forms.Select(attrs={"x-model": "kind"}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = Product.objects.filter(is_active=True) | (
            self.instance.products.all() if self.instance.pk else Product.objects.none()
        )
        self.fields["products"].queryset = (
            self.fields["products"].queryset.distinct().order_by("name")
        )
        if self.instance.pk:
            self.fields["products"].initial = list(
                self.instance.products.values_list("pk", flat=True)
            )
        self.fields["min_purchase"].required = False
        self.fields["min_quantity"].required = False

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Discount.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A discount with this name already exists.")
        return name

    def clean_min_purchase(self):
        value = self.cleaned_data.get("min_purchase")
        if value is None:
            return 0
        if value < 0:
            raise forms.ValidationError("The minimum purchase cannot be negative.")
        return value

    def clean_min_quantity(self):
        value = self.cleaned_data.get("min_quantity")
        if value is None:
            return 1
        if value < 1:
            raise forms.ValidationError("The minimum quantity must be at least 1.")
        return value

    def clean_usage_limit(self):
        value = self.cleaned_data.get("usage_limit")
        if value is not None and value < 1:
            raise forms.ValidationError(
                "The usage limit must be at least 1, or empty for unlimited."
            )
        if value is not None and self.instance.pk and value < self.instance.used_count:
            raise forms.ValidationError(
                f"It has already been used {self.instance.used_count} times."
            )
        return value

    def clean(self):
        cleaned = super().clean()
        kind, value = cleaned.get("discount_type"), cleaned.get("value")
        if value is not None:
            if value <= 0:
                self.add_error("value", "The value must be more than 0.")
            elif kind == DiscountType.PERCENTAGE and value > 100:
                self.add_error("value", "A percentage discount cannot be more than 100.")
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        if cleaned.get("applies_to") == AppliesTo.PRODUCT and not cleaned.get("products"):
            self.add_error("products", "Select at least one product.")
        return cleaned
