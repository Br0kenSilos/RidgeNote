import pytest
from accounts import services
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db


def test_anonymous_root_redirects_to_login(client):
    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == f"{reverse('accounts:login')}?next=/"


def test_authenticated_root_renders_home_dashboard(client):
    user = get_user_model().objects.create_user(
        username="foundation-home-user",
        password="LongUniquePassword123!",
        setup_completed_at=timezone.now(),
    )
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert b"home-dashboard" in response.content
    assert b"RidgeNote is running." not in response.content


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
def test_health_status_page_proves_database_and_static_marker(client):
    response = client.get(reverse("health_status"))

    assert response.status_code == 200
    assert b"RidgeNote is running." in response.content
    assert b"PostgreSQL answered the database probe" in response.content
    assert b"data-ridgenote-static-check" in response.content


def test_health_endpoint_uses_database(client):
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": 1}
