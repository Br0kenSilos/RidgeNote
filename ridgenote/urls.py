"""URL configuration for RidgeNote."""

from core import views
from django.urls import include, path
from notes import views as note_views

urlpatterns = [
    path("", note_views.home, name="home"),
    path("health/", views.health, name="health"),
    path("health-status/", views.health_status, name="health_status"),
    path("help/", views.help_page, name="help"),
    path("", include("notes.urls")),
    path("", include("accounts.urls")),
]
