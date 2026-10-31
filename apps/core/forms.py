"""Form helpers shared by every module."""
from django import forms

_INPUT = (
    "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm "
    "placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 "
    "focus:ring-brand-500/20"
)
_CHECKBOX = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"


class TailwindFormMixin:
    """Gives every widget the same Tailwind look, so templates stay free of styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css = _CHECKBOX if isinstance(widget, forms.CheckboxInput) else _INPUT
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()
