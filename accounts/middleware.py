from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from accounts import services


class SessionSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _wants_json_response(self, request) -> bool:
        accept_header = request.headers.get("Accept", "")
        return request.path.endswith(("/autosave/", "/freshness/")) or (
            "application/json" in accept_header
        )

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            expected_generation = request.session.get(services.SESSION_GENERATION_KEY)
            session_expired = services.session_is_expired(request)
            if expected_generation != user.session_generation or session_expired:
                logout(request)
                if self._wants_json_response(request):
                    return JsonResponse(
                        {"ok": False, "error": "authentication_required"},
                        status=401,
                    )
                return redirect("accounts:login")

            if user.must_change_password and request.path not in {
                reverse("accounts:forced_password_change"),
                reverse("accounts:logout"),
            }:
                return redirect("accounts:forced_password_change")

        response = self.get_response(request)

        user = getattr(request, "user", None)
        if user and user.is_authenticated and services.should_refresh_session_activity(request):
            services.mark_session_activity(request)
        return response
