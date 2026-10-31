from django.shortcuts import render


def home(request):
    """Placeholder landing page; replaced by the role-aware dashboard later."""
    return render(request, "core/home.html", {"page_title": "Dashboard"})
