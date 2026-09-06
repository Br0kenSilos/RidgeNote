"""Optional SMTP Invitation Delivery.

Covers `accounts/mail.py` (content rendering, send/failure categorization),
the `send_invitation`/Reissue send-checkbox orchestration in
`accounts/views.py` (strictly post-commit, never rolling back the
underlying invitation mutation on a mail failure), the success-only
`EVENT_INVITATION_SENT` audit event, and the manual-vs-emailed URL
distinction. The admin UI's own invitation-management suite
(`test_user_management_invitations.py`) is intentionally untouched here
and is not duplicated.
"""

import logging
import re
from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail as django_mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import mail, services
from accounts.forms import SETUP_METHOD_INVITATION, SETUP_METHOD_SET_PASSWORD
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
EXTERNAL_URL = "https://ridgenote.example.test"

SMTP_CONFIGURED = override_settings(
    RIDGENOTE_SMTP_CONFIGURED=True,
    RIDGENOTE_EXTERNAL_URL=EXTERNAL_URL,
)
SMTP_UNCONFIGURED = override_settings(
    RIDGENOTE_SMTP_CONFIGURED=False,
    RIDGENOTE_EXTERNAL_URL="",
)


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


def invitation_payload(username="new-invited-user", *, send_invitation=None, **overrides):
    payload = {
        "setup_method": SETUP_METHOD_INVITATION,
        "username": username,
        "display_name": username,
        "email": "invitee@example.test",
        "role": User.ROLE_USER,
        "is_active": "on",
    }
    if send_invitation:
        payload["send_invitation"] = "on"
    payload.update(overrides)
    return payload


def temporary_password_payload(username="new-temp-user", **overrides):
    payload = {
        "setup_method": SETUP_METHOD_SET_PASSWORD,
        "username": username,
        "display_name": username,
        "email": "temp-user@example.test",
        "role": User.ROLE_USER,
        "is_active": "on",
        "password1": "AnotherLongPassword456!",
        "password2": "AnotherLongPassword456!",
        "require_password_change": "on",
    }
    payload.update(overrides)
    return payload


# -- Mail rendering (accounts/mail.py, direct unit tests) -------------------


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_has_correct_subject():
    user = create_setup_incomplete_target(email="invitee@example.test", display_name="Invitee")
    expires_at = timezone.now() + timezone.timedelta(minutes=120)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert message.subject == "You're invited to RidgeNote"


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_recipient_is_target_email():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = timezone.now() + timezone.timedelta(minutes=120)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert message.to == ["invitee@example.test"]


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_plain_text_contains_external_url_and_token():
    user = create_setup_incomplete_target(email="invitee@example.test", display_name="Invitee")
    expires_at = timezone.now() + timezone.timedelta(minutes=120)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert f"{EXTERNAL_URL}/invite/tok123/" in message.body
    assert "Invitee" in message.body
    assert "ignore this email" in message.body.lower()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_has_html_alternative_with_link():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = timezone.now() + timezone.timedelta(minutes=120)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert len(message.alternatives) == 1
    html_body, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert f"{EXTERNAL_URL}/invite/tok123/" in html_body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_never_leaks_internal_identifiers():
    # The boilerplate copy legitimately says "An administrator has
    # invited you" (referring to the *inviting* admin generically, and
    # incidentally containing the substring "admin" -- not a leak). This
    # test instead confirms the recipient's own specific username and
    # database ID are never echoed.
    user = create_setup_incomplete_target(
        username="secretusername",
        display_name="Invitee Display Name",
        email="invitee@example.test",
        role=User.ROLE_ADMIN,
    )
    expires_at = timezone.now() + timezone.timedelta(minutes=120)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    combined = message.body + message.alternatives[0][0]
    assert "secretusername" not in combined


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_expiry_renders_human_readable_summer_edt():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = datetime(2026, 8, 15, 23, 25, 21, 487980, tzinfo=UTC)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert "August 15, 2026 at 7:25 PM EDT" in message.body
    assert "August 15, 2026 at 7:25 PM EDT" in message.alternatives[0][0]


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_expiry_renders_human_readable_winter_est():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = datetime(2026, 1, 15, 20, 25, 0, tzinfo=UTC)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert "January 15, 2026 at 3:25 PM EST" in message.body
    assert "January 15, 2026 at 3:25 PM EST" in message.alternatives[0][0]


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_expiry_ignores_invited_users_own_timezone():
    user = create_setup_incomplete_target(email="invitee@example.test", timezone_name="Asia/Tokyo")
    expires_at = datetime(2026, 8, 15, 23, 25, 21, tzinfo=UTC)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert "August 15, 2026 at 7:25 PM EDT" in message.body
    assert "Asia/Tokyo" not in message.body
    # Tokyo (UTC+9) would render this instant as "August 16" at 08:25 --
    # confirming the application timezone was used, not the user's.
    assert "August 16" not in message.body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_build_invitation_email_expiry_omits_raw_offset_representation():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = datetime(2026, 8, 15, 23, 25, 21, 487980, tzinfo=UTC)

    message = mail.build_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert "487980" not in message.body
    assert "-04:00" not in message.body
    assert "+00:00" not in message.body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_send_invitation_email_populates_expiry_context():
    user = create_setup_incomplete_target(email="invitee@example.test")
    expires_at = timezone.now() + timezone.timedelta(minutes=45)

    result = mail.send_invitation_email(user=user, raw_token="tok123", expires_at=expires_at)

    assert result.sent is True
    sent = django_mail.outbox[0]
    assert "expires" in sent.body.lower()


# -- send_invitation_email() eligibility guards ------------------------------


@pytest.mark.django_db
@SMTP_UNCONFIGURED
def test_send_invitation_email_unconfigured_smtp_returns_not_sent():
    user = create_setup_incomplete_target(email="invitee@example.test")

    result = mail.send_invitation_email(user=user, raw_token="tok123", expires_at=timezone.now())

    assert result.sent is False
    assert result.reason == mail.MAIL_REASON_SMTP_NOT_CONFIGURED
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_send_invitation_email_blank_recipient_email_returns_not_sent():
    user = create_setup_incomplete_target(email="")

    result = mail.send_invitation_email(user=user, raw_token="tok123", expires_at=timezone.now())

    assert result.sent is False
    assert result.reason == mail.MAIL_REASON_RECIPIENT_EMAIL_UNAVAILABLE
    assert django_mail.outbox == []


@pytest.mark.django_db
@override_settings(RIDGENOTE_SMTP_CONFIGURED=True, RIDGENOTE_EXTERNAL_URL="")
def test_send_invitation_email_missing_external_url_returns_not_sent():
    user = create_setup_incomplete_target(email="invitee@example.test")

    result = mail.send_invitation_email(user=user, raw_token="tok123", expires_at=timezone.now())

    assert result.sent is False
    assert result.reason == mail.MAIL_REASON_EXTERNAL_URL_UNAVAILABLE
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_send_invitation_email_transport_failure_is_categorized_not_raised(monkeypatch):
    user = create_setup_incomplete_target(email="invitee@example.test")

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError("simulated connection refusal")

    monkeypatch.setattr(
        "django.core.mail.message.EmailMessage.send",
        raise_smtp_error,
    )

    result = mail.send_invitation_email(user=user, raw_token="tok123", expires_at=timezone.now())

    assert result.sent is False
    assert result.reason == mail.MAIL_REASON_SEND_FAILED


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_send_invitation_email_transport_failure_log_excludes_sensitive_data(monkeypatch, caplog):
    user = create_setup_incomplete_target(email="invitee@example.test")

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError(
            "simulated failure mentioning hunter2secretpassword and a raw url "
            f"{EXTERNAL_URL}/invite/should-not-appear/"
        )

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", raise_smtp_error)

    with caplog.at_level(logging.WARNING):
        mail.send_invitation_email(
            user=user, raw_token="should-not-appear", expires_at=timezone.now()
        )

    log_text = caplog.text
    assert "hunter2secretpassword" not in log_text
    assert "should-not-appear" not in log_text
    assert "/invite/" not in log_text


# -- Create User send behavior ------------------------------------------


@pytest.mark.django_db
@SMTP_UNCONFIGURED
def test_create_user_smtp_unconfigured_preserves_manual_invitation_behavior():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"), invitation_payload(username="unconfigured-user")
    )

    assert response.status_code == 200
    assert django_mail.outbox == []
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_SENT).exists()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_configured_email_present_send_checked_sends():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="send-checked-user", send_invitation=True),
    )

    assert response.status_code == 200
    assert len(django_mail.outbox) == 1
    assert django_mail.outbox[0].to == ["invitee@example.test"]


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_configured_send_unchecked_does_not_send():
    admin = create_admin()
    client = authenticated_client(admin)

    payload = invitation_payload(username="send-unchecked-user")
    payload.pop("send_invitation", None)
    response = client.post(reverse("accounts:user_create"), payload)

    assert response.status_code == 200
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_blank_target_email_never_sends():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="blank-email-user", email="", send_invitation=True),
    )

    assert response.status_code == 200
    assert django_mail.outbox == []
    created = User.objects.get(username="blank-email-user")
    assert created.setup_completed_at is None  # invitation still created normally


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_set_password_mode_never_sends_regardless_of_flag():
    admin = create_admin()
    client = authenticated_client(admin)

    payload = temporary_password_payload(username="set-password-user")
    payload["send_invitation"] = "on"
    response = client.post(reverse("accounts:user_create"), payload, follow=True)

    assert response.status_code == 200
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_successful_send_produces_event_invitation_sent():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="audit-send-user", send_invitation=True),
    )

    created = User.objects.get(username="audit-send-user")
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_SENT, target_user=created)
    assert event.actor_id == admin.pk
    assert event.details["recipient_email"] == "invitee@example.test"
    assert event.details["delivery"] == "smtp"


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_sent_audit_details_contain_no_raw_token_or_url():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="no-leak-user", send_invitation=True),
    )

    created = User.objects.get(username="no-leak-user")
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_SENT, target_user=created)
    details_text = str(event.details)
    assert "token" not in details_text.lower()
    assert "/invite/" not in details_text
    assert "http" not in details_text.lower()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_send_failure_does_not_roll_back_user_or_invitation(monkeypatch):
    admin = create_admin()
    client = authenticated_client(admin)

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError("simulated")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", raise_smtp_error)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="failure-user", send_invitation=True),
    )

    assert response.status_code == 200
    created = User.objects.get(username="failure-user")
    assert created.has_usable_password() is False
    assert not created.has_completed_setup
    latest = services.latest_invitation_for_user(created)
    assert latest is not None
    assert latest.is_valid
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_SENT).exists()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_send_failure_leaves_manual_link_in_response(monkeypatch):
    admin = create_admin()
    client = authenticated_client(admin)

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError("simulated")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", raise_smtp_error)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="manual-fallback-user", send_invitation=True),
    )

    body = response.content.decode()
    assert "/invite/" in body
    assert "could not send the email" in body


@pytest.mark.django_db
@override_settings(RIDGENOTE_SMTP_CONFIGURED=True, RIDGENOTE_EXTERNAL_URL="")
def test_create_user_missing_external_url_does_not_roll_back_creation():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="no-external-url-user", send_invitation=True),
    )

    assert response.status_code == 200
    assert User.objects.filter(username="no-external-url-user").exists()
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_manual_link_uses_request_origin_not_external_url():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="origin-check-user", send_invitation=True),
    )

    body = response.content.decode()
    assert "testserver" in body  # Django test client's default request host
    assert EXTERNAL_URL not in body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_emailed_url_uses_external_url_not_request_origin():
    admin = create_admin()
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="emailed-origin-user", send_invitation=True),
    )

    sent = django_mail.outbox[0]
    assert EXTERNAL_URL in sent.body
    assert "testserver" not in sent.body


# -- Reissue / Issue send behavior ---------------------------------------


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_checked_send_produces_fresh_emailed_link():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert response.status_code == 200
    assert len(django_mail.outbox) == 1
    assert django_mail.outbox[0].to == ["invitee@example.test"]


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_prior_invitation_superseded_when_sending():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    first = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    first.invitation.refresh_from_db()
    assert first.invitation.revoked_at is not None


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_emailed_link_matches_the_new_not_prior_token():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    body = response.content.decode()
    match = re.search(r'value="(http://[^"]*/invite/([^"/]+)/)"', body)
    assert match is not None
    manual_token = match.group(2)
    sent = django_mail.outbox[0]
    assert manual_token in sent.body
    assert f"{EXTERNAL_URL}/invite/{manual_token}/" in sent.body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_send_failure_leaves_new_invitation_valid_and_old_superseded(monkeypatch):
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    first = services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError("simulated")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", raise_smtp_error)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert response.status_code == 200
    first.invitation.refresh_from_db()
    assert first.invitation.revoked_at is not None
    latest = services.latest_invitation_for_user(target)
    assert latest.pk != first.invitation.pk
    assert latest.is_valid
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_SENT).exists()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_unchecked_does_not_send():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_invitation_reissue", args=[target.pk]), {})

    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_blank_email_does_not_send_even_if_requested():
    admin = create_admin()
    target = create_setup_incomplete_target(email="")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_issue_first_invitation_uses_identical_send_path_as_reissue():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert response.status_code == 200
    assert len(django_mail.outbox) == 1


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_setup_complete_target_still_rejected():
    admin = create_admin()
    target = create_account("already-set-up")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
        follow=True,
    )

    assert django_mail.outbox == []
    assert "already completed setup" in response.content.decode()


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_successful_send_produces_success_audit():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_INVITATION_SENT, target_user=target
    ).exists()


# -- Security ---------------------------------------------------------------


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_ordinary_user_cannot_invoke_create_user_send_flow():
    user = create_account("ordinary-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="forbidden-user", send_invitation=True),
    )

    assert response.status_code in {302, 403}
    assert not User.objects.filter(username="forbidden-user").exists()
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_ordinary_user_cannot_invoke_reissue_send_flow():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    ordinary = create_account("ordinary-user-2")
    client = authenticated_client(ordinary)

    response = client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    assert response.status_code in {302, 403}
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_csrf_protection_unchanged():
    admin = create_admin()
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = admin.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.post(
        reverse("accounts:user_create"),
        invitation_payload(username="csrf-user", send_invitation=True),
    )

    assert response.status_code == 403
    assert django_mail.outbox == []


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_no_failed_send_audit_event_exists(monkeypatch):
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    def raise_smtp_error(self, fail_silently=False):
        raise ConnectionRefusedError("simulated")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", raise_smtp_error)

    client.post(
        reverse("accounts:user_invitation_reissue", args=[target.pk]),
        {"send_invitation": "on"},
    )

    # No "failed" event type is ever recorded -- this codebase's audit
    # philosophy records completed domain mutations only, not transient
    # mail-transport telemetry.
    assert not AuditEvent.objects.filter(event_type__icontains="failed").exists()
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_SENT).exists()


# -- UI: send-checkbox visibility -----------------------------------------


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_send_checkbox_rendered_when_configured():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))

    body = response.content.decode()
    assert 'name="send_invitation"' in body
    assert "Send invitation by email" in body


@pytest.mark.django_db
@SMTP_UNCONFIGURED
def test_create_user_send_checkbox_absent_when_unconfigured():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))

    body = response.content.decode()
    assert 'name="send_invitation"' not in body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_create_user_send_checkbox_defaults_checked():
    admin = create_admin()
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_create"))

    body = response.content.decode()
    checkbox_html = body.split('name="send_invitation"')[1].split(">")[0]
    assert "checked" in checkbox_html


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_confirm_send_checkbox_shown_when_eligible():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_invitation_reissue", args=[target.pk]))

    body = response.content.decode()
    assert 'name="send_invitation"' in body
    assert "invitee@example.test" in body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_reissue_confirm_send_checkbox_absent_when_target_email_blank():
    admin = create_admin()
    target = create_setup_incomplete_target(email="")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_invitation_reissue", args=[target.pk]))

    body = response.content.decode()
    assert 'name="send_invitation"' not in body


@pytest.mark.django_db
@SMTP_CONFIGURED
def test_no_persistent_delivery_history_surface_on_user_detail():
    admin = create_admin()
    target = create_setup_incomplete_target(email="invitee@example.test")
    services.issue_user_invitation(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", kwargs={"user_id": target.pk}))

    body = response.content.decode()
    assert "Delivery history" not in body
    assert "delivery-history" not in body
    assert "Sent" not in body
