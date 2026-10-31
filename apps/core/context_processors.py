from .navigation import items_for


def navigation(request):
    user = getattr(request, "user", None)
    if user is None:
        return {"nav_items": []}
    return {"nav_items": items_for(user, request.path)}
