from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Landing page: the business dashboard for admin/staff, the portal home for customers.

    This is the one place where core hands over to the feature apps, so they are imported here
    (not at the top) to keep `core` free of dependencies on them.
    """
    if request.user.is_internal:
        from apps.reports.views import internal_dashboard

        return internal_dashboard(request)
    from apps.portal.views import customer_dashboard

    return customer_dashboard(request)


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)
