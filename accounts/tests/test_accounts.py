import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from threading import Thread
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connections
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.forms import SetupForm
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
OTHER_PASSWORD = "AnotherLongPassword123!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def assert_uses_compiled_stylesheet(response):
    assert b"core/dist/assets/app.css" in response.content


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


@pytest.mark.django_db
def test_setup_page_references_compiled_stylesheet():
    response = Client().get(reverse("accounts:setup"))

    assert response.status_code == 200
    assert_uses_compiled_stylesheet(response)


@pytest.mark.django_db
def test_login_page_references_compiled_stylesheet():
    create_admin()

    response = Client().get(reverse("accounts:login"))

    assert response.status_code == 200
    assert_uses_compiled_stylesheet(response)


@pytest.mark.django_db
def test_login_page_uses_dark_login_shell_only():
    create_admin()

    response = Client().get(reverse("accounts:login"))
    body = response.content.decode()

    assert response.status_code == 200
    # The canonical `<html>` element must be dark too, not just `<body>`,
    # so root-level theme propagation covers both.
    assert '<html lang="en" data-theme="dark">' in body
    assert '<body data-theme="dark" class="login-page">' in body
    assert 'class="content-shell login-shell"' in body
    assert 'class="login-panel"' in body
    assert "Private notes, simple by default." in body


@pytest.mark.django_db
def test_setup_page_keeps_default_warm_light_body_theme():
    response = Client().get(reverse("accounts:setup"))
    body = response.content.decode()

    assert response.status_code == 200
    assert '<html lang="en" data-theme="warm-light">' in body
    assert '<body data-theme="warm-light">' in body
    assert 'class="login-page"' not in body


@pytest.mark.django_db
def test_admin_user_list_references_compiled_stylesheet():
    admin = create_admin()

    response = authenticated_client(admin).get(reverse("accounts:user_list"))

    assert response.status_code == 200
    assert_uses_compiled_stylesheet(response)


@pytest.mark.django_db
def test_authenticated_landing_page_renders_notes_home():
    # The authenticated dashboard's heading text ("Welcome to RidgeNote")
    # is not distinctive enough on its own, so this test also checks for
    # dashboard-specific structure (workspace shell, Recent module).
    user = create_account("landing-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    assert_uses_compiled_stylesheet(response)
    assert b"home-dashboard" in response.content
    assert b"No notes yet" in response.content


@pytest.mark.django_db
def test_admin_navigation_includes_users_link_and_logout_post_form():
    admin = create_admin("nav-admin", display_name="Navigation Admin")

    response = authenticated_client(admin).get(reverse("home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'href="/admin/users/"' in body
    assert "Signed in as Navigation Admin" in body
    assert 'method="post" action="/logout/"' in body
    assert "csrfmiddlewaretoken" in body


@pytest.mark.django_db
def test_normal_user_navigation_omits_users_link():
    user = create_account("nav-user", display_name="Navigation User")

    response = authenticated_client(user).get(reverse("home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'href="/admin/users/"' not in body
    assert "Signed in as Navigation User" in body
    assert 'method="post" action="/logout/"' in body


@pytest.mark.django_db
def test_blank_display_name_falls_back_to_canonical_username_in_navigation():
    user = create_account("fallback-user", display_name="")

    response = authenticated_client(user).get(reverse("home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Signed in as fallback-user" in body


@pytest.mark.django_db
def test_user_list_and_detail_prefer_display_name_while_showing_canonical_username():
    admin = create_admin("people-admin", display_name="People Admin")
    target = create_account("casey-user", display_name="Casey User")
    client = authenticated_client(admin)

    list_response = client.get(reverse("accounts:user_list"))
    detail_response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert list_response.status_code == 200
    assert "Casey User" in list_response.content.decode()
    assert "casey-user" in list_response.content.decode()
    assert detail_response.status_code == 200
    assert "<h1>Casey User</h1>" in detail_response.content.decode()
    assert "<dd>casey-user</dd>" in detail_response.content.decode()


@pytest.mark.django_db
def test_get_logout_remains_rejected():
    user = create_account("get-logout-user")

    response = authenticated_client(user).get(reverse("accounts:logout"))

    assert response.status_code == 405


@pytest.mark.django_db
def test_logout_requires_csrf_token_when_csrf_checks_are_enabled():
    user = create_account("csrf-logout-user")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.post(reverse("accounts:logout"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_authenticated_login_and_setup_redirect_as_documented():
    admin = create_admin("redirect-admin")
    client = authenticated_client(admin)

    login_response = client.get(reverse("accounts:login"))
    setup_response = client.get(reverse("accounts:setup"))

    assert login_response.status_code == 302
    assert login_response.url == reverse("home")
    assert setup_response.status_code == 302
    assert setup_response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_forced_password_change_page_explains_requirement():
    user = create_account("guidance-user", must_change_password=True)

    response = authenticated_client(user).get(reverse("accounts:forced_password_change"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Your administrator requires you to choose a new password before continuing." in body
    assert "Enter and confirm a new password" in body
    assert "RidgeNote's password requirements" in body
    assert "you will continue into RidgeNote" in body


@pytest.mark.django_db
def test_user_create_boolean_fields_are_associated_with_labels():
    admin = create_admin("checkbox-admin")

    response = authenticated_client(admin).get(reverse("accounts:user_create"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'class="form-field form-field--checkbox"' in body
    assert 'class="checkbox-control"' in body
    assert 'type="checkbox" name="is_active"' in body
    assert 'id="id_is_active"' in body
    assert 'for="id_is_active"' in body
    assert "Active" in body


@pytest.mark.django_db
def test_user_create_has_require_password_change_control_for_set_password():
    """The Create User `require_password_change` checkbox is present
    (default checked), scoped to Set password: unconditional forced
    change would create a semantic contradiction with calling the
    password "temporary." `UserPasswordResetForm` keeps its own, separate
    control unaffected (see
    `test_admin_password_reset_view_changes_password_sets_flag_and_invalidates_session`)."""
    admin = create_admin("has-checkbox-admin")

    response = authenticated_client(admin).get(reverse("accounts:user_create"))
    body = response.content.decode()

    assert 'name="require_password_change"' in body
    assert "Require password change on first sign-in" in body
    assert 'id="id_require_password_change"' in body
    assert "checked" in body.split('id="id_require_password_change"')[1].split(">")[0]


@pytest.mark.django_db
def test_user_detail_uses_update_status_label_for_status_form():
    # The Account settings panel's three independent forms (Role,
    # Enabled, Email) visually read as one settings form, so a generic
    # "Save changes" button was misleading -- clicking it while an
    # unsaved Email edit was present silently discarded that edit, since
    # it only submits the Enabled form. Every button now explicitly
    # names its own setting.
    admin = create_admin("detail-admin")
    target = create_account("detail-user")

    response = authenticated_client(admin).get(reverse("accounts:user_detail", args=[target.pk]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Update Status" in body
    assert "Save changes" not in body


@pytest.mark.django_db
def test_initial_admin_setup_creates_admin_and_closes_bootstrap():
    response = Client().post(
        reverse("accounts:setup"),
        {
            "username": "  FirstAdmin  ",
            "display_name": "First Admin",
            "email": "",
            **password_payload(),
        },
    )

    assert response.status_code == 302
    admin = get_user_model().objects.get(username="firstadmin")
    assert admin.display_name == "First Admin"
    assert admin.role == User.ROLE_ADMIN
    assert admin.is_active is True
    assert admin.is_staff is False
    assert admin.is_superuser is False
    assert services.admin_exists() is True
    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INITIAL_ADMIN_CREATED).exists()

    response = Client().get(reverse("accounts:setup"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_post_setup_is_rejected_after_admin_exists():
    create_admin()

    response = Client().post(
        reverse("accounts:setup"),
        {"username": "second-admin", "email": "", **password_payload()},
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert get_user_model().objects.filter(role=User.ROLE_ADMIN).count() == 1
    assert not get_user_model().objects.filter(username="second-admin").exists()


@pytest.mark.django_db
def test_bootstrap_closed_when_only_admin_is_inactive():
    create_admin("disabled-admin", is_active=False)

    response = Client().get(reverse("accounts:setup"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_bootstrap_service_rechecks_after_serialization_lock(monkeypatch):
    form = SetupForm({"username": "racing-admin", "email": "", **password_payload()})
    calls = []
    assert form.is_valid()

    def fake_lock():
        calls.append("lock-acquired")

    def admin_exists_after_lock():
        calls.append("admin-exists-checked")
        return "lock-acquired" in calls

    monkeypatch.setattr(services, "acquire_admin_operation_lock", fake_lock)
    monkeypatch.setattr(services, "admin_exists", admin_exists_after_lock)

    with pytest.raises(services.BootstrapClosedError):
        services.create_initial_admin(form)

    assert calls == ["lock-acquired", "admin-exists-checked"]
    assert not get_user_model().objects.filter(username="racing-admin").exists()


@pytest.mark.django_db
def test_two_sequential_bootstrap_posts_cannot_create_two_initial_admins():
    client = Client()
    first = client.post(
        reverse("accounts:setup"),
        {"username": "first", "email": "", **password_payload()},
    )
    second = Client().post(
        reverse("accounts:setup"),
        {"username": "second", "email": "", **password_payload()},
    )

    assert first.status_code == 302
    assert second.status_code == 302
    assert get_user_model().objects.filter(role=User.ROLE_ADMIN).count() == 1
    assert not get_user_model().objects.filter(username="second").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("login_username", ["Case-User", "case-user", "CASE-USER", "  Case-User  "])
def test_successful_login_accepts_case_insensitive_username(login_username):
    create_admin()
    user = create_account("Case-User")

    response = Client().post(
        reverse("accounts:login"),
        {"username": login_username, "password": PASSWORD},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    session = response.wsgi_request.session
    assert session["_auth_user_id"] == str(user.pk)
    user.refresh_from_db()
    assert user.username == "case-user"
    assert user.display_name == "Case-User"


@pytest.mark.django_db
def test_direct_manager_creation_normalizes_username_and_preserves_display_name():
    user = get_user_model().objects.create_user(
        username="  Steve  ",
        password=PASSWORD,
        role=User.ROLE_USER,
        display_name="Steve",
    )

    assert user.username == "steve"
    assert user.display_name == "Steve"


@pytest.mark.django_db
def test_direct_model_save_normalizes_username_and_trims_display_name():
    user = User(username="  ModelUser  ", role=User.ROLE_USER, display_name="  Model User  ")
    user.set_password(PASSWORD)
    user.save()

    user.refresh_from_db()
    assert user.username == "modeluser"
    assert user.display_name == "Model User"


@pytest.mark.django_db
def test_successful_login_creates_authenticated_session_and_audit_event():
    create_admin()
    user = create_account("login-user")

    response = Client().post(
        reverse("accounts:login"),
        {"username": user.username, "password": PASSWORD},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    session = response.wsgi_request.session
    assert session["_auth_user_id"] == str(user.pk)
    assert session[services.SESSION_GENERATION_KEY] == user.session_generation
    assert services.LAST_ACTIVITY_KEY in session
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_LOGIN_SUCCESS,
        target_user=user,
    ).exists()


@pytest.mark.django_db
def test_case_insensitive_login_still_requires_the_correct_password():
    create_admin()
    user = create_account("PasswordCaseUser")

    response = Client().post(
        reverse("accounts:login"),
        {"username": "passwordcaseuser", "password": "incorrect"},
    )

    assert response.status_code == 200
    assert (
        "The username or password is incorrect, or the account cannot sign in."
        in response.content.decode()
    )
    user.refresh_from_db()
    assert user.failed_login_count == 1


@pytest.mark.django_db
def test_generic_login_failure_does_not_disclose_missing_inactive_or_bad_password():
    create_admin()
    create_account("dormant-user", is_active=False)
    create_account("wrong-password-user")
    cases = [
        {"username": "missing-user", "password": PASSWORD},
        {"username": "dormant-user", "password": PASSWORD},
        {"username": "wrong-password-user", "password": "incorrect"},
    ]

    for payload in cases:
        response = Client().post(reverse("accounts:login"), payload)
        body = response.content.decode()
        assert response.status_code == 200
        assert "The username or password is incorrect, or the account cannot sign in." in body
        assert "inactive" not in body.lower()
        assert "not found" not in body.lower()

    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LOGIN_FAILED).count() == 3


@pytest.mark.django_db
def test_unsafe_external_next_url_is_rejected_after_login():
    create_admin()
    user = create_account("next-user")

    response = Client().post(
        reverse("accounts:login"),
        {"username": user.username, "password": PASSWORD, "next": "https://evil.example/"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_logout_ends_session_and_creates_audit_event():
    user = create_account("logout-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:logout"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert client.get(reverse("accounts:session_status")).status_code == 401
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_LOGOUT,
        actor_username_snapshot=user.username,
    ).exists()


@pytest.mark.django_db
def test_failed_login_locks_account_without_extending_existing_lock(settings):
    settings.RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD = 2
    settings.RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS = 900
    settings.RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS = 900
    create_admin()
    user = create_account("locked-user")
    client = Client()

    for username in [user.username.upper(), user.username.lower()]:
        client.post(reverse("accounts:login"), {"username": username, "password": "wrong"})

    user.refresh_from_db()
    first_locked_until = user.locked_until
    assert first_locked_until is not None

    client.post(reverse("accounts:login"), {"username": user.username, "password": "wrong"})
    user.refresh_from_db()

    assert user.locked_until == first_locked_until
    assert (
        AuditEvent.objects.filter(
            target_user=user,
            event_type=AuditEvent.EVENT_ACCOUNT_LOCKED,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_automatic_unlock_after_locked_until_has_passed(settings):
    settings.RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD = 5
    create_admin()
    user = create_account(
        "expired-lock-user",
        failed_login_count=5,
        failed_login_window_started_at=timezone.now() - timedelta(minutes=30),
        locked_until=timezone.now() - timedelta(minutes=1),
    )

    response = Client().post(
        reverse("accounts:login"),
        {"username": user.username, "password": "still-wrong"},
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.locked_until is None
    assert user.failed_login_count == 1
    assert AuditEvent.objects.filter(
        target_user=user,
        event_type=AuditEvent.EVENT_ACCOUNT_UNLOCKED,
    ).exists()


@pytest.mark.django_db
def test_successful_login_clears_failed_attempt_state():
    create_admin()
    user = create_account(
        "clear-state-user",
        failed_login_count=3,
        failed_login_window_started_at=timezone.now() - timedelta(minutes=1),
    )

    response = Client().post(
        reverse("accounts:login"),
        {"username": user.username, "password": PASSWORD},
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.failed_login_count == 0
    assert user.failed_login_window_started_at is None
    assert user.locked_until is None
    assert user.last_successful_login_at is not None


@pytest.mark.django_db(transaction=True)
def test_failed_login_counter_updates_are_concurrency_safe(settings):
    settings.RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD = 50
    user = create_account("concurrent-user")
    errors = []

    def fail_login():
        close_old_connections()
        try:
            services.record_failed_login(user.username)
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)
        finally:
            close_old_connections()
            connections.close_all()

    threads = [Thread(target=fail_login) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    user.refresh_from_db()
    assert user.failed_login_count == 5


@pytest.mark.django_db
def test_anonymous_and_non_admin_users_cannot_access_admin_screens():
    user = create_account("plain-user")

    anonymous_response = Client().get(reverse("accounts:user_list"))
    non_admin_response = authenticated_client(user).get(reverse("accounts:user_list"))

    assert anonymous_response.status_code == 302
    assert reverse("accounts:login") in anonymous_response.url
    assert non_admin_response.status_code == 403


@pytest.mark.django_db
def test_setup_form_rejects_case_only_duplicate_username():
    create_admin("AdminCase")

    form = SetupForm(
        {
            "username": "  admincase  ",
            "display_name": "Admin Case",
            "email": "",
            **password_payload(),
        }
    )

    assert not form.is_valid()
    assert form.errors["username"] == ["A user with that username already exists."]


@pytest.mark.django_db
def test_admin_can_create_user():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        {
            "setup_method": "temporary_password",
            "username": "  Created-User  ",
            "display_name": "Created User",
            "email": "",
            "role": User.ROLE_USER,
            "is_active": "on",
            "require_password_change": "on",
            **password_payload(),
        },
        follow=True,
    )

    assert response.status_code == 200
    created = get_user_model().objects.get(username="created-user")
    assert "Created user Created User." in response.content.decode()
    assert created.display_name == "Created User"
    assert created.role == User.ROLE_USER
    assert created.is_active is True
    assert created.must_change_password is True
    assert created.is_staff is False
    assert created.is_superuser is False
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_USER_CREATED, target_user=created
    ).exists()


@pytest.mark.django_db
def test_admin_user_create_rejects_case_only_duplicate_username():
    admin = create_admin("creator-admin")
    create_account("Created-User")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        {
            "setup_method": "temporary_password",
            "username": "  created-user  ",
            "display_name": "Created User Duplicate",
            "email": "",
            "role": User.ROLE_USER,
            "is_active": "on",
            **password_payload(),
        },
    )

    assert response.status_code == 200
    assert response.context["form"].errors["username"] == [
        "A user with that username already exists."
    ]
    assert get_user_model().objects.filter(username__iexact="created-user").count() == 1


@pytest.mark.django_db
def test_role_values_are_limited_to_admin_and_user_in_user_creation():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        {
            "setup_method": "temporary_password",
            "username": "bad-role-user",
            "email": "",
            "role": "owner",
            "is_active": "on",
            **password_payload(),
        },
    )

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username="bad-role-user").exists()


@pytest.mark.django_db
def test_promote_and_demote_work_when_another_active_admin_remains():
    actor = create_admin("actor")
    other_admin = create_admin("other-admin")
    user = create_account("promoted-user")

    services.set_user_role(target=user, role=User.ROLE_ADMIN, actor=actor)
    services.set_user_role(target=other_admin, role=User.ROLE_USER, actor=actor)

    user.refresh_from_db()
    other_admin.refresh_from_db()
    assert user.role == User.ROLE_ADMIN
    assert other_admin.role == User.ROLE_USER
    assert services.active_admin_count() == 2


@pytest.mark.django_db
def test_disable_user_increments_generation_and_invalidates_existing_session():
    admin = create_admin()
    user = create_account("disabled-user")
    client = authenticated_client(user)

    services.set_user_active(target=user, active=False, actor=admin)

    user.refresh_from_db()
    assert user.is_active is False
    assert user.session_generation == 2
    assert user.disabled_at is not None
    assert client.get(reverse("accounts:session_status")).status_code in {302, 401}


@pytest.mark.django_db
def test_enabling_user_clears_disabled_at_without_incrementing_generation():
    admin = create_admin()
    disabled_at = timezone.now()
    user = create_account("enabled-user", is_active=False, disabled_at=disabled_at)

    services.set_user_active(target=user, active=True, actor=admin)

    user.refresh_from_db()
    assert user.is_active is True
    assert user.disabled_at is None
    assert user.session_generation == 1
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_USER_ENABLED, target_user=user
    ).exists()


@pytest.mark.django_db
def test_last_active_admin_cannot_self_demote_or_self_disable():
    admin = create_admin("solo-admin")

    with pytest.raises(services.LastActiveAdminError):
        services.set_user_role(target=admin, role=User.ROLE_USER, actor=admin)
    with pytest.raises(services.LastActiveAdminError):
        services.set_user_active(target=admin, active=False, actor=admin)

    admin.refresh_from_db()
    assert admin.role == User.ROLE_ADMIN
    assert admin.is_active is True


@pytest.mark.django_db
def test_admin_password_reset_view_changes_password_sets_flag_and_invalidates_session():
    admin = create_admin()
    target = create_account("reset-target")
    target_client = authenticated_client(target)
    admin_client = authenticated_client(admin)

    response = admin_client.post(
        reverse("accounts:user_password_reset", args=[target.pk]),
        {**password_payload(OTHER_PASSWORD), "require_password_change": "on"},
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert check_password(OTHER_PASSWORD, target.password)
    assert target.must_change_password is True
    assert target.session_generation == 2
    assert target_client.get(reverse("accounts:session_status")).status_code in {302, 401}
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMIN_PASSWORD_RESET,
        target_user=target,
    ).exists()


@pytest.mark.django_db
def test_existing_user_validation_allows_non_username_edits_without_effective_username_change():
    user = create_account(
        "CanonicalCase",
        email="before@example.com",
        display_name="Canonical Case",
    )

    user.email = "after@example.com"
    user.full_clean()
    user.save(update_fields=["email"])

    user.refresh_from_db()
    assert user.username == "canonicalcase"
    assert user.display_name == "Canonical Case"
    assert user.email == "after@example.com"


@pytest.mark.django_db
def test_password_entry_workflows_use_django_password_validation():
    create_admin()
    weak_payload = {"password1": "123", "password2": "123"}
    assert not SetupForm({"username": "weak-setup", "email": "", **weak_payload}).is_valid()

    admin = get_user_model().objects.get(username="admin")
    client = authenticated_client(admin)
    create_response = client.post(
        reverse("accounts:user_create"),
        {
            "setup_method": "temporary_password",
            "username": "weak-create",
            "role": User.ROLE_USER,
            "is_active": "on",
            **weak_payload,
        },
    )
    target = create_account("weak-reset")
    reset_response = client.post(
        reverse("accounts:user_password_reset", args=[target.pk]), weak_payload
    )
    forced_user = create_account("weak-forced", must_change_password=True)
    forced_response = authenticated_client(forced_user).post(
        reverse("accounts:forced_password_change"),
        weak_payload,
    )

    assert create_response.status_code == 200
    assert not get_user_model().objects.filter(username="weak-create").exists()
    assert reset_response.status_code == 200
    target.refresh_from_db()
    assert check_password(PASSWORD, target.password)
    assert forced_response.status_code == 200
    forced_user.refresh_from_db()
    assert forced_user.must_change_password is True


@pytest.mark.django_db
def test_forced_password_change_clears_flag_and_preserves_current_session():
    user = create_account("must-change", must_change_password=True)
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:forced_password_change"),
        password_payload(OTHER_PASSWORD),
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.must_change_password is False
    assert user.session_generation == 1
    assert check_password(OTHER_PASSWORD, user.password)
    assert client.get(reverse("accounts:session_status")).status_code == 200
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_FORCED_PASSWORD_CHANGE_COMPLETED,
        target_user=user,
        actor=user,
    ).exists()


@pytest.mark.django_db
@override_settings(RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS=60, RIDGENOTE_SESSION_WARNING_SECONDS=5)
def test_session_status_does_not_refresh_idle_activity():
    user = create_account("session-user")
    client = authenticated_client(user)
    session = client.session
    old_activity = (timezone.now() - timedelta(seconds=30)).timestamp()
    session[services.LAST_ACTIVITY_KEY] = old_activity
    session.save()

    response = client.get(reverse("accounts:session_status"))

    assert response.status_code == 200
    assert client.session[services.LAST_ACTIVITY_KEY] == old_activity


@pytest.mark.django_db
@pytest.mark.parametrize("static_url", ["static/", "/static/"])
def test_static_requests_do_not_refresh_idle_activity(settings, static_url):
    settings.STATIC_URL = static_url
    user = create_account(f"static-user-{static_url.startswith('/')}")
    client = authenticated_client(user)
    session = client.session
    old_activity = (timezone.now() - timedelta(seconds=30)).timestamp()
    session[services.LAST_ACTIVITY_KEY] = old_activity
    session.save()

    response = client.get("/static/core/dist/assets/app.css")

    assert response.status_code in {200, 301, 302, 404}
    assert client.session[services.LAST_ACTIVITY_KEY] == old_activity


@pytest.mark.django_db
@override_settings(RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS=60, RIDGENOTE_SESSION_WARNING_SECONDS=5)
def test_expired_session_logs_out_before_view():
    user = create_account("expired-user")
    client = authenticated_client(user)
    session = client.session
    session[services.LAST_ACTIVITY_KEY] = (timezone.now() - timedelta(seconds=120)).timestamp()
    session.save()

    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_cli_recovery_finds_admin_case_insensitively():
    admin = create_admin("RecoverAdmin", display_name="Recover Admin")
    stdout = StringIO()

    with patch(
        "accounts.management.commands.recover_admin_password.getpass", return_value=OTHER_PASSWORD
    ):
        call_command("recover_admin_password", "recoveradmin", stdout=stdout)

    admin.refresh_from_db()
    assert check_password(OTHER_PASSWORD, admin.password)
    assert "Administrator password reset" in stdout.getvalue()


@pytest.mark.django_db
def test_cli_recovery_rejects_missing_account_and_creates_no_account():
    with patch(
        "accounts.management.commands.recover_admin_password.getpass", return_value=OTHER_PASSWORD
    ):
        with pytest.raises(CommandError, match="No existing administrator"):
            call_command("recover_admin_password", "missing-admin")

    assert get_user_model().objects.count() == 0


@pytest.mark.django_db
def test_cli_recovery_rejects_normal_user_without_promotion():
    user = create_account("normal-user")

    with patch(
        "accounts.management.commands.recover_admin_password.getpass", return_value=OTHER_PASSWORD
    ):
        with pytest.raises(CommandError, match="only an existing administrator"):
            call_command("recover_admin_password", user.username)

    user.refresh_from_db()
    assert user.role == User.ROLE_USER


@pytest.mark.django_db
def test_cli_recovery_rejects_mismatched_confirmation():
    admin = create_admin()

    with patch(
        "accounts.management.commands.recover_admin_password.getpass",
        side_effect=[OTHER_PASSWORD, "DifferentLongPassword123!"],
    ):
        with pytest.raises(CommandError, match="Passwords did not match"):
            call_command("recover_admin_password", admin.username)

    admin.refresh_from_db()
    assert check_password(PASSWORD, admin.password)


@pytest.mark.django_db
def test_cli_recovery_rejects_invalid_password_with_django_validation():
    admin = create_admin()

    with patch("accounts.management.commands.recover_admin_password.getpass", return_value="123"):
        with pytest.raises(ValidationError):
            call_command("recover_admin_password", admin.username)

    admin.refresh_from_db()
    assert check_password(PASSWORD, admin.password)


@pytest.mark.django_db
def test_cli_recovery_changes_password_increments_generation_and_audits():
    admin = create_admin()
    stdout = StringIO()

    with patch(
        "accounts.management.commands.recover_admin_password.getpass", return_value=OTHER_PASSWORD
    ):
        call_command("recover_admin_password", admin.username, stdout=stdout)

    admin.refresh_from_db()
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_CLI_ADMIN_PASSWORD_RECOVERED)
    assert check_password(OTHER_PASSWORD, admin.password)
    assert admin.session_generation == 2
    assert event.actor is None
    assert event.source == AuditEvent.SOURCE_MANAGEMENT_COMMAND
    assert event.target_user == admin
    assert event.target_username_snapshot == admin.username
    assert "Administrator password reset" in stdout.getvalue()


@pytest.mark.django_db
def test_audit_actor_and_target_set_null_preserves_username_snapshots_after_delete():
    actor = create_admin("audit-actor")
    target = create_account("audit-target")
    event = services.record_audit_event(
        AuditEvent.EVENT_USER_DISABLED,
        actor=actor,
        target_user=target,
    )

    actor.delete()
    target.delete()
    event.refresh_from_db()

    assert event.actor is None
    assert event.target_user is None
    assert event.actor_username_snapshot == "audit-actor"
    assert event.target_username_snapshot == "audit-target"


@pytest.mark.django_db
def test_audit_details_drop_sensitive_fields():
    event = services.record_audit_event(
        AuditEvent.EVENT_LOGIN_FAILED,
        details={
            "password": "plain",
            "token": "token-value",
            "hash": "hash-value",
            "reason": "bad-password",
            "nested": {"secret": "nested-secret", "kept": "safe"},
        },
    )

    assert event.details == {"reason": "bad-password", "nested": {"kept": "safe"}}
    assert "plain" not in str(event.details)
    assert "token-value" not in str(event.details)
    assert "hash-value" not in str(event.details)
    assert "nested-secret" not in str(event.details)


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD", "0"),
        ("RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS", "not-an-int"),
        ("RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS", "-1"),
        ("RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS", "0"),
        ("RIDGENOTE_SESSION_WARNING_SECONDS", "0"),
    ],
)
def test_invalid_positive_integer_environment_values_fail_validation(env_name, env_value):
    env = os.environ.copy()
    env[env_name] = env_value

    result = subprocess.run(
        [sys.executable, "-c", "import ridgenote.settings"],
        cwd=django_settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert env_name in result.stderr


@pytest.mark.parametrize("warning_seconds", ["60", "120"])
def test_session_warning_must_be_less_than_idle_timeout(warning_seconds):
    env = os.environ.copy()
    env["RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS"] = "60"
    env["RIDGENOTE_SESSION_WARNING_SECONDS"] = warning_seconds

    result = subprocess.run(
        [sys.executable, "-c", "import ridgenote.settings"],
        cwd=django_settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "RIDGENOTE_SESSION_WARNING_SECONDS" in result.stderr


@pytest.mark.django_db
def test_get_user_model_resolves_to_accounts_user_model_and_table():
    UserModel = get_user_model()

    assert UserModel is User
    assert UserModel._meta.label == "accounts.User"
    assert UserModel._meta.db_table == "accounts_user"


@pytest.mark.django_db
def test_ridgenote_authorization_does_not_depend_on_staff_superuser_or_groups():
    group = Group.objects.create(name="admins-in-name-only")
    user = create_account("staff-user", is_staff=True, is_superuser=True)
    user.groups.add(group)

    response = authenticated_client(user).get(reverse("accounts:user_list"))

    assert response.status_code == 403
