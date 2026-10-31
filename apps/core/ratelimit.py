"""Tiny fixed-window rate limiter backed by Django's cache (no third-party service).

Used on login, signup and password reset to slow down brute force and abuse.
Keyed by client IP using REMOTE_ADDR only; X-Forwarded-For is client-controlled
and must not be trusted unless a known proxy sets it.
"""
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "unknown")


def rate_limit(scope: str, limit: int, window: int, methods=("POST",)):
    """Allow `limit` requests per `window` seconds per IP for the given HTTP methods."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method in methods:
                key = f"rl:{scope}:{client_ip(request)}"
                cache.add(key, 0, window)  # starts the window only if absent
                try:
                    count = cache.incr(key)
                except ValueError:  # key expired between add() and incr()
                    cache.set(key, 1, window)
                    count = 1
                if count > limit:
                    return HttpResponse(
                        "Too many attempts. Please wait a few minutes and try again.",
                        status=429,
                        content_type="text/plain",
                    )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
