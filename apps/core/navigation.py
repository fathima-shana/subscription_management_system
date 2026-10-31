"""Sidebar registry. Each module adds its own entries here; roles decide who sees what."""
from dataclasses import dataclass

from django.urls import reverse

ALL = ("admin", "staff", "customer")
INTERNAL = ("admin", "staff")
ADMIN = ("admin",)

# Simple stroke icons (24x24 viewBox).
ICONS = {
    "home": "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
    "users": "M17 20v-1a4 4 0 00-4-4H7a4 4 0 00-4 4v1M10 11a4 4 0 100-8 4 4 0 000 8zM21 20v-1a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75",
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    "box": "M21 8l-9-5-9 5v8l9 5 9-5V8zM3.3 7.5L12 12.5l8.7-5M12 22V12.5",
    "repeat": "M17 2l4 4-4 4M3 11V9a3 3 0 013-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 01-3 3H3",
    "tag": "M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0L3 13V3h10l7.6 7.6a2 2 0 010 2.8zM7.5 7.5h.01",
    "percent": "M19 5L5 19M7.5 9a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM16.5 18a1.5 1.5 0 100-3 1.5 1.5 0 000 3z",
}


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    roles: tuple
    icon: str
    exact: bool = False  # exact path match (dashboard) vs. prefix match (sections)


NAV_ITEMS = [
    NavItem("Dashboard", "core:home", ALL, "home", exact=True),
    NavItem("Customers", "customers:list", INTERNAL, "users"),
    NavItem("Products", "catalog:product_list", INTERNAL, "box"),
    NavItem("Plans", "catalog:plan_list", INTERNAL, "repeat"),
    NavItem("Taxes", "taxes:list", INTERNAL, "percent"),
    NavItem("Discounts", "discounts:list", INTERNAL, "tag"),
    NavItem("Staff", "staff:list", ADMIN, "shield"),
]


def items_for(user, path: str) -> list[dict]:
    if not user.is_authenticated:
        return []
    visible = []
    for item in NAV_ITEMS:
        if user.role not in item.roles:
            continue
        url = reverse(item.url_name)
        active = path == url if item.exact else path.startswith(url)
        visible.append(
            {"label": item.label, "url": url, "icon": ICONS[item.icon], "active": active}
        )
    return visible
