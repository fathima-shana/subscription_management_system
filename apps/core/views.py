from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Placeholder landing page; replaced by the role-aware dashboard in a later step."""
    return render(request, "core/home.html", {"page_title": "Dashboard"})
