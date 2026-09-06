from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def database_probe() -> int:
    with connection.cursor() as cursor:
        cursor.execute("select 1")
        row = cursor.fetchone()
    return int(row[0])


def health_status(request: HttpRequest) -> HttpResponse:
    """Human-readable status page. Not the machine monitoring endpoint --
    see `health()` below for that."""
    return render(request, "core/health_status.html", {"database_probe": database_probe()})


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "database": database_probe()})


@login_required
def help_page(request: HttpRequest) -> HttpResponse:
    """The canonical, no-JS-reachable Help page. Renders the same
    `core/help/_body.html` partial the global Help dialog
    (`base.html`) uses, so the two never maintain independent copies
    of Help content."""
    return render(request, "core/help/page.html")
