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
