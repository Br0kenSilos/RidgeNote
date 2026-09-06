"""Invitation Acceptance Flow.

Covers the token-bearing entry route (`GET /invite/<raw_token>/`), the
token-free setup route (`GET/POST /invite/setup/`), the
`accept_user_invitation()` service, the `EVENT_INVITATION_ACCEPTED`
audit event, auto-login, the generic invalid-invitation response, the
already-authenticated interstitial, response security headers, CSRF
protection, and real PostgreSQL concurrency for double-accept and each
accept-vs-{reissue,revoke,disable} race. No SMTP, no admin create/invite
UI (see `test_user_management_invitations.py`), no
password reset -- this file covers the acceptance mechanics only.
"""

import re
import threading
import time

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User, UserInvitation

PASSWORD = "LongUniquePassword123!"
NEW_PASSWORD = "AnotherLongPassword456!"
PENDING_KEY = "pending_invitation_id"
UNAVAILABLE_MESSAGE = "This invitation is invalid or has expired."


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


def issue(target, admin=None):
    admin = admin or create_admin(f"{target.username}-admin")
    return services.issue_user_invitation(target=target, actor=admin)


def authenticated_client(user, *, enforce_csrf_checks=False):
    client = Client(enforce_csrf_checks=enforce_csrf_checks)
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def client_with_pending(invitation_id):
    client = Client()
    session = client.session
    session[PENDING_KEY] = invitation_id
    session.save()
    return client


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


CSRF_TOKEN_RE = re.compile(r'csrfmiddlewaretoken" value="([^"]+)"')


def _extract_csrf_token(html: str) -> str | None:
    match = CSRF_TOKEN_RE.search(html)
    return match.group(1) if match else None


# -- Entry route ------------------------------------------------------------


@pytest.mark.django_db
def test_valid_token_redirects_to_token_free_setup():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client()

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:invite_setup")


@pytest.mark.django_db
def test_valid_token_stores_invitation_pk_only_in_session():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client()

    client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert client.session[PENDING_KEY] == issued.invitation.pk


@pytest.mark.django_db
def test_raw_token_absent_from_session():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client()

    client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    session_values = [str(v) for v in dict(client.session).values()]
    assert not any(issued.raw_token in v for v in session_values)


@pytest.mark.django_db
def test_raw_token_absent_from_redirect_location():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client()

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert issued.raw_token not in response["Location"]


@pytest.mark.django_db
def test_entry_valid_response_has_no_store_and_no_referrer():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client()

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_unknown_token_shows_generic_unavailable():
    client = Client()

    response = client.get(
        reverse("accounts:invite_entry", kwargs={"raw_token": "not-a-real-token"})
    )

    assert response.status_code == 200
    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_unknown_token_entry_response_has_no_store_and_no_referrer():
    client = Client()

    response = client.get(
        reverse("accounts:invite_entry", kwargs={"raw_token": "not-a-real-token"})
    )

    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"


def _invalidated_invitation_scenarios():
    """Returns a list of zero-arg callables, each producing a raw token
    whose invitation is structurally invalid in a different way, for the
    "identical response regardless of reason" tests below."""

    def expired():
        target = create_setup_incomplete_target("expired-target")
        issued = issue(target)
        UserInvitation.objects.filter(pk=issued.invitation.pk).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
        return issued.raw_token

    def revoked():
        admin = create_admin("revoked-admin")
        target = create_setup_incomplete_target("revoked-target")
        issued = issue(target, admin)
        services.revoke_user_invitation(invitation=issued.invitation, actor=admin)
        return issued.raw_token

    def accepted():
        target = create_setup_incomplete_target("accepted-target")
        issued = issue(target)
        UserInvitation.objects.filter(pk=issued.invitation.pk).update(accepted_at=timezone.now())
        return issued.raw_token

    def superseded():
        admin = create_admin("superseded-admin")
        target = create_setup_incomplete_target("superseded-target")
        issued = issue(target, admin)
        services.issue_user_invitation(target=target, actor=admin)
        return issued.raw_token

    return {"expired": expired, "revoked": revoked, "accepted": accepted, "superseded": superseded}


@pytest.mark.django_db
@pytest.mark.parametrize("scenario_name", ["expired", "revoked", "accepted", "superseded"])
def test_invalid_states_all_produce_identical_unavailable_response(scenario_name):
    scenario = _invalidated_invitation_scenarios()[scenario_name]
    raw_token = scenario()
    client = Client()

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": raw_token}))

    assert response.status_code == 200
    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_invalid_response_reveals_no_user_identity():
    target = create_setup_incomplete_target("secret-invitee")
    issued = issue(target)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(accepted_at=timezone.now())
    client = Client()

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))
    body = response.content.decode()

    assert "secret-invitee" not in body
    assert target.email not in body if target.email else True


# -- Authenticated-user interstitial -----------------------------------------


@pytest.mark.django_db
def test_authenticated_user_opening_invite_does_not_switch_identity():
    visitor = create_account("already-signed-in")
    target = create_setup_incomplete_target("someone-else")
    issued = issue(target)
    client = authenticated_client(visitor)

    client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    response = client.get(reverse("accounts:account"))
    assert response.status_code == 200
    assert response.wsgi_request.user == visitor


@pytest.mark.django_db
def test_authenticated_visitor_creates_no_pending_session_state():
    visitor = create_account("no-session-write")
    target = create_setup_incomplete_target("target-b")
    issued = issue(target)
    client = authenticated_client(visitor)

    client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert PENDING_KEY not in client.session


@pytest.mark.django_db
def test_authenticated_visitor_token_is_never_looked_up(monkeypatch):
    visitor = create_account("no-lookup-visitor")
    target = create_setup_incomplete_target("target-c")
    issued = issue(target)
    client = authenticated_client(visitor)

    def _should_not_be_called(raw_token):
        raise AssertionError(
            "invitation_for_raw_token() must not be called for an authenticated visitor"
        )

    monkeypatch.setattr(services, "invitation_for_raw_token", _should_not_be_called)

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))
    assert response.status_code == 200


@pytest.mark.django_db
def test_interstitial_instructs_sign_out_and_shows_display_label():
    visitor = create_account("visible-visitor", display_name="Visible Visitor")
    target = create_setup_incomplete_target("target-d")
    issued = issue(target)
    client = authenticated_client(visitor)

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))
    body = response.content.decode()

    assert "Visible Visitor" in body
    assert "Sign" in body


@pytest.mark.django_db
def test_interstitial_response_has_no_store_and_strict_origin_referrer():
    """The interstitial renders a Sign Out POST form -- it must use
    `strict-origin`, not `no-referrer`. Firefox derives `Origin: null`
    for a same-origin POST from a page whose `Referrer-Policy` is
    `no-referrer`, which Django's
    CSRF middleware correctly rejects (`Origin checking failed - null
    does not match any trusted origins.`). `strict-origin` avoids that
    while still never revealing the raw-token path this page was reached
    through -- only the bare origin is ever sent as a referrer."""
    visitor = create_account("header-visitor")
    target = create_setup_incomplete_target("target-e")
    issued = issue(target)
    client = authenticated_client(visitor)

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "strict-origin"


@pytest.mark.django_db
def test_raw_token_not_preserved_through_logout():
    visitor = create_account("logout-visitor")
    target = create_setup_incomplete_target("target-f")
    issued = issue(target)
    client = authenticated_client(visitor)
    # Simulate a stale pending-invitation key somehow already present
    # (e.g. from an earlier anonymous visit in the same browser) to prove
    # Django's own logout() flushes it, independent of this module's own
    # design of never writing it for an authenticated visitor.
    session = client.session
    session[PENDING_KEY] = issued.invitation.pk
    session.save()

    client.post(reverse("accounts:logout"))

    assert PENDING_KEY not in client.session


# -- Setup GET ----------------------------------------------------------


@pytest.mark.django_db
def test_setup_get_with_valid_pending_renders_form():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.get(reverse("accounts:invite_setup"))

    assert response.status_code == 200
    assert "password1" in response.content.decode()


@pytest.mark.django_db
def test_setup_get_response_has_no_store_and_strict_origin_referrer():
    """The setup page renders the Set Password POST form -- it must use
    `strict-origin`, not `no-referrer`, for the same Firefox
    Origin-derivation reason as the interstitial. This page's own URL
    never contains the raw token regardless, so `strict-origin` costs
    nothing here beyond what `no-referrer` already avoided."""
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.get(reverse("accounts:invite_setup"))

    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "strict-origin"


@pytest.mark.django_db
def test_setup_get_missing_session_state_unavailable():
    client = Client()

    response = client.get(reverse("accounts:invite_setup"))

    assert response.status_code == 200
    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_stale_bad_pk_unavailable():
    client = client_with_pending(999_999_999)

    response = client.get(reverse("accounts:invite_setup"))

    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_revoked_after_entry_unavailable():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = issue(target, admin)
    client = client_with_pending(issued.invitation.pk)

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)
    response = client.get(reverse("accounts:invite_setup"))

    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_expired_after_entry_unavailable():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    response = client.get(reverse("accounts:invite_setup"))

    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_target_disabled_unavailable():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    target.is_active = False
    target.save(update_fields=["is_active"])
    response = client.get(reverse("accounts:invite_setup"))

    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_target_setup_complete_unavailable():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    target.setup_completed_at = timezone.now()
    target.save(update_fields=["setup_completed_at"])
    response = client.get(reverse("accounts:invite_setup"))

    assert UNAVAILABLE_MESSAGE in response.content.decode()


@pytest.mark.django_db
def test_setup_get_invalid_state_clears_pending_session_key():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = issue(target, admin)
    client = client_with_pending(issued.invitation.pk)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    client.get(reverse("accounts:invite_setup"))

    assert PENDING_KEY not in client.session


# -- Password form --------------------------------------------------------


@pytest.mark.django_db
def test_password_mismatch_rejected():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": "SomethingElse123!"},
    )

    assert response.status_code == 200
    assert "did not match" in response.content.decode()


@pytest.mark.django_db
def test_password_policy_failure_rejected():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.post(
        reverse("accounts:invite_setup"),
        {"password1": "short", "password2": "short"},
    )

    assert response.status_code == 200
    target.refresh_from_db()
    assert target.setup_completed_at is None


@pytest.mark.django_db
def test_form_error_causes_no_lifecycle_mutation():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(reverse("accounts:invite_setup"), {"password1": "x", "password2": "y"})

    target.refresh_from_db()
    issued.invitation.refresh_from_db()
    assert target.setup_completed_at is None
    assert issued.invitation.accepted_at is None


@pytest.mark.django_db
def test_form_error_retains_pending_session_key():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(reverse("accounts:invite_setup"), {"password1": "x", "password2": "y"})

    assert client.session[PENDING_KEY] == issued.invitation.pk


@pytest.mark.django_db
def test_raw_token_absent_from_rendered_setup_html():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.get(reverse("accounts:invite_setup"))

    assert issued.raw_token not in response.content.decode()


# -- Successful acceptance ------------------------------------------------


@pytest.mark.django_db
def test_acceptance_sets_password_via_django_hashing():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    target.refresh_from_db()
    assert target.password != NEW_PASSWORD
    assert check_password(NEW_PASSWORD, target.password)


@pytest.mark.django_db
def test_acceptance_sets_setup_completed_at_and_accepted_at_consistently():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    before = timezone.now()
    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )
    after = timezone.now()

    target.refresh_from_db()
    issued.invitation.refresh_from_db()
    assert target.setup_completed_at is not None
    assert issued.invitation.accepted_at is not None
    assert before <= target.setup_completed_at <= after
    assert abs((target.setup_completed_at - issued.invitation.accepted_at).total_seconds()) < 1


@pytest.mark.django_db
def test_acceptance_clears_must_change_password():
    target = create_setup_incomplete_target()
    target.must_change_password = True
    target.save(update_fields=["must_change_password"])
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    target.refresh_from_db()
    assert target.must_change_password is False


@pytest.mark.django_db
def test_acceptance_does_not_change_is_active_or_role():
    target = create_setup_incomplete_target()
    original_role = target.role
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    target.refresh_from_db()
    assert target.is_active is True
    assert target.role == original_role


@pytest.mark.django_db
def test_acceptance_preserves_invitation_row():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    assert UserInvitation.objects.filter(pk=issued.invitation.pk).exists()


@pytest.mark.django_db
def test_acceptance_records_one_correct_audit_event():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ACCEPTED)
    assert events.count() == 1
    event = events.get()
    target.refresh_from_db()
    assert event.actor_id == target.pk
    assert event.target_user_id == target.pk
    assert event.details["invitation_id"] == issued.invitation.pk


@pytest.mark.django_db
def test_acceptance_audit_excludes_sensitive_data():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_ACCEPTED)
    details_str = str(event.details)
    assert issued.raw_token not in details_str
    assert issued.invitation.token_hash not in details_str
    assert NEW_PASSWORD not in details_str


@pytest.mark.django_db
def test_acceptance_clears_pending_session_key():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    assert PENDING_KEY not in client.session


@pytest.mark.django_db
def test_acceptance_auto_logs_in_and_redirects_home():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    response = client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_authenticated_request_succeeds_after_acceptance():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    response = client.get(reverse("accounts:account"))
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated
    assert response.wsgi_request.user.pk == target.pk


@pytest.mark.django_db
def test_invitation_cannot_be_reused_after_acceptance():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = client_with_pending(issued.invitation.pk)

    client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    second_client = Client()
    response = second_client.get(
        reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token})
    )
    assert UNAVAILABLE_MESSAGE in response.content.decode()


# -- POST-time revalidation ------------------------------------------------


@pytest.mark.django_db
def test_service_rejects_expired_invitation_at_accept_time():
    target = create_setup_incomplete_target()
    issued = issue(target)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)

    target.refresh_from_db()
    assert target.setup_completed_at is None


@pytest.mark.django_db
def test_service_rejects_revoked_invitation_at_accept_time():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = issue(target, admin)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


@pytest.mark.django_db
def test_service_rejects_superseded_invitation_at_accept_time():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = issue(target, admin)
    services.issue_user_invitation(target=target, actor=admin)

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


@pytest.mark.django_db
def test_service_rejects_disabled_target_at_accept_time():
    target = create_setup_incomplete_target()
    issued = issue(target)
    target.is_active = False
    target.save(update_fields=["is_active"])

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


@pytest.mark.django_db
def test_service_rejects_target_already_setup_complete_at_accept_time():
    target = create_setup_incomplete_target()
    issued = issue(target)
    target.setup_completed_at = timezone.now()
    target.save(update_fields=["setup_completed_at"])

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)


@pytest.mark.django_db
def test_no_partial_mutation_on_failed_accept():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = issue(target, admin)
    original_password_hash = target.password
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    with pytest.raises(services.InvitationNoLongerEligibleError):
        services.accept_user_invitation(invitation_id=issued.invitation.pk, password=NEW_PASSWORD)

    target.refresh_from_db()
    issued.invitation.refresh_from_db()
    assert target.password == original_password_hash
    assert target.setup_completed_at is None
    assert issued.invitation.accepted_at is None
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ACCEPTED).exists()


# -- CSRF -------------------------------------------------------------------
#
# `{% csrf_token %}` is present in both templates, and a full round trip
# (real GET, extract the
# actual rendered token, POST it back with `enforce_csrf_checks=True`)
# succeeds against this code. Every "happy path" test above uses the
# default `Client()`, which disables CSRF enforcement entirely, so a
# missing/broken token could never be caught by those tests alone. The
# tests below close that gap by proving the actual rendered-token round
# trip, not just that a request without a token is rejected.


@pytest.mark.django_db
def test_setup_get_response_contains_a_csrf_token():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session[PENDING_KEY] = issued.invitation.pk
    session.save()

    response = client.get(reverse("accounts:invite_setup"))

    assert _extract_csrf_token(response.content.decode()) is not None


@pytest.mark.django_db
def test_setup_post_with_rendered_csrf_token_succeeds_under_enforcement():
    """Full round trip: a real GET renders a real token; that exact token,
    submitted with matching cookies and CSRF enforcement enabled, must
    reach `accept_user_invitation()` and succeed -- not merely avoid a
    403 in a test where CSRF checking is disabled."""
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session[PENDING_KEY] = issued.invitation.pk
    session.save()

    get_response = client.get(reverse("accounts:invite_setup"))
    token = _extract_csrf_token(get_response.content.decode())
    assert token is not None

    response = client.post(
        reverse("accounts:invite_setup"),
        {
            "csrfmiddlewaretoken": token,
            "password1": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    target.refresh_from_db()
    assert target.setup_completed_at is not None


@pytest.mark.django_db
def test_setup_post_succeeds_with_matching_origin_and_referer_headers():
    """A follow-up browser-reproduction investigation (second fresh-browser
    403 report) found the Django test client's default requests never set
    `Origin`/`Referer` at all, so `CsrfViewMiddleware`'s Origin-verification
    path was never exercised by any prior test. This proves a same-origin
    POST carrying `Origin`/`Referer` headers -- as any real browser sends
    for a same-page form submission -- still succeeds, closing that gap."""
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session[PENDING_KEY] = issued.invitation.pk
    session.save()

    get_response = client.get(reverse("accounts:invite_setup"))
    token = _extract_csrf_token(get_response.content.decode())
    assert token is not None

    same_origin = "http://testserver"
    response = client.post(
        reverse("accounts:invite_setup"),
        {
            "csrfmiddlewaretoken": token,
            "password1": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
        HTTP_ORIGIN=same_origin,
        HTTP_REFERER=f"{same_origin}{reverse('accounts:invite_setup')}",
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.setup_completed_at is not None


@pytest.mark.django_db
def test_setup_post_requires_csrf():
    target = create_setup_incomplete_target()
    issued = issue(target)
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session[PENDING_KEY] = issued.invitation.pk
    session.save()

    response = client.post(
        reverse("accounts:invite_setup"),
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    assert response.status_code == 403
    target.refresh_from_db()
    assert target.setup_completed_at is None


@pytest.mark.django_db
def test_interstitial_response_contains_a_csrf_token():
    visitor = create_account("interstitial-csrf-visitor")
    target = create_setup_incomplete_target("interstitial-csrf-target")
    issued = issue(target)
    client = authenticated_client(visitor, enforce_csrf_checks=True)

    response = client.get(reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token}))

    assert _extract_csrf_token(response.content.decode()) is not None


@pytest.mark.django_db
def test_interstitial_logout_with_rendered_csrf_token_succeeds_under_enforcement():
    """Full round trip for the already-signed-in interstitial's Sign Out
    form: a real GET renders a real token; that exact token, submitted
    with matching cookies and CSRF enforcement enabled, must reach the
    existing logout view and succeed."""
    visitor = create_account("interstitial-logout-visitor")
    target = create_setup_incomplete_target("interstitial-logout-target")
    issued = issue(target)
    client = authenticated_client(visitor, enforce_csrf_checks=True)

    get_response = client.get(
        reverse("accounts:invite_entry", kwargs={"raw_token": issued.raw_token})
    )
    token = _extract_csrf_token(get_response.content.decode())
    assert token is not None

    response = client.post(reverse("accounts:logout"), {"csrfmiddlewaretoken": token})

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:login")
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_logout_remains_post_only():
    visitor = create_account("logout-get-visitor")
    client = authenticated_client(visitor)

    response = client.get(reverse("accounts:logout"))

    assert response.status_code == 405


# -- Real PostgreSQL concurrency ------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_double_acceptance_exactly_one_succeeds(monkeypatch):
    target = create_setup_incomplete_target("race-double-accept")
    issued = issue(target)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services._lock_invitation_for_update
    call_count = {"n": 0}

    def pausing_lock(invitation_pk):
        locked = original_lock(invitation_pk)
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second accept attempt never proceeded"
        return locked

    monkeypatch.setattr(services, "_lock_invitation_for_update", pausing_lock)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.accept_user_invitation(
                invitation_id=issued.invitation.pk, password=NEW_PASSWORD
            )
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first accept attempt never reached the lock"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.accept_user_invitation(
                invitation_id=issued.invitation.pk, password="DifferentPassword789!"
            )
        except Exception as exc:
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)
    proceed.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    successes = [o for o in (outcome_a, outcome_b) if "result" in o]
    failures = [o for o in (outcome_a, outcome_b) if "error" in o]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0]["error"], services.InvitationNoLongerEligibleError)

    target.refresh_from_db()
    issued.invitation.refresh_from_db()
    assert target.setup_completed_at is not None
    assert issued.invitation.accepted_at is not None
    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ACCEPTED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_accept_versus_reissue_is_deterministic(monkeypatch):
    """Pauses the reissue thread mid-transaction (after it has already
    locked the user row and revoked prior invitations, via the same
    `_generate_invitation_token()` hook `test_invitation_service.py`'s own
    concurrency tests use), so it deterministically wins the race --
    exercising outcome B
    (reissue wins, old invitation revoked, pending acceptance fails
    cleanly), the more subtle of the two possible outcomes. Outcome A
    (acceptance wins first) is already exercised by the plain sequential
    coverage in the "Successful acceptance" tests above, since acceptance
    always locks the user row first when no other transaction is
    contending for it."""
    admin = create_admin("race-reissue-admin")
    target = create_setup_incomplete_target("race-reissue-target")
    issued = issue(target, admin)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_generate = services._generate_invitation_token
    call_count = {"n": 0}

    def pausing_generate():
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "concurrent accept attempt never proceeded"
        return original_generate()

    monkeypatch.setattr(services, "_generate_invitation_token", pausing_generate)

    outcome_reissue = {}

    def run_reissue():
        try:
            outcome_reissue["result"] = services.issue_user_invitation(target=target, actor=admin)
        except Exception as exc:  # pragma: no cover
            outcome_reissue["error"] = exc
        finally:
            connections.close_all()

    thread_reissue = _run_in_thread(run_reissue)
    assert lock_acquired.wait(timeout=5), "reissue never reached the paused point"

    outcome_accept = {}

    def run_accept():
        try:
            outcome_accept["result"] = services.accept_user_invitation(
                invitation_id=issued.invitation.pk, password=NEW_PASSWORD
            )
        except Exception as exc:
            outcome_accept["error"] = exc
        finally:
            connections.close_all()

    thread_accept = _run_in_thread(run_accept)
    time.sleep(0.3)
    proceed.set()
    thread_reissue.join(timeout=5)
    thread_accept.join(timeout=5)

    assert "error" not in outcome_reissue, outcome_reissue.get("error")
    assert "result" not in outcome_accept
    assert isinstance(outcome_accept["error"], services.InvitationNoLongerEligibleError)

    target.refresh_from_db()
    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is not None
    assert issued.invitation.accepted_at is None
    assert target.setup_completed_at is None
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ACCEPTED).exists()
    # New invitation from the reissue is the sole structurally-valid one.
    valid = [inv for inv in UserInvitation.objects.filter(user=target) if inv.is_valid]
    assert len(valid) == 1
    assert valid[0].pk == outcome_reissue["result"].invitation.pk


@pytest.mark.django_db(transaction=True)
def test_accept_versus_revoke_is_deterministic(monkeypatch):
    admin = create_admin("race-revoke-admin")
    target = create_setup_incomplete_target("race-revoke-target")
    issued = issue(target, admin)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services._lock_invitation_for_update
    call_count = {"n": 0}

    def pausing_lock(invitation_pk):
        locked = original_lock(invitation_pk)
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "revoke never proceeded"
        return locked

    monkeypatch.setattr(services, "_lock_invitation_for_update", pausing_lock)

    outcome_accept = {}

    def run_accept():
        try:
            outcome_accept["result"] = services.accept_user_invitation(
                invitation_id=issued.invitation.pk, password=NEW_PASSWORD
            )
        except Exception as exc:  # pragma: no cover
            outcome_accept["error"] = exc
        finally:
            connections.close_all()

    thread_accept = _run_in_thread(run_accept)
    assert lock_acquired.wait(timeout=5), "accept never reached the invitation lock"

    outcome_revoke = {}

    def run_revoke():
        try:
            outcome_revoke["result"] = services.revoke_user_invitation(
                invitation=issued.invitation, actor=admin
            )
        except Exception as exc:
            outcome_revoke["error"] = exc
        finally:
            connections.close_all()

    thread_revoke = _run_in_thread(run_revoke)
    time.sleep(0.3)
    proceed.set()
    thread_accept.join(timeout=5)
    thread_revoke.join(timeout=5)

    target.refresh_from_db()
    issued.invitation.refresh_from_db()

    if "result" in outcome_accept:
        assert target.setup_completed_at is not None
        assert issued.invitation.accepted_at is not None
        assert issued.invitation.revoked_at is None
    else:
        assert isinstance(outcome_accept["error"], services.InvitationNoLongerEligibleError)
        assert issued.invitation.revoked_at is not None
        assert target.setup_completed_at is None

    assert not (
        issued.invitation.accepted_at is not None and issued.invitation.revoked_at is not None
    )


@pytest.mark.django_db(transaction=True)
def test_accept_versus_disable_is_deterministic(monkeypatch):
    admin = create_admin("race-disable-admin")
    target = create_setup_incomplete_target("race-disable-target")
    issued = issue(target, admin)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services._lock_invitation_for_update
    call_count = {"n": 0}

    def pausing_lock(invitation_pk):
        locked = original_lock(invitation_pk)
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "disable never proceeded"
        return locked

    monkeypatch.setattr(services, "_lock_invitation_for_update", pausing_lock)

    outcome_accept = {}

    def run_accept():
        try:
            outcome_accept["result"] = services.accept_user_invitation(
                invitation_id=issued.invitation.pk, password=NEW_PASSWORD
            )
        except Exception as exc:  # pragma: no cover
            outcome_accept["error"] = exc
        finally:
            connections.close_all()

    thread_accept = _run_in_thread(run_accept)
    assert lock_acquired.wait(timeout=5), "accept never reached the invitation lock"

    outcome_disable = {}

    def run_disable():
        try:
            outcome_disable["result"] = services.set_user_active(
                target=target, active=False, actor=admin
            )
        except Exception as exc:
            outcome_disable["error"] = exc
        finally:
            connections.close_all()

    thread_disable = _run_in_thread(run_disable)
    time.sleep(0.3)
    proceed.set()
    thread_accept.join(timeout=5)
    thread_disable.join(timeout=5)

    assert "error" not in outcome_disable, outcome_disable.get("error")

    target.refresh_from_db()
    if "result" in outcome_accept:
        # Acceptance's own transaction committed before disable's lock
        # attempt could observe it, or disable ran first and acceptance
        # observed is_active still True at its own re-check -- either
        # way both operations complete without deadlock or corruption.
        assert target.setup_completed_at is not None
    else:
        assert isinstance(outcome_accept["error"], services.InvitationNoLongerEligibleError)
