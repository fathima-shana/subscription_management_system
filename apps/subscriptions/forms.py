from django import forms
from django.db.models import Prefetch
from django.utils import timezone

from apps.accounts.models import CustomerProfile, Role
from apps.catalog.models import Plan, Product, ProductVariant
from apps.core.forms import TailwindFormMixin
from apps.taxes.models import Tax

from .models import PaymentTerm, QuotationTemplate, Subscription


def _date_widget():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CustomerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        company = f"{obj.company_name} - " if obj.company_name else ""
        return f"{company}{obj.user.full_name} ({obj.user.email})"


class SubscriptionCreateForm(TailwindFormMixin, forms.ModelForm):
    customer = CustomerChoiceField(queryset=CustomerProfile.objects.none())
    template = forms.ModelChoiceField(
        queryset=QuotationTemplate.objects.none(),
        required=False,
        empty_label="No template",
        help_text="Copies the plan and product lines, and sets how long the quotation stays valid.",
    )
    plan = forms.ModelChoiceField(
        queryset=Plan.objects.none(),
        required=False,
        help_text="Not needed when you pick a template.",
    )

    class Meta:
        model = Subscription
        fields = [
            "customer",
            "template",
            "plan",
            "payment_term",
            "start_date",
            "expiry_date",
            "notes",
        ]
        widgets = {
            "start_date": _date_widget(),
            "expiry_date": _date_widget(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = (
            CustomerProfile.objects.select_related("user")
            .filter(user__role=Role.CUSTOMER, user__is_active=True)
            .order_by("user__full_name")
        )
        self.fields["template"].queryset = QuotationTemplate.objects.filter(
            is_active=True, plan__is_active=True
        ).order_by("name")
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True).order_by("name")
        self.fields["payment_term"].queryset = PaymentTerm.objects.all()
        self.fields["start_date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("template") and not cleaned.get("plan") and "plan" not in self.errors:
            self.add_error("plan", "Pick a plan, or choose a template.")
        start, end = cleaned.get("start_date"), cleaned.get("expiry_date")
        if start and end and end < start:
            self.add_error("expiry_date", "The expiry date cannot be before the start date.")
        return cleaned


class SubscriptionEditForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ["plan", "payment_term", "start_date", "expiry_date", "quote_valid_until", "notes"]
        widgets = {
            "start_date": _date_widget(),
            "expiry_date": _date_widget(),
            "quote_valid_until": _date_widget(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = Plan.objects.filter(is_active=True)
        if self.instance.plan_id:
            allowed = Plan.objects.filter(pk=self.instance.plan_id) | allowed
        self.fields["plan"].queryset = allowed.distinct().order_by("name")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("expiry_date")
        if start and end and end < start:
            self.add_error("expiry_date", "The expiry date cannot be before the start date.")
        return cleaned


class _ItemForm(TailwindFormMixin, forms.Form):
    """Pick a product or one of its variants from a single list, plus a quantity."""

    item = forms.ChoiceField(label="Product")
    quantity = forms.IntegerField(
        min_value=1, initial=1, widget=forms.NumberInput(attrs={"min": 1})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        products = (
            Product.objects.filter(is_active=True)
            .prefetch_related(
                Prefetch("variants", queryset=ProductVariant.objects.filter(is_active=True))
            )
            .order_by("name")
        )
        self._items = {}
        choices = [("", "Select a product")]
        for p in products:
            self._items[f"p{p.pk}"] = (p, None)
            choices.append((f"p{p.pk}", f"{p.name} - ₹{p.sales_price:,.2f}"))
            for v in p.variants.all():
                self._items[f"v{v.pk}"] = (p, v)
                choices.append((f"v{v.pk}", f"{p.name} ({v.label}) - ₹{p.price_for(v):,.2f}"))
        self.fields["item"].choices = choices

    def clean_quantity(self):
        value = self.cleaned_data["quantity"]
        if value < 1:
            raise forms.ValidationError("The quantity must be at least 1.")
        return value

    def clean(self):
        cleaned = super().clean()
        key = cleaned.get("item")
        if key:
            cleaned["product"], cleaned["variant"] = self._items[key]
        return cleaned


class SubscriptionLineForm(_ItemForm):
    tax = forms.ChoiceField(label="Tax", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._taxes = {str(t.pk): t for t in Tax.objects.filter(is_active=True).order_by("name")}
        self.fields["tax"].choices = [("", "Product default"), ("none", "No tax")] + [
            (k, t.label) for k, t in self._taxes.items()
        ]

    def clean(self):
        cleaned = super().clean()
        choice = cleaned.get("tax") or ""
        product = cleaned.get("product")
        if choice == "none":
            cleaned["tax"] = None
        elif choice:
            cleaned["tax"] = self._taxes[choice]
        else:
            cleaned["tax"] = (
                product.default_tax
                if product and product.default_tax and product.default_tax.is_active
                else None
            )
        return cleaned


class TemplateLineForm(_ItemForm):
    pass


class QuotationTemplateForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = QuotationTemplate
        fields = ["name", "plan", "validity_days", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = Plan.objects.filter(is_active=True)
        if self.instance.plan_id:
            allowed = Plan.objects.filter(pk=self.instance.plan_id) | allowed
        self.fields["plan"].queryset = allowed.distinct().order_by("name")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if (
            QuotationTemplate.objects.filter(name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("A template with this name already exists.")
        return name

    def clean_validity_days(self):
        value = self.cleaned_data["validity_days"]
        if value < 1:
            raise forms.ValidationError("The validity must be at least 1 day.")
        return value
