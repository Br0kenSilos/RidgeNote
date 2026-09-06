from __future__ import annotations

from accounts.models import User
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden


def admin_required(request: HttpRequest) -> HttpResponse | None:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.is_active or request.user.role != User.ROLE_ADMIN:
        return HttpResponseForbidden("RidgeNote administrator access is required.")
    return None
