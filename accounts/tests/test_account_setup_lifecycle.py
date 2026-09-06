"""Onboarding Lifecycle Foundation.

Covers `accounts.User.setup_completed_at`/`has_completed_setup` and the
two current production paths that must stamp setup-complete
immediately (initial-admin bootstrap, administrator direct-password
user creation). No current production path in this codebase produces a
`setup_completed_at=NULL` user.

Setup-incomplete authentication gating (whether a manually-constructed
NULL-setup user can log in) is covered in
`test_account_setup_authentication.py`, which is the
authoritative place for that behavior.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
OTHER_PASSWORD = "AnotherLongPassword123!"


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


def password_payload(password=PASSWORD):
    return {"password1": password, "password2": password}


# ---------------------------------------------------------------------------
# has_completed_setup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_has_completed_setup_is_false_when_setup_completed_at_is_null():
    user = create_account("null-setup-user", setup_completed_at=None)
    assert user.has_completed_setup is False


@pytest.mark.django_db
def test_has_completed_setup_is_true_when_setup_completed_at_is_set():
    user = create_account("complete-setup-user", setup_completed_at=timezone.now())
    assert user.has_completed_setup is True


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_initial_admin_setup_is_setup_complete():
    response = Client().post(
        reverse("accounts:setup"),
        {
            "username": "bootstrap-admin",
            "display_name": "Bootstrap Admin",
            "email": "",
            **password_payload(),
        },
    )

    assert response.status_code == 302
    admin = get_user_model().objects.get(username="bootstrap-admin")
    assert admin.setup_completed_at is not None
    assert admin.has_completed_setup is True


@pytest.mark.django_db
def test_initial_admin_setup_semantics_otherwise_unchanged():
    # Regression guard: this must not have altered any of the
    # existing bootstrap guarantees.
    response = Client().post(
        reverse("accounts:setup"),
        {
            "username": "bootstrap-admin-2",
            "display_name": "Bootstrap Admin 2",
            "email": "",
            **password_payload(),
        },
    )

    assert response.status_code == 302
    admin = get_user_model().objects.get(username="bootstrap-admin-2")
    assert admin.role == User.ROLE_ADMIN
    assert admin.is_active is True
    assert admin.is_staff is False
    assert admin.is_superuser is False
    assert admin.must_change_password is False
    assert services.admin_exists() is True
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_INITIAL_ADMIN_CREATED, target_user=admin
    ).exists()


# ---------------------------------------------------------------------------
# Administrator direct-password user creation
# ---------------------------------------------------------------------------


def _create_user_payload(**overrides):
    payload = {
        "setup_method": "temporary_password",
        "username": "created-user",
        "display_name": "Created User",
        "email": "",
        "role": User.ROLE_USER,
        "is_active": "on",
        # Mirrors the Set password checkbox's own `initial=True`
        # (checked-by-default) state, as a real browser submitting the
        # as-rendered form would. Pass `require_password_change=False`
        # to model an administrator unchecking it.
        "require_password_change": "on",
        **password_payload(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_admin_created_user_is_setup_complete():
    admin = create_admin("creator-admin")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_create"), _create_user_payload())

    assert response.status_code == 302
    created = get_user_model().objects.get(username="created-user")
    assert created.setup_completed_at is not None
    assert created.has_completed_setup is True


@pytest.mark.django_db
def test_admin_created_user_default_checked_requires_change():
    """Set password's
    `require_password_change` checkbox defaults checked, producing
    `must_change_password=True` -- but is administrator-discretionary
    (see `test_admin_created_user_unchecked_does_not_require_change`
    below), not unconditional. Distinct from `UserPasswordResetForm`'s
    own, separate `require_password_change` control, which governs
    resetting an already setup-complete account's password."""
    admin = create_admin("creator-admin-2")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="require-change-user"),
    )

    created = get_user_model().objects.get(username="require-change-user")
    assert created.must_change_password is True
    assert created.setup_completed_at is not None


@pytest.mark.django_db
def test_admin_created_user_unchecked_does_not_require_change():
    """Unchecking Require password change on
    Set password produces `must_change_password=False`."""
    admin = create_admin("creator-admin-3")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="no-change-user", require_password_change=False),
    )

    created = get_user_model().objects.get(username="no-change-user")
    assert created.must_change_password is False
    assert created.setup_completed_at is not None


@pytest.mark.django_db
def test_admin_created_user_role_and_email_unchanged():
    admin = create_admin("creator-admin-4")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        _create_user_payload(
            username="role-email-user", role=User.ROLE_ADMIN, email="role-email@example.com"
        ),
    )

    created = get_user_model().objects.get(username="role-email-user")
    assert created.role == User.ROLE_ADMIN
    assert created.email == "role-email@example.com"
    assert created.setup_completed_at is not None


@pytest.mark.django_db
def test_admin_created_user_audit_event_unchanged():
    admin = create_admin("creator-admin-5")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="audit-user"),
    )

    created = get_user_model().objects.get(username="audit-user")
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_USER_CREATED, target_user=created
    ).exists()


@pytest.mark.django_db
def test_admin_created_user_redirect_unchanged():
    admin = create_admin("creator-admin-6")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="redirect-user"),
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:user_list")


# ---------------------------------------------------------------------------
# Authentication regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_normal_setup_complete_user_still_logs_in():
    create_account("normal-login-user", setup_completed_at=timezone.now())

    response = Client().post(
        reverse("accounts:login"), {"username": "normal-login-user", "password": PASSWORD}
    )

    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Production-path regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bootstrap_never_produces_a_setup_incomplete_user():
    Client().post(
        reverse("accounts:setup"),
        {
            "username": "no-null-bootstrap-admin",
            "display_name": "No Null Bootstrap Admin",
            "email": "",
            **password_payload(),
        },
    )

    admin = get_user_model().objects.get(username="no-null-bootstrap-admin")
    assert admin.setup_completed_at is not None


@pytest.mark.django_db
def test_admin_user_create_never_produces_a_setup_incomplete_user():
    admin = create_admin("no-null-creator-admin")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        _create_user_payload(username="no-null-created-user"),
    )

    created = get_user_model().objects.get(username="no-null-created-user")
    assert created.setup_completed_at is not None
