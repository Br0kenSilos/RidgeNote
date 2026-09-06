"""Setup-Completion Authentication Gate.

Covers `CanonicalUsernameModelBackend.user_can_authenticate()`'s
`has_completed_setup` requirement and `services.record_failed_login()`'s
matching early-return (mirroring the existing `is_active` precedent
exactly, so setup-incomplete attempts never contribute to lockout).

Every fixture in this file explicitly passes `setup_completed_at` --
never relying on a default -- since that is precisely the field under
test.
"""

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
INVALID_LOGIN_MESSAGE = "The username or password is incorrect, or the account cannot sign in."


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


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backend_authenticates_active_setup_complete_user():
    create_account("active-complete-user", is_active=True, setup_completed_at=timezone.now())

    user = authenticate(username="active-complete-user", password=PASSWORD)

    assert user is not None
    assert user.username == "active-complete-user"


@pytest.mark.django_db
def test_backend_rejects_active_setup_incomplete_user():
    create_account("active-incomplete-user", is_active=True, setup_completed_at=None)

    user = authenticate(username="active-incomplete-user", password=PASSWORD)

    assert user is None


@pytest.mark.django_db
def test_backend_rejects_inactive_setup_complete_user():
    create_account("inactive-complete-user", is_active=False, setup_completed_at=timezone.now())

    user = authenticate(username="inactive-complete-user", password=PASSWORD)

    assert user is None


@pytest.mark.django_db
def test_backend_rejects_inactive_setup_incomplete_user():
    create_account("inactive-incomplete-user", is_active=False, setup_completed_at=None)

    user = authenticate(username="inactive-incomplete-user", password=PASSWORD)

    assert user is None


@pytest.mark.django_db
def test_backend_still_rejects_wrong_password():
    create_account("wrong-password-user", is_active=True, setup_completed_at=timezone.now())

    user = authenticate(username="wrong-password-user", password="not-the-real-password")

    assert user is None


@pytest.mark.django_db
def test_backend_username_case_normalization_unchanged():
    create_account("CaseUser", is_active=True, setup_completed_at=timezone.now())

    user = authenticate(username="CASEUSER", password=PASSWORD)

    assert user is not None
    assert user.username == "caseuser"


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_view_setup_incomplete_correct_password_returns_generic_invalid_login():
    create_admin("gate-login-admin")
    create_account("gate-incomplete-user", setup_completed_at=None)

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-incomplete-user", "password": PASSWORD},
    )

    assert response.status_code == 200
    assert INVALID_LOGIN_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_login_view_response_does_not_mention_setup_or_invitation_state():
    create_admin("gate-login-admin-2")
    create_account("gate-incomplete-user-2", setup_completed_at=None)

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-incomplete-user-2", "password": PASSWORD},
    )
    content = response.content.decode().lower()

    assert "invit" not in content
    assert "setup" not in content
    assert "activat" not in content
    assert "pending" not in content


@pytest.mark.django_db
def test_login_view_setup_complete_user_still_logs_in():
    create_account("gate-complete-login-user", setup_completed_at=timezone.now())

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-complete-login-user", "password": PASSWORD},
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_login_view_inactive_user_behavior_unchanged():
    create_admin("gate-login-admin-3")
    create_account("gate-inactive-user", is_active=False, setup_completed_at=timezone.now())

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-inactive-user", "password": PASSWORD},
    )

    assert response.status_code == 200
    assert INVALID_LOGIN_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_login_view_wrong_password_behavior_unchanged():
    create_admin("gate-login-admin-4")
    create_account("gate-wrong-password-user", setup_completed_at=timezone.now())

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-wrong-password-user", "password": "incorrect"},
    )

    assert response.status_code == 200
    assert INVALID_LOGIN_MESSAGE in response.content.decode()


# ---------------------------------------------------------------------------
# Lockout / failure accounting
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_setup_incomplete_attempt_does_not_increment_failed_login_count():
    user = create_account("gate-lockout-user", setup_completed_at=None)

    services.record_failed_login(user.username)

    user.refresh_from_db()
    assert user.failed_login_count == 0


@pytest.mark.django_db
def test_setup_incomplete_attempt_does_not_advance_failure_window():
    user = create_account("gate-lockout-window-user", setup_completed_at=None)

    services.record_failed_login(user.username)

    user.refresh_from_db()
    assert user.failed_login_window_started_at is None


@pytest.mark.django_db
def test_setup_incomplete_attempt_does_not_set_lockout():
    user = create_account("gate-lockout-locked-user", setup_completed_at=None)

    services.record_failed_login(user.username)

    user.refresh_from_db()
    assert user.locked_until is None


@pytest.mark.django_db
def test_setup_incomplete_attempt_preserves_existing_lockout_state():
    # Mirrors the existing inactive-user precedent: the early return
    # happens before any lockout-state mutation, so pre-existing lockout
    # fields (however they got there) are left completely untouched.
    now = timezone.now()
    user = create_account(
        "gate-preserve-lockout-user",
        setup_completed_at=None,
        failed_login_count=3,
        failed_login_window_started_at=now,
    )

    services.record_failed_login(user.username)

    user.refresh_from_db()
    assert user.failed_login_count == 3
    assert user.failed_login_window_started_at == now


@pytest.mark.django_db
def test_setup_incomplete_attempt_records_login_failed_event_with_internal_detail():
    user = create_account("gate-audit-user", setup_completed_at=None)

    services.record_failed_login(user.username)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_LOGIN_FAILED, target_user=user)
    assert event.details == {"setup_incomplete": True}


@pytest.mark.django_db
def test_setup_incomplete_audit_detail_never_appears_in_response():
    create_admin("gate-audit-admin")
    create_account("gate-audit-response-user", setup_completed_at=None)

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-audit-response-user", "password": PASSWORD},
    )

    assert "setup_incomplete" not in response.content.decode()


# ---------------------------------------------------------------------------
# Forced password change
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_setup_complete_must_change_password_user_authenticates_and_is_redirected():
    create_admin("gate-forced-change-admin")
    create_account(
        "gate-forced-change-user",
        setup_completed_at=timezone.now(),
        must_change_password=True,
    )

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-forced-change-user", "password": PASSWORD},
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("accounts:forced_password_change")


@pytest.mark.django_db
def test_forced_password_change_completion_behavior_unaffected():
    from accounts.forms import ForcedPasswordChangeForm

    user = create_account(
        "gate-forced-complete-user",
        setup_completed_at=timezone.now(),
        must_change_password=True,
    )
    other_password = "AnotherLongPassword123!"
    request = RequestFactory().post("/password/change-required/")
    request.session = authenticated_client(user).session
    request.user = user

    form = ForcedPasswordChangeForm(
        {"password1": other_password, "password2": other_password}, user=user
    )
    assert form.is_valid()

    services.complete_forced_password_change(
        user=user, new_password=other_password, request=request
    )

    user.refresh_from_db()
    assert user.must_change_password is False
    assert user.check_password(other_password)


# ---------------------------------------------------------------------------
# Bootstrap / direct creation regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bootstrap_admin_remains_immediately_login_capable():
    Client().post(
        reverse("accounts:setup"),
        {
            "username": "gate-bootstrap-admin",
            "display_name": "Gate Bootstrap Admin",
            "email": "",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )

    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-bootstrap-admin", "password": PASSWORD},
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_admin_created_direct_password_user_remains_immediately_login_capable():
    admin = create_admin("gate-creator-admin")
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        {
            "setup_method": "temporary_password",
            "username": "gate-created-user",
            "display_name": "Gate Created User",
            "email": "",
            "role": User.ROLE_USER,
            "is_active": "on",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )

    created = get_user_model().objects.get(username="gate-created-user")
    assert created.setup_completed_at is not None
    response = Client().post(
        reverse("accounts:login"),
        {"username": "gate-created-user", "password": PASSWORD},
    )
    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Centrality regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backend_directly_proves_central_enforcement_independent_of_login_view():
    from accounts.auth_backends import CanonicalUsernameModelBackend

    create_account("gate-central-user", is_active=True, setup_completed_at=None)

    backend = CanonicalUsernameModelBackend()
    result = backend.authenticate(None, username="gate-central-user", password=PASSWORD)

    assert result is None


# ---------------------------------------------------------------------------
# Side effects
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_rejected_setup_incomplete_login_creates_no_authenticated_session():
    create_admin("gate-session-admin")
    create_account("gate-session-user", setup_completed_at=None)
    client = Client()

    client.post(
        reverse("accounts:login"),
        {"username": "gate-session-user", "password": PASSWORD},
    )

    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_rejected_setup_incomplete_login_does_not_change_session_generation():
    create_admin("gate-session-gen-admin")
    user = create_account("gate-session-gen-user", setup_completed_at=None)
    original_generation = user.session_generation

    Client().post(
        reverse("accounts:login"),
        {"username": "gate-session-gen-user", "password": PASSWORD},
    )

    user.refresh_from_db()
    assert user.session_generation == original_generation
