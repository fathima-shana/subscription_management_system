"""Production settings: everything secure by default."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

if SECRET_KEY.startswith("dev-only"):  # noqa: F405
    raise RuntimeError("Set DJANGO_SECRET_KEY for production.")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
