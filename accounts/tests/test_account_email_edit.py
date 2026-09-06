"""Self-Service Email Editing.

A direct companion to `test_user_management_email_edit.py` (Admin Edit
User Email):
lets an authenticated, setup-complete user change their own email from
the existing Account page. Reuses the same `UserEmailForm`/
`services.set_user_email()`/`EVENT_EMAIL_CHANGED` completely unchanged
-- `target=request.user, actor=request.user` is the entire self-service
authorization boundary, matching the existing Display Name/password
sections' own "always operate on `request.user`" convention. General
Account-page/password-change coverage lives in `test_account_page.py`;
Display Name coverage lives in `test_account_display_name.py`; this
file is scoped to the Email section itself and its non-interference
with the other two sections. No separate form, service, or CSS
exists for this.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User

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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def email_payload(email):
    return {"account_action": "email", "email": email}


# ---------------------------------------------------------------------------
# Self-service change
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_authenticated_user_can_change_own_email():
    user = create_account("self-edit-user", email="old@example.test")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), email_payload("new@example.test"))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == "new@example.test"


@pytest.mark.django_db
def test_email_is_normalized_for_whitespace_and_case():
    user = create_account("normalize-user", email="old@example.test")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), email_payload("  New@Example.TEST  "))

    user.refresh_from_db()
    assert user.email == "new@example.test"


@pytest.mark.django_db
def test_duplicate_canonical_email_rejected_with_field_adjacent_error():
    create_account("existing-owner", email="taken@example.test")
    user = create_account("dup-user", email="old@example.test")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), email_payload("  Taken@Example.TEST "))

    assert response.status_code == 200
    content = response.content.decode()
    assert "A user with that email already exists." in content
    user.refresh_from_db()
    assert user.email == "old@example.test"


@pytest.mark.django_db
def test_blank_email_accepted():
    user = create_account("blank-user", email="old@example.test")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), email_payload(""))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == ""


@pytest.mark.django_db
def test_normalized_no_op_succeeds_with_no_audit():
    user = create_account("noop-user", email="same@example.test")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), email_payload("  Same@Example.TEST "))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.email == "same@example.test"
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_EMAIL_CHANGED).exists()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_genuine_change_creates_exactly_one_email_changed_event_self_audited():
    user = create_account("audit-user", email="old@example.test")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), email_payload("new@example.test"))

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_EMAIL_CHANGED)
    assert event.actor_id == user.pk
    assert event.target_user_id == user.pk
    assert event.details["old_email"] == "old@example.test"
    assert event.details["new_email"] == "new@example.test"


@pytest.mark.django_db
def test_duplicate_submission_creates_no_audit_event():
    create_account("dup-existing", email="taken@example.test")
    user = create_account("dup-audit-user", email="old@example.test")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), email_payload("taken@example.test"))

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_EMAIL_CHANGED).exists()


# ---------------------------------------------------------------------------
# Session / authentication
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_session_remains_authenticated_after_email_change():
    user = create_account("session-user", email="old@example.test")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), email_payload("new@example.test"))
    follow_up = client.get(reverse("accounts:account"))

    assert follow_up.status_code == 200
    assert follow_up.wsgi_request.user.is_authenticated
    assert follow_up.wsgi_request.user.pk == user.pk


@pytest.mark.django_db
def test_anonymous_user_redirected_to_login():
    response = Client().post(reverse("accounts:account"), email_payload("new@example.test"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


# ---------------------------------------------------------------------------
# Invitation / setup invariants
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_email_change_does_not_alter_setup_role_or_active_state():
    user = create_account("invariant-user", email="old@example.test", role=User.ROLE_USER)
    setup_at = user.setup_completed_at
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), email_payload("new@example.test"))

    user.refresh_from_db()
    assert user.setup_completed_at == setup_at
    assert user.role == User.ROLE_USER
    assert user.is_active is True
    assert user.deletion_scheduled_at is None


# ---------------------------------------------------------------------------
# UI / non-interference with the other two Account-page sections
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_page_renders_save_email_button():
    user = create_account("ui-user", email="old@example.test")
    client = authenticated_client(user)

    response = client.get(reverse("accounts:account"))
    content = response.content.decode()

    assert "Save email" in content


@pytest.mark.django_db
def test_display_name_section_still_renders_and_saves_independently():
    user = create_account("interop-user", email="old@example.test", display_name="Original Name")
    client = authenticated_client(user)

    response = client.get(reverse("accounts:account"))
    content = response.content.decode()
    assert "Save display name" in content
    assert 'value="Original Name"' in content

    client.post(
        reverse("accounts:account"),
        {"account_action": "display_name", "display_name": "Updated Name"},
    )

    user.refresh_from_db()
    assert user.display_name == "Updated Name"
    assert user.email == "old@example.test"
