from django import forms

from apps.core.forms import TailwindFormMixin

from .models import Tax, TaxType


class TaxForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Tax
        fields = ["name", "tax_type", "rate"]
        widgets = {"name": forms.TextInput(attrs={"autofocus": True})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Tax.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A tax with this name already exists.")
        return name

    def clean(self):
        cleaned = super().clean()
        rate, kind = cleaned.get("rate"), cleaned.get("tax_type")
        if rate is not None and rate < 0:
            self.add_error("rate", "The rate cannot be negative.")
        elif rate is not None and kind == TaxType.PERCENTAGE and rate > 100:
            self.add_error("rate", "A percentage tax cannot be more than 100.")
        return cleaned
