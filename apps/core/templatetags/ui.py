"""Small presentation helpers shared by every template."""
from django import template

register = template.Library()

# Status -> Tailwind classes. One place so badges look identical everywhere.
_BADGE = {
    "draft": "bg-slate-100 text-slate-700 ring-slate-200",
    "quotation": "bg-amber-50 text-amber-700 ring-amber-200",
    "confirmed": "bg-sky-50 text-sky-700 ring-sky-200",
    "active": "bg-emerald-50 text-emerald-700 ring-emerald-200",
    "closed": "bg-slate-200 text-slate-600 ring-slate-300",
    "paid": "bg-emerald-50 text-emerald-700 ring-emerald-200",
    "overdue": "bg-rose-50 text-rose-700 ring-rose-200",
    "cancelled": "bg-rose-50 text-rose-700 ring-rose-200",
    "scheduled": "bg-sky-50 text-sky-700 ring-sky-200",
    "expired": "bg-slate-200 text-slate-600 ring-slate-300",
    "exhausted": "bg-amber-50 text-amber-700 ring-amber-200",
    "inactive": "bg-slate-100 text-slate-500 ring-slate-200",
}
_DEFAULT = "bg-slate-100 text-slate-700 ring-slate-200"


@register.simple_tag
def badge_classes(status: str) -> str:
    return _BADGE.get(str(status).lower(), _DEFAULT)


@register.filter
def words(value):
    """Split a string on whitespace: {% for w in "a b c"|words %}."""
    return str(value).split()


@register.filter
def money(value):
    """Format a Decimal/number as Indian-style currency with 2 decimals."""
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"
