from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Placeholder landing page; replaced by the role-aware dashboard in a later step."""
    return render(request, "core/home.html", {"page_title": "Dashboard"})


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)
