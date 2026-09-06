"""Admin Edit User Email.

An administrator can create a User with a mistyped email address, with
no way to correct it afterward. This adds a narrow, admin-only
email-correction control to `user_detail.html`'s existing "Account
settings" panel, reusing the existing `normalize_email()`/
`email_conflicts()` infrastructure (see `test_email_normalization.py`)
and the existing DB canonical/uniqueness constraints. It is
deliberately inert with respect to invitations, setup state, active
state, deletion state, role, password, and sessions.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import mail, services
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
EXTERNAL_URL = "https://ridgenote.example.test"

SMTP_CONFIGURED = override_settings(
    RIDGENOTE_SMTP_CONFIGURED=True,
    RIDGENOTE_EXTERNAL_URL=EXTERNAL_URL,
)


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


def email_url(target):
    return reverse("accounts:user_email", args=[target.pk])


# -- Service layer ------------------------------------------------------


@pytest.mark.django_db
def test_set_user_email_changes_value_and_records_audit():
    admin = create_admin("service-admin")
    target = create_account("service-target", email="old@example.test")

    services.set_user_email(target=target, email="new@example.test", actor=admin)

    target.refresh_from_db()
    assert target.email == "new@example.test"
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_EMAIL_CHANGED)
    assert event.actor_id == admin.pk
    assert event.target_user_id == target.pk
    assert event.details["old_email"] == "old@example.test"
    assert event.details["new_email"] == "new@example.test"


@pytest.mark.django_db
def test_set_user_email_normalizes_whitespace_and_case():
    admin = create_admin("normalize-admin")
    target = create_account("normalize-target", email="old@example.test")

    services.set_user_email(target=target, email="  New@Example.TEST  ", actor=admin)

    target.refresh_from_db()
    assert target.email == "new@example.test"


@pytest.mark.django_db
def test_set_user_email_no_op_for_unchanged_normalized_value_records_no_audit():
    admin = create_admin("noop-admin")
    target = create_account("noop-target", email="same@example.test")

    services.set_user_email(target=target, email="  Same@Example.TEST ", actor=admin)

    target.refresh_from_db()
    assert target.email == "same@example.test"
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_EMAIL_CHANGED).exists()


@pytest.mark.django_db
def test_set_user_email_blank_is_accepted():
    admin = create_admin("blank-admin")
    target = create_account("blank-target", email="old@example.test")

    services.set_user_email(target=target, email="", actor=admin)

    target.refresh_from_db()
    assert target.email == ""
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_EMAIL_CHANGED)
    assert event.details["new_email"] == ""


# -- Lifecycle coverage: Invited / Active / Disabled / Pending deletion --


@pytest.mark.django_db
def test_admin_can_edit_invited_user_email():
    admin = create_admin("invited-editor-admin")
    target = create_setup_incomplete_target("invited-target", email="old@example.test")
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "corrected@example.test"})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == "corrected@example.test"


@pytest.mark.django_db
def test_admin_can_edit_active_user_email():
    admin = create_admin("active-editor-admin")
    target = create_account("active-target", email="old@example.test")
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "corrected@example.test"})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == "corrected@example.test"


@pytest.mark.django_db
def test_admin_can_edit_disabled_user_email():
    admin = create_admin("disabled-editor-admin")
    target = create_account("disabled-target", email="old@example.test", is_active=False)
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "corrected@example.test"})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == "corrected@example.test"


@pytest.mark.django_db
def test_admin_can_edit_pending_deletion_user_email():
    admin = create_admin("pending-editor-admin")
    target = create_account("pending-target", email="old@example.test")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "corrected@example.test"})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == "corrected@example.test"
    assert target.deletion_scheduled_at is not None


# -- Permissions ----------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_redirected_to_login():
    target = create_account("anon-target", email="old@example.test")

    response = Client().post(email_url(target), {"email": "new@example.test"})

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
    target.refresh_from_db()
    assert target.email == "old@example.test"


@pytest.mark.django_db
def test_non_admin_forbidden():
    non_admin = create_account("non-admin-actor")
    target = create_account("forbidden-target", email="old@example.test")
    client = authenticated_client(non_admin)

    response = client.post(email_url(target), {"email": "new@example.test"})

    assert response.status_code == 403
    target.refresh_from_db()
    assert target.email == "old@example.test"


# -- Duplicate / validation -------------------------------------------------


@pytest.mark.django_db
def test_duplicate_canonical_email_rejected_with_field_adjacent_error():
    admin = create_admin("dup-admin")
    create_account("dup-existing", email="taken@example.test")
    target = create_account("dup-target", email="old@example.test")
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "  Taken@Example.TEST "})

    assert response.status_code == 200
    content = response.content.decode()
    assert "A user with that email already exists." in content
    target.refresh_from_db()
    assert target.email == "old@example.test"
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_EMAIL_CHANGED).exists()


@pytest.mark.django_db
def test_unchanged_self_email_via_view_succeeds_with_no_audit():
    admin = create_admin("self-admin")
    target = create_account("self-target", email="same@example.test")
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "same@example.test"})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == "same@example.test"
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_EMAIL_CHANGED).exists()


@pytest.mark.django_db
def test_blank_email_accepted_via_view():
    admin = create_admin("blank-view-admin")
    target = create_account("blank-view-target", email="old@example.test")
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": ""})

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.email == ""


# -- User Detail layout preserved -------------------------------------------


@pytest.mark.django_db
def test_user_detail_still_contains_role_and_enabled_actions():
    admin = create_admin("layout-admin")
    target = create_account("layout-target", email="old@example.test")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))
    content = response.content.decode()

    assert reverse("accounts:user_role", args=[target.pk]) in content
    assert reverse("accounts:user_active", args=[target.pk]) in content
    assert reverse("accounts:user_email", args=[target.pk]) in content


# -- Invitation invariants ---------------------------------------------------


@pytest.mark.django_db
def test_editing_invited_users_email_leaves_existing_invitation_intact():
    admin = create_admin("invariant-admin")
    target = create_setup_incomplete_target("invariant-target", email="old@example.test")
    issued = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(email_url(target), {"email": "corrected@example.test"})

    assert response.status_code == 302
    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None
    assert issued.invitation.accepted_at is None
    latest = services.latest_invitation_for_user(target)
    assert latest.pk == issued.invitation.pk


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_after_email_correction_uses_corrected_address():
    admin = create_admin("reissue-admin")
    target = create_setup_incomplete_target("reissue-target", email="old@example.test")
    services.issue_user_invitation(target=target, actor=admin)

    services.set_user_email(target=target, email="corrected@example.test", actor=admin)
    target.refresh_from_db()

    reissued = services.issue_user_invitation(target=target, actor=admin)
    result = mail.send_invitation_email(
        user=target,
        raw_token=reissued.raw_token,
        expires_at=reissued.invitation.expires_at,
    )

    assert result.sent is True
    sent = django_mail.outbox[0]
    assert sent.to == ["corrected@example.test"]
    assert "old@example.test" not in sent.body
