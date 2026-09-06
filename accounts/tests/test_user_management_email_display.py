"""User Management email display.

Email addresses are visible on the
administrator User Management surfaces (`user_list`,
`user_detail`) so administrators can inspect the identity data they
manage. This is display-only: no editable email surface is
introduced here (the Create User form has its own editable `email`
field), and privacy-sensitive surfaces that
deliberately exclude email (e.g. admin-recovery owner-selection
labels, covered by `notes/tests/test_admin_recovery.py`) are untouched.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import User

PASSWORD = "LongUniquePassword123!"


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def create_admin(username="admin", *, password=PASSWORD, **kwargs):
    return create_account(username, role=User.ROLE_ADMIN, password=password, **kwargs)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


@pytest.mark.django_db
def test_user_list_shows_nonblank_email():
    admin = create_admin("list-email-admin")
    create_account("list-email-user", email="visible@example.com")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))

    assert response.status_code == 200
    assert "visible@example.com" in response.content.decode()


@pytest.mark.django_db
def test_user_list_shows_not_set_for_blank_email():
    admin = create_admin("list-blank-admin")
    create_account("list-blank-user", email="")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Not set" in content


@pytest.mark.django_db
def test_user_detail_shows_nonblank_email():
    admin = create_admin("detail-email-admin")
    target = create_account("detail-email-user", email="detail@example.com")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert response.status_code == 200
    assert "<dd>detail@example.com</dd>" in response.content.decode()


@pytest.mark.django_db
def test_user_detail_shows_not_set_for_blank_email():
    admin = create_admin("detail-blank-admin")
    target = create_account("detail-blank-user", email="")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert "<dd>Not set</dd>" in content


@pytest.mark.django_db
def test_user_detail_displays_email_canonically_lowercase():
    admin = create_admin("detail-case-admin")
    target = create_account("detail-case-user", email="Mixed@Example.COM")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))
    content = response.content.decode()

    assert "<dd>mixed@example.com</dd>" in content
    assert "Mixed@Example.COM" not in content


@pytest.mark.django_db
def test_create_user_form_still_has_editable_email_field_unchanged():
    # Regression guard: the Create User
    # form collects email independently of the display-only surfaces
    # covered elsewhere in this file.
    admin = create_admin("create-form-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'name="email"' in content


# ---------------------------------------------------------------------------
# Duplicate-email error styling
# ---------------------------------------------------------------------------


def _create_user_payload(**overrides):
    payload = {
        "setup_method": "temporary_password",
        "username": "styling-new-user",
        "display_name": "Styling New User",
        "email": "taken@example.com",
        "role": User.ROLE_USER,
        "is_active": "on",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_duplicate_email_still_returns_a_field_level_error():
    admin = create_admin("styling-admin")
    create_account("styling-existing-user", email="taken@example.com")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_create"), _create_user_payload())
    content = response.content.decode()

    assert response.status_code == 200
    assert "A user with that email already exists." in content
    assert not get_user_model().objects.filter(username="styling-new-user").exists()


@pytest.mark.django_db
def test_duplicate_email_error_uses_established_visible_errorlist_markup():
    admin = create_admin("styling-markup-admin")
    create_account("styling-markup-existing-user", email="markup@example.com")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="styling-markup-new-user", email="markup@example.com"),
    )
    content = response.content.decode()

    assert '<ul class="errorlist"' in content
    # The error sits inside the shared `.form-field` wrapper that now
    # carries the red validation-error styling, immediately after the
    # Email input and before the submit button.
    field_pos = content.index('id="id_email"')
    error_pos = content.index('<ul class="errorlist"', field_pos)
    submit_pos = content.index("Create User</button>")
    assert field_pos < error_pos < submit_pos


@pytest.mark.django_db
def test_duplicate_email_submission_produces_no_raw_db_error():
    admin = create_admin("styling-no-500-admin")
    create_account("styling-no-500-existing-user", email="no500@example.com")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="styling-no-500-new-user", email="no500@example.com"),
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_normal_user_creation_still_succeeds_unchanged():
    admin = create_admin("styling-normal-admin")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="styling-normal-user", email="normal@example.com"),
        follow=True,
    )

    assert response.status_code == 200
    created = get_user_model().objects.get(username="styling-normal-user")
    assert created.email == "normal@example.com"
