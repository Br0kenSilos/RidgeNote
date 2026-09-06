"""Dual-Mode Admin Create User + Invitation
Management UI.

Covers `UserCreateForm` (setup_method), the
`create_invited_user()` service and its atomicity with
`issue_user_invitation()`, the temporary-password
`create_user()` path, the `reset_user_password()` setup-incomplete
guard, the derived `account_status_label`/`account_status_detail`
properties, `latest_invitation_for_user()`, the one-time raw-link
response (Create User invitation-mode success and Reissue success both
render `user_detail.html` directly), and the Reissue/Revoke views.
No SMTP delivery work.
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.forms import SETUP_METHOD_INVITATION, SETUP_METHOD_SET_PASSWORD, UserCreateForm
from accounts.models import AuditEvent, User, UserInvitation

PASSWORD = "LongUniquePassword123!"
NEW_PASSWORD = "AnotherLongPassword456!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


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


def create_setup_incomplete_target(username="invitee", **kwargs):
    kwargs["setup_completed_at"] = None
    return create_account(username, **kwargs)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def invitation_payload(username="new-invited-user", **overrides):
    payload = {
        "setup_method": SETUP_METHOD_INVITATION,
        "username": username,
        "display_name": username,
        "email": "",
        "role": User.ROLE_USER,
        "is_active": "on",
    }
    payload.update(overrides)
    return payload


def temporary_password_payload(username="new-temp-user", **overrides):
    payload = {
        "setup_method": SETUP_METHOD_SET_PASSWORD,
        "username": username,
        "display_name": username,
        "email": "",
        "role": User.ROLE_USER,
        "is_active": "on",
        "password1": NEW_PASSWORD,
        "password2": NEW_PASSWORD,
        # Mirrors the widget's own `initial=True` (checked-by-default)
        # state -- a real browser submitting the as-rendered form sends
        # this key. Pass `require_password_change=False` to model an
        # administrator unchecking it.
        "require_password_change": "on",
    }
    payload.update(overrides)
    return payload


def extract_invitation_url(html: str) -> str | None:
    match = re.search(r'value="(http://[^"]*/invite/[^"]+/)"', html)
    return match.group(1) if match else None


# -- Form semantics -----------------------------------------------------


@pytest.mark.django_db
def test_form_default_setup_method_is_invitation():
    form = UserCreateForm()
    assert form.fields["setup_method"].initial == SETUP_METHOD_INVITATION


@pytest.mark.django_db
def test_invitation_mode_does_not_require_password_fields():
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_temporary_password_mode_requires_password():
    form = UserCreateForm(data=temporary_password_payload(password1="", password2=""))
    assert not form.is_valid()


@pytest.mark.django_db
def test_temporary_password_mismatch_rejected():
    form = UserCreateForm(data=temporary_password_payload(password2="SomethingElse123!"))
    assert not form.is_valid()
    assert "did not match" in str(form.errors)


@pytest.mark.django_db
def test_invalid_setup_method_rejected():
    form = UserCreateForm(data=invitation_payload(setup_method="not-a-real-method"))
    assert not form.is_valid()
    assert "setup_method" in form.errors


@pytest.mark.django_db
def test_invitation_mode_nonblank_password_rejected():
    form = UserCreateForm(data=invitation_payload(password1="SomePassword123!"))
    assert not form.is_valid()
    assert "not used for invitation" in str(form.errors).lower()


@pytest.mark.django_db
def test_no_js_post_semantics_invitation_mode():
    """A no-JS browser submits every field the server-rendered form
    contains, including the blank password fields, for whichever radio
    button was selected -- this must validate cleanly."""
    admin = create_admin()
    client = authenticated_client(admin)
    payload = invitation_payload()
    payload["password1"] = ""
    payload["password2"] = ""

    response = client.post(reverse("accounts:user_create"), payload)

    assert response.status_code == 200
    assert User.objects.filter(username="new-invited-user").exists()


# -- must_change_password semantics -----------------------------------------
#
# Calling an administrator-chosen password "temporary" while forcing an
# unconditional first-login change would create a semantic contradiction if
# the change requirement isn't actually meant to be optional. The current
# model uses `require_password_change`, scoped to Set
# password only, defaulting checked (True). Invitation does not have this
# field and does not use it.


@pytest.mark.django_db
def test_set_password_default_checked_produces_must_change_password_true():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_create"), temporary_password_payload(username="temp-user"))

    assert User.objects.get(username="temp-user").must_change_password is True


@pytest.mark.django_db
def test_set_password_unchecked_produces_must_change_password_false():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        temporary_password_payload(username="temp-user-unchecked", require_password_change=False),
    )

    assert User.objects.get(username="temp-user-unchecked").must_change_password is False


@pytest.mark.django_db
def test_invitation_mode_always_produces_must_change_password_false():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_create"), invitation_payload(username="invited-user-mcp"))

    user = User.objects.get(username="invited-user-mcp")
    assert user.must_change_password is False


@pytest.mark.django_db
def test_invitation_mode_ignores_submitted_require_password_change():
    """A stale/scripted client submitting `require_password_change=on`
    alongside Invitation mode must not influence the outcome --
    `create_invited_user()` sets `must_change_password=False`
    unconditionally, regardless of what else was posted."""
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="invited-user-stale-rpc", require_password_change="on"),
    )

    user = User.objects.get(username="invited-user-stale-rpc")
    assert user.must_change_password is False


# -- Invitation-mode creation ---------------------------------------------


@pytest.mark.django_db
def test_invitation_mode_create_admin_succeeds():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    assert result.user.username == "new-invited-user"


@pytest.mark.django_db
def test_invitation_mode_create_ordinary_user_forbidden():
    ordinary = create_account("ordinary")
    client = authenticated_client(ordinary)

    response = client.post(reverse("accounts:user_create"), invitation_payload())

    assert response.status_code == 403
    assert not User.objects.filter(username="new-invited-user").exists()


@pytest.mark.django_db
def test_invitation_mode_creates_setup_incomplete_user_with_unusable_password():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    assert result.user.setup_completed_at is None
    assert result.user.must_change_password is False
    assert result.user.has_usable_password() is False


@pytest.mark.django_db
def test_invitation_mode_issues_initial_invitation_with_raw_token_returned_once():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    assert result.invitation.user_id == result.user.pk
    assert result.raw_token
    assert result.invitation.token_hash != result.raw_token
    assert UserInvitation.objects.filter(user=result.user).count() == 1


@pytest.mark.django_db
def test_invitation_mode_raw_token_not_persisted_session_or_audit():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    for event in AuditEvent.objects.filter(target_user=result.user):
        assert result.raw_token not in str(event.details)


@pytest.mark.django_db
def test_invitation_mode_records_separate_created_and_issued_audits():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    assert (
        AuditEvent.objects.filter(
            event_type=AuditEvent.EVENT_USER_CREATED, target_user=result.user
        ).count()
        == 1
    )
    assert (
        AuditEvent.objects.filter(
            event_type=AuditEvent.EVENT_INVITATION_ISSUED, target_user=result.user
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_invitation_mode_blank_email_supported():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload(email=""))
    assert form.is_valid(), form.errors

    result = services.create_invited_user(form=form, actor=admin)

    assert result.user.email == ""


@pytest.mark.django_db
def test_invitation_mode_login_gate_blocks_before_acceptance():
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors
    result = services.create_invited_user(form=form, actor=admin)

    client = Client()
    response = client.post(
        reverse("accounts:login"),
        {"username": result.user.username, "password": "anything-at-all"},
    )
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_invitation_issuance_failure_rolls_back_user_creation(monkeypatch):
    admin = create_admin()
    form = UserCreateForm(data=invitation_payload())
    assert form.is_valid(), form.errors

    def boom(*, target, actor, request=None):
        raise services.InvitationActorNotAuthorizedError("simulated failure")

    monkeypatch.setattr(services, "issue_user_invitation", boom)

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.create_invited_user(form=form, actor=admin)

    assert not User.objects.filter(username="new-invited-user").exists()
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_USER_CREATED, target_username_snapshot="new-invited-user"
    ).exists()


# -- Temporary-password creation ------------------------------------------


@pytest.mark.django_db
def test_temporary_password_setup_complete_immediately():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors

    user = services.create_user(form=form, actor=admin)

    assert user.setup_completed_at is not None


@pytest.mark.django_db
def test_temporary_password_usable_hashed_password():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors

    user = services.create_user(form=form, actor=admin)

    assert user.has_usable_password() is True
    assert check_password(NEW_PASSWORD, user.password)


@pytest.mark.django_db
def test_temporary_password_must_change_password_true_by_default():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors

    user = services.create_user(form=form, actor=admin)

    assert user.must_change_password is True


@pytest.mark.django_db
def test_temporary_password_creates_no_invitation_row():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors

    user = services.create_user(form=form, actor=admin)

    assert not UserInvitation.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_temporary_password_existing_user_created_audit_preserved():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors

    user = services.create_user(form=form, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_USER_CREATED, target_user=user)
    assert event.actor_id == admin.pk


@pytest.mark.django_db
def test_temporary_password_first_login_enters_forced_password_change():
    admin = create_admin()
    form = UserCreateForm(data=temporary_password_payload())
    assert form.is_valid(), form.errors
    user = services.create_user(form=form, actor=admin)

    client = Client()
    response = client.post(
        reverse("accounts:login"),
        {"username": user.username, "password": NEW_PASSWORD},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:forced_password_change")


# -- Reset Password guard --------------------------------------------------


@pytest.mark.django_db
def test_reset_password_action_hidden_for_setup_incomplete_user():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))

    assert "Reset password" not in response.content.decode()


@pytest.mark.django_db
def test_reset_password_service_rejects_setup_incomplete_target():
    admin = create_admin()
    target = create_setup_incomplete_target()
    original_password = target.password

    with pytest.raises(services.TargetSetupIncompleteError):
        services.reset_user_password(
            target=target,
            new_password=NEW_PASSWORD,
            must_change_password=True,
            actor=admin,
        )

    target.refresh_from_db()
    assert target.password == original_password


@pytest.mark.django_db
def test_reset_password_direct_post_cannot_bypass_guard():
    admin = create_admin()
    target = create_setup_incomplete_target()
    original_password_hash = target.password
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_password_reset", kwargs={"user_id": target.pk}),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "require_password_change": "on"},
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.password == original_password_hash
    assert not check_password(NEW_PASSWORD, target.password)


@pytest.mark.django_db
def test_reset_password_setup_complete_behavior_unchanged():
    admin = create_admin()
    target = create_account("resettable")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_password_reset", kwargs={"user_id": target.pk}),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "require_password_change": "on"},
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert check_password(NEW_PASSWORD, target.password)
    assert target.must_change_password is True


# -- Account status ---------------------------------------------------------


@pytest.mark.django_db
def test_status_active_setup_incomplete_is_invited():
    user = create_setup_incomplete_target(is_active=True)
    assert user.account_status_label == "Invited"
    assert user.account_status_detail == ""


@pytest.mark.django_db
def test_status_active_setup_complete_is_active():
    user = create_account("active-complete")
    assert user.account_status_label == "Active"


@pytest.mark.django_db
def test_status_disabled_setup_complete_is_disabled():
    user = create_account("disabled-complete", is_active=False)
    assert user.account_status_label == "Disabled"
    assert user.account_status_detail == ""


@pytest.mark.django_db
def test_status_disabled_setup_incomplete_shows_onboarding_context():
    user = create_setup_incomplete_target(is_active=False)
    assert user.account_status_label == "Disabled"
    assert user.account_status_detail != ""


@pytest.mark.django_db
def test_status_expired_invitation_does_not_become_active():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    target.refresh_from_db()
    assert target.account_status_label == "Invited"


@pytest.mark.django_db
def test_status_revoked_invitation_does_not_become_active():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)
    target.refresh_from_db()
    assert target.account_status_label == "Invited"


# -- Persistent invitation metadata ----------------------------------------


@pytest.mark.django_db
def test_latest_invitation_for_user_none_when_not_issued():
    target = create_setup_incomplete_target()
    assert services.latest_invitation_for_user(target) is None


@pytest.mark.django_db
def test_latest_invitation_for_user_returns_newest_after_reissue():
    admin = create_admin()
    target = create_setup_incomplete_target()
    first = services.issue_user_invitation(target=target, actor=admin)
    second = services.issue_user_invitation(target=target, actor=admin)

    latest = services.latest_invitation_for_user(target)

    assert latest.pk == second.invitation.pk
    assert latest.pk != first.invitation.pk


@pytest.mark.django_db
def test_detail_page_shows_issued_timestamp_without_raw_token():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Not issued" not in body
    assert issued.raw_token not in body


@pytest.mark.django_db
def test_detail_page_shows_live_expiry():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Expires" in body
    assert "Reissue invitation" in body
    assert "Revoke invitation" in body


@pytest.mark.django_db
def test_detail_page_shows_expired_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Expired" in body
    assert "Reissue invitation" in body
    assert "Revoke invitation" not in body


@pytest.mark.django_db
def test_detail_page_shows_revoked_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Revoked" in body
    assert "Reissue invitation" in body
    assert "Revoke invitation" not in body


@pytest.mark.django_db
def test_detail_page_shows_not_issued_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Not issued" in body
    assert "Issue invitation" in body


@pytest.mark.django_db
def test_detail_page_hides_invitation_section_for_setup_complete_target():
    admin = create_admin()
    target = create_account("already-set-up")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert 'id="invitation-heading"' not in body


@pytest.mark.django_db
def test_superseded_historical_row_does_not_confuse_current_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Expires" in body
    assert body.count('id="invitation-heading"') == 1


# -- One-time raw link -----------------------------------------------------


@pytest.mark.django_db
def test_create_post_response_contains_raw_url_once():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_create"), invitation_payload())
    body = response.content.decode()

    assert response.status_code == 200
    url = extract_invitation_url(body)
    assert url is not None
    assert "/invite/" in url


@pytest.mark.django_db
def test_normal_detail_get_after_create_does_not_contain_raw_url():
    admin = create_admin()
    client = authenticated_client(admin)
    client.post(reverse("accounts:user_create"), invitation_payload())
    user = User.objects.get(username="new-invited-user")

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": user.pk}))
    body = response.content.decode()

    assert extract_invitation_url(body) is None


@pytest.mark.django_db
def test_reissue_post_response_contains_new_raw_url_once():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert extract_invitation_url(body) is not None


@pytest.mark.django_db
def test_subsequent_detail_get_after_reissue_does_not_contain_raw_url():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)
    client.post(reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk}))

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    assert extract_invitation_url(response.content.decode()) is None


@pytest.mark.django_db
def test_generated_url_uses_current_request_origin():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"), invitation_payload(), SERVER_NAME="testserver"
    )
    url = extract_invitation_url(response.content.decode())

    assert url.startswith("http://testserver/invite/")


@pytest.mark.django_db
def test_raw_token_absent_from_session_messages_query_audit():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_create"), invitation_payload())
    url = extract_invitation_url(response.content.decode())
    raw_token = url.rsplit("/invite/", 1)[1].rstrip("/")

    session_values = [str(v) for v in dict(client.session).values()]
    assert not any(raw_token in v for v in session_values)
    for event in AuditEvent.objects.all():
        assert raw_token not in str(event.details)


# -- Reissue -----------------------------------------------------------


@pytest.mark.django_db
def test_reissue_admin_only():
    ordinary = create_account("ordinary-reissuer")
    target = create_setup_incomplete_target()
    client = authenticated_client(ordinary)

    response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_reissue_get_cannot_mutate():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.get(reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk}))

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None


@pytest.mark.django_db
def test_reissue_invalidates_previous_and_issues_fresh_token():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk}))

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is not None
    assert UserInvitation.objects.filter(user=target).count() == 2


@pytest.mark.django_db
def test_repeated_reissue_changes_token_each_time():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    first_response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )
    second_response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )

    first_url = extract_invitation_url(first_response.content.decode())
    second_url = extract_invitation_url(second_response.content.decode())
    assert first_url != second_url
    assert UserInvitation.objects.filter(user=target).count() == 2


@pytest.mark.django_db
def test_reissue_setup_complete_target_rejected():
    admin = create_admin()
    target = create_account("already-complete-target")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 302
    assert not UserInvitation.objects.filter(user=target).exists()


@pytest.mark.django_db
def test_reissue_confirmation_renders_before_mutation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    response = client.get(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 200
    assert not UserInvitation.objects.filter(user=target).exists()


@pytest.mark.django_db
def test_reissue_records_correct_audit():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk}))

    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_INVITATION_ISSUED, target_user=target
    ).exists()


@pytest.mark.django_db
def test_reissue_requires_csrf():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = admin.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.post(
        reverse("accounts:user_invitation_reissue", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 403
    assert not UserInvitation.objects.filter(user=target).exists()


# -- Revoke --------------------------------------------------------------


@pytest.mark.django_db
def test_revoke_admin_only():
    ordinary = create_account("ordinary-revoker")
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(ordinary)

    response = client.post(
        reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_revoke_get_cannot_mutate():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.get(reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk}))

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None


@pytest.mark.django_db
def test_revoke_live_invitation_succeeds():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 302
    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is not None


@pytest.mark.django_db
def test_revoke_does_not_change_account_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk}))

    target.refresh_from_db()
    assert target.is_active is True
    assert target.setup_completed_at is None


@pytest.mark.django_db
def test_revoke_not_offered_for_expired_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 302
    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None


@pytest.mark.django_db
def test_revoke_setup_complete_target_rejected():
    admin = create_admin()
    target = create_account("revoke-complete-target")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_revoke_records_correct_audit():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk}))

    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_INVITATION_REVOKED, target_user=target
    ).exists()


@pytest.mark.django_db
def test_revoke_requires_csrf():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = admin.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.post(
        reverse("accounts:user_invitation_revoke", kwargs={"user_id": target.pk})
    )

    assert response.status_code == 403
    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None


# -- Disable / re-enable interaction ---------------------------------------


@pytest.mark.django_db
def test_disabling_invited_account_does_not_revoke_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    services.set_user_active(target=target, active=False, actor=admin)

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None
    assert issued.invitation.is_valid is True


@pytest.mark.django_db
def test_disabled_target_cannot_accept_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    services.set_user_active(target=target, active=False, actor=admin)

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


@pytest.mark.django_db
def test_reenable_before_expiry_restores_usability():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    services.set_user_active(target=target, active=False, actor=admin)
    services.set_user_active(target=target, active=True, actor=admin)

    user = services.accept_user_invitation(
        invitation_id=issued.invitation.pk, password=NEW_PASSWORD
    )
    assert user.setup_completed_at is not None


@pytest.mark.django_db
def test_expired_invitation_still_requires_reissue_after_reenable():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    services.set_user_active(target=target, active=False, actor=admin)
    services.set_user_active(target=target, active=True, actor=admin)

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


# -- Setup-method field markup -----------------------------------------------


def _setup_method_field_markup(body: str) -> str:
    # Scopes the markup assertion
    # to the setup_method field's own rendered `<div>` rather than the
    # whole response body, since the site-wide Help panel (embedded on
    # every page) legitimately contains its own `<ul>` list markup
    # elsewhere in the same document.
    class_start = body.index('class="form-field form-field--radio-inline"')
    div_start = body.rindex("<div", 0, class_start)
    next_field = body.find('<div class="form-field', div_start + 1)
    div_end = next_field if next_field != -1 else body.index("</form>", div_start)
    return body[div_start:div_end]


@pytest.mark.django_db
def test_create_user_setup_method_renders_compact_inline_radio_markup():
    """The setup_method RadioSelect field renders through the
    `_form_field.html` "radio" branch (compact inline layout), not
    Django's default stacked `<ul>`/`<li>` radio markup."""
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))
    body = response.content.decode()
    field_markup = _setup_method_field_markup(body)

    assert 'class="form-field form-field--radio-inline"' in field_markup
    assert 'class="radio-inline-group"' in field_markup
    assert '<label class="radio-inline-group__option">' in field_markup
    assert 'id="id_setup_method_0"' in field_markup
    assert 'id="id_setup_method_1"' in field_markup
    # No leftover default Django RadioSelect list markup for this field.
    assert "<ul>" not in field_markup


@pytest.mark.django_db
def test_create_user_setup_method_labels_are_invitation_and_set_password():
    """The password-based path is labeled
    "Set password", not "Temporary password" -- the checkbox below lets
    the administrator decide whether that password must be changed on
    first sign-in, so calling it inherently "temporary" was a semantic
    contradiction."""
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))
    body = response.content.decode()

    assert "Invitation" in body
    assert "Set password" in body
    assert "Temporary password" not in body


@pytest.mark.django_db
def test_create_user_password_rows_carry_progressive_enhancement_hook():
    """Password1/password2 and the Require password change checkbox rows
    all carry `data-password-field-row` for `setup-method-toggle.ts` to
    hide/show together, and the form carries `data-setup-method-form`
    for it to find. Server-side validation is unaffected -- all three
    fields remain plain, always-present form fields."""
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))
    body = response.content.decode()

    assert "data-setup-method-form" in body
    assert body.count("data-password-field-row") == 3
    assert 'name="password1"' in body
    assert 'name="password2"' in body
    assert 'name="require_password_change"' in body


@pytest.mark.django_db
def test_detail_page_invitation_metadata_uses_compact_wording():
    """The redundant `Invitation:` prefix
    is dropped since the metadata line sits inside an already-titled
    Invitation section; timestamp semantics are unaffected."""
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert 'class="invitation-meta"' in body
    assert "Issued" in body
    assert "Expires" in body


@pytest.mark.django_db
def test_detail_page_state_appropriate_actions_still_render_after_correction():
    """The UI density/grouping correction must not change which actions
    are offered for which invitation state."""
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Reissue invitation" in body
    assert "Revoke invitation" in body
    assert 'class="field-with-action"' in body


@pytest.mark.django_db
def test_reissue_confirm_page_uses_compact_panel_modifier():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_invitation_reissue", args=[target.pk]))
    body = response.content.decode()

    assert 'class="panel panel--confirm-compact"' in body
    assert "Reissue invitation" in body
    assert "Cancel" in body


@pytest.mark.django_db
def test_revoke_confirm_page_uses_compact_panel_modifier_and_danger_button():
    admin = create_admin()
    target = create_setup_incomplete_target()
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_invitation_revoke", args=[target.pk]))
    body = response.content.decode()

    assert 'class="panel panel--confirm-compact"' in body
    assert 'class="button-link--danger"' in body
    assert "Cancel" in body


@pytest.mark.django_db
def test_invitation_copy_control_still_present_after_density_correction():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_create"), invitation_payload())
    body = response.content.decode()

    assert "data-copy-invitation-trigger" in body
    assert "data-copy-invitation-source" in body


# -- Set password + require_password_change ---------------------------------


@pytest.mark.django_db
def test_set_password_no_js_omitted_checkbox_key_means_unchecked():
    """No-JS fallback: a real browser's unchecked checkbox omits the key
    entirely from the POST body (not `require_password_change=off`).
    Server-side validation must treat that exactly like an explicit
    uncheck, not the checked default."""
    admin = create_admin()
    client = authenticated_client(admin)
    payload = temporary_password_payload(username="no-js-unchecked-user")
    del payload["require_password_change"]

    client.post(reverse("accounts:user_create"), payload)

    user = User.objects.get(username="no-js-unchecked-user")
    assert user.must_change_password is False


@pytest.mark.django_db
def test_set_password_no_js_present_checkbox_key_means_checked():
    """No-JS fallback: a real browser's checked checkbox (the default
    rendered state) submits `require_password_change=on`."""
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        temporary_password_payload(username="no-js-checked-user"),
    )

    user = User.objects.get(username="no-js-checked-user")
    assert user.must_change_password is True


@pytest.mark.django_db
def test_detail_page_hides_password_change_required_for_setup_incomplete_target():
    admin = create_admin()
    target = create_setup_incomplete_target()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Password change required" not in body


@pytest.mark.django_db
def test_detail_page_shows_password_change_required_for_setup_complete_target():
    admin = create_admin()
    target = create_account("setup-complete-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert "Password change required" in body


@pytest.mark.django_db
def test_detail_page_account_settings_and_actions_sections_render():
    admin = create_admin()
    target = create_account("grouping-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert 'id="account-settings-heading"' in body
    assert 'id="account-actions-heading"' in body
    assert "Update Role" in body
    assert "Update Status" in body
    assert "Update Email" in body
    assert "Schedule deletion" in body


@pytest.mark.django_db
def test_detail_page_deletion_pending_actions_still_render_in_account_actions():
    admin = create_admin()
    target = create_account("pending-deletion-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))
    body = response.content.decode()

    assert 'id="account-actions-heading"' in body
    assert "Cancel deletion" in body
