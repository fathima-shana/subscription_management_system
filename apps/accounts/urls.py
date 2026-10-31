from django.urls import path

from apps.core.ratelimit import rate_limit

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", rate_limit("login", 10, 300)(views.LoginView.as_view()), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("signup/", rate_limit("signup", 10, 3600)(views.signup), name="signup"),
    # Password reset: the reset link is emailed to the registered address only.
    path(
        "password-reset/",
        rate_limit("reset", 5, 3600)(views.PasswordResetView.as_view()),
        name="password_reset",
    ),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
