from django import forms

from apps.core.forms import TailwindFormMixin
from apps.taxes.models import Tax

from .models import Plan, Product, ProductVariant


class ProductForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "product_type", "sales_price", "cost_price", "default_tax", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only active taxes can be chosen, but keep the current one visible when editing.
        allowed = Tax.objects.filter(is_active=True)
        current = self.instance.default_tax_id
        if current:
            allowed = Tax.objects.filter(pk=current) | allowed
        self.fields["default_tax"].queryset = allowed.order_by("name")
        self.fields["default_tax"].empty_label = "No tax"

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Product.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A product with this name already exists.")
        return name

    def clean_sales_price(self):
        value = self.cleaned_data["sales_price"]
        if value < 0:
            raise forms.ValidationError("The price cannot be negative.")
        return value

    def clean_cost_price(self):
        value = self.cleaned_data["cost_price"]
        if value < 0:
            raise forms.ValidationError("The cost cannot be negative.")
        return value


class VariantForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ["attribute", "value", "extra_price"]
        labels = {"extra_price": "Extra price"}

    def __init__(self, *args, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.fields["attribute"].widget.attrs["placeholder"] = "e.g. Brand"
        self.fields["value"].widget.attrs["placeholder"] = "e.g. Odoo"
        self.fields["extra_price"].initial = 0

    def clean_extra_price(self):
        value = self.cleaned_data["extra_price"]
        if value < 0:
            raise forms.ValidationError("The extra price cannot be negative.")
        return value

    def clean(self):
        cleaned = super().clean()
        attribute, value = cleaned.get("attribute"), cleaned.get("value")
        if attribute and value:
            clash = self.product.variants.filter(
                attribute__iexact=attribute.strip(), value__iexact=value.strip()
            )
            clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(
                    "value",
                    f"{attribute.strip()}: {value.strip()} already exists for this product.",
                )
        return cleaned


class PlanForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            "name",
            "billing_period",
            "min_quantity",
            "start_date",
            "end_date",
            "auto_close",
            "closable",
            "pausable",
            "renewable",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Plan.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A plan with this name already exists.")
        return name

    def clean_min_quantity(self):
        value = self.cleaned_data["min_quantity"]
        if value < 1:
            raise forms.ValidationError("The minimum quantity must be at least 1.")
        return value

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        if cleaned.get("auto_close") and not end:
            self.add_error("auto_close", "Set an end date to use auto-close.")
        return cleaned
