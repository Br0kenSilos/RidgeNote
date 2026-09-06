"""Secure Invitation-Token Foundation.

Covers the `UserInvitation` model, `RIDGENOTE_INVITATION_EXPIRY_MINUTES`,
and the `issue_user_invitation()` / `revoke_user_invitation()` /
`invitation_for_raw_token()` service layer: token security, the in-service
administrator-actor authorization check, issuance eligibility, reissue
-with-supersession semantics, explicit-revoke idempotency, structural
validity, raw-token lookup, and real PostgreSQL concurrency for both the
zero-prior-invitation reissue race and the revoke/reissue race. This
file is scoped to the persistence and service layer only -- URL/view/UI
coverage lives in `test_user_management_invitations.py`, acceptance
coverage in `test_invitation_acceptance.py`, and SMTP delivery in
`test_invitation_email_delivery.py`.
"""

import hashlib
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User, UserInvitation

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


def create_setup_incomplete_target(username="invitee", **kwargs):
    kwargs["setup_completed_at"] = None
    return create_account(username, **kwargs)


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- Token security -----------------------------------------------------


@pytest.mark.django_db
def test_issuance_returns_a_nonempty_raw_token():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.raw_token
    assert isinstance(result.raw_token, str)


@pytest.mark.django_db
def test_raw_token_has_plausible_entropy():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    # secrets.token_urlsafe(32) encodes ~256 bits; avoid a brittle exact
    # -length assertion (URL-safe base64 padding/charset can vary the
    # rendered length slightly) and instead assert a generous floor.
    assert len(result.raw_token) >= 32
    assert len(set(result.raw_token)) > 1


@pytest.mark.django_db
def test_stored_hash_differs_from_raw_token():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.invitation.token_hash != result.raw_token


@pytest.mark.django_db
def test_stored_hash_equals_expected_sha256_digest():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    expected = hashlib.sha256(result.raw_token.encode("utf-8")).hexdigest()
    assert result.invitation.token_hash == expected
    assert len(result.invitation.token_hash) == 64


@pytest.mark.django_db
def test_token_hash_is_unique_at_the_database_level():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UserInvitation.objects.create(
                user=target,
                token_hash=result.invitation.token_hash,
                expires_at=timezone.now() + timedelta(minutes=120),
            )


@pytest.mark.django_db
def test_raw_token_absent_from_issuance_audit_details():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_ISSUED)
    assert result.raw_token not in str(event.details)


@pytest.mark.django_db
def test_token_hash_absent_from_issuance_audit_details():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_ISSUED)
    assert result.invitation.token_hash not in str(event.details)


# -- Expiry setting -------------------------------------------------------


def _settings_import_fails(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("RIDGENOTE_INVITATION_EXPIRY_MINUTES", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import ridgenote.settings"],
        cwd=django_settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_invitation_expiry_minutes_default_is_120():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ridgenote.settings as s; print(s.RIDGENOTE_INVITATION_EXPIRY_MINUTES)",
        ],
        cwd=django_settings.BASE_DIR,
        env={k: v for k, v in os.environ.items() if k != "RIDGENOTE_INVITATION_EXPIRY_MINUTES"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "120"


def test_invitation_expiry_minutes_explicit_positive_value_honored():
    env = os.environ.copy()
    env["RIDGENOTE_INVITATION_EXPIRY_MINUTES"] = "45"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ridgenote.settings as s; print(s.RIDGENOTE_INVITATION_EXPIRY_MINUTES)",
        ],
        cwd=django_settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "45"


@pytest.mark.parametrize(
    "raw_value",
    ["0", "-1", "not-an-int"],
)
def test_invitation_expiry_minutes_invalid_values_rejected(raw_value):
    result = _settings_import_fails({"RIDGENOTE_INVITATION_EXPIRY_MINUTES": raw_value})
    assert result.returncode != 0
    assert "RIDGENOTE_INVITATION_EXPIRY_MINUTES" in result.stderr


# -- Administrator authorization ------------------------------------------


@pytest.mark.django_db
def test_active_admin_can_issue():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.invitation.pk is not None


@pytest.mark.django_db
def test_ordinary_user_cannot_issue():
    ordinary = create_account("ordinary")
    target = create_setup_incomplete_target()

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.issue_user_invitation(target=target, actor=ordinary)

    assert not UserInvitation.objects.filter(user=target).exists()
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ISSUED).exists()


@pytest.mark.django_db
def test_inactive_admin_cannot_issue():
    admin = create_admin("inactive-admin")
    admin.is_active = False
    admin.save(update_fields=["is_active"])
    target = create_setup_incomplete_target()

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.issue_user_invitation(target=target, actor=admin)

    assert not UserInvitation.objects.filter(user=target).exists()


@pytest.mark.django_db
def test_stale_in_memory_actor_object_cannot_bypass_live_authorization():
    admin = create_admin("soon-demoted-admin")
    target = create_setup_incomplete_target()
    # Simulate an already-loaded `actor` object that no longer reflects
    # the live database row -- e.g. it was demoted by another admin
    # after this in-memory reference was obtained.
    stale_actor = User.objects.get(pk=admin.pk)
    admin.role = User.ROLE_USER
    admin.save(update_fields=["role"])

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.issue_user_invitation(target=target, actor=stale_actor)


@pytest.mark.django_db
def test_active_admin_can_revoke():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    revoked = services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    assert revoked.revoked_at is not None


@pytest.mark.django_db
def test_ordinary_user_cannot_revoke():
    admin = create_admin()
    ordinary = create_account("ordinary-revoker")
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.revoke_user_invitation(invitation=issued.invitation, actor=ordinary)

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).exists()


@pytest.mark.django_db
def test_inactive_admin_cannot_revoke():
    admin = create_admin()
    disabled_admin = create_admin("disabled-revoker")
    disabled_admin.is_active = False
    disabled_admin.save(update_fields=["is_active"])
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.revoke_user_invitation(invitation=issued.invitation, actor=disabled_admin)

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None


@pytest.mark.django_db
def test_nullable_created_by_does_not_imply_anonymous_issuance():
    admin = create_admin("later-deleted-admin")
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    assert issued.invitation.created_by_id == admin.pk

    admin.delete()
    issued.invitation.refresh_from_db()
    assert issued.invitation.created_by_id is None

    # A null `created_by` on an existing historical row must not weaken
    # the authorization check on a *new* issuance call.
    new_target = create_setup_incomplete_target("second-invitee")
    with pytest.raises(services.InvitationActorNotAuthorizedError):
        services.issue_user_invitation(target=new_target, actor=admin)


# -- Issuance ---------------------------------------------------------------


@pytest.mark.django_db
def test_setup_incomplete_target_can_receive_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.invitation.user_id == target.pk


@pytest.mark.django_db
def test_inactive_setup_incomplete_target_can_receive_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    target.is_active = False
    target.save(update_fields=["is_active"])

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.invitation.user_id == target.pk


@pytest.mark.django_db
def test_setup_complete_target_rejected():
    admin = create_admin()
    target = create_account("already-set-up")

    with pytest.raises(services.InvitationTargetAlreadySetUpError):
        services.issue_user_invitation(target=target, actor=admin)

    assert not UserInvitation.objects.filter(user=target).exists()


@pytest.mark.django_db
def test_issuance_timestamps_are_correct():
    admin = create_admin()
    target = create_setup_incomplete_target()

    before = timezone.now()
    result = services.issue_user_invitation(target=target, actor=admin)
    after = timezone.now()

    invitation = result.invitation
    assert before <= invitation.created_at <= after
    expected_expiry = invitation.created_at + timedelta(
        minutes=django_settings.RIDGENOTE_INVITATION_EXPIRY_MINUTES
    )
    assert abs((invitation.expires_at - expected_expiry).total_seconds()) < 2


@pytest.mark.django_db
def test_issuance_sets_created_by_to_the_issuing_admin():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    assert result.invitation.created_by_id == admin.pk


@pytest.mark.django_db
def test_issuance_records_correct_audit_event():
    admin = create_admin()
    target = create_setup_incomplete_target()

    result = services.issue_user_invitation(target=target, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_ISSUED)
    assert event.actor_id == admin.pk
    assert event.target_user_id == target.pk
    assert event.details["invitation_id"] == result.invitation.pk


@pytest.mark.django_db
def test_issuance_does_not_change_target_password_setup_or_active_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    original_password_hash = target.password
    original_is_active = target.is_active
    original_role = target.role

    services.issue_user_invitation(target=target, actor=admin)

    target.refresh_from_db()
    assert target.password == original_password_hash
    assert target.setup_completed_at is None
    assert target.is_active == original_is_active
    assert target.role == original_role


# -- Reissue ------------------------------------------------------------


@pytest.mark.django_db
def test_reissue_produces_a_fresh_token_and_hash():
    admin = create_admin()
    target = create_setup_incomplete_target()

    first = services.issue_user_invitation(target=target, actor=admin)
    second = services.issue_user_invitation(target=target, actor=admin)

    assert second.raw_token != first.raw_token
    assert second.invitation.token_hash != first.invitation.token_hash


@pytest.mark.django_db
def test_reissue_revokes_previous_outstanding_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()

    first = services.issue_user_invitation(target=target, actor=admin)
    services.issue_user_invitation(target=target, actor=admin)

    first.invitation.refresh_from_db()
    assert first.invitation.revoked_at is not None
    assert first.invitation.is_valid is False


@pytest.mark.django_db
def test_reissue_preserves_prior_row():
    admin = create_admin()
    target = create_setup_incomplete_target()

    first = services.issue_user_invitation(target=target, actor=admin)
    services.issue_user_invitation(target=target, actor=admin)

    assert UserInvitation.objects.filter(pk=first.invitation.pk).exists()
    assert UserInvitation.objects.filter(user=target).count() == 2


@pytest.mark.django_db
def test_only_newest_invitation_is_structurally_valid_after_reissue():
    admin = create_admin()
    target = create_setup_incomplete_target()

    services.issue_user_invitation(target=target, actor=admin)
    second = services.issue_user_invitation(target=target, actor=admin)

    valid_invitations = [inv for inv in UserInvitation.objects.filter(user=target) if inv.is_valid]
    assert len(valid_invitations) == 1
    assert valid_invitations[0].pk == second.invitation.pk


@pytest.mark.django_db
def test_expired_but_unrevoked_prior_invitation_is_also_superseded():
    admin = create_admin()
    target = create_setup_incomplete_target()

    first = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=first.invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    services.issue_user_invitation(target=target, actor=admin)

    first.invitation.refresh_from_db()
    assert first.invitation.revoked_at is not None


@pytest.mark.django_db
def test_automatic_supersession_emits_no_revoke_audit_event():
    admin = create_admin()
    target = create_setup_incomplete_target()

    services.issue_user_invitation(target=target, actor=admin)
    services.issue_user_invitation(target=target, actor=admin)

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).exists()
    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_ISSUED).count() == 2


# -- Explicit revoke ------------------------------------------------------


@pytest.mark.django_db
def test_explicit_revoke_invalidates_a_valid_invitation():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    issued.invitation.refresh_from_db()
    assert issued.invitation.is_valid is False


@pytest.mark.django_db
def test_explicit_revoke_preserves_the_row():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    assert UserInvitation.objects.filter(pk=issued.invitation.pk).exists()


@pytest.mark.django_db
def test_explicit_revoke_records_correct_audit_event():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_INVITATION_REVOKED)
    assert event.actor_id == admin.pk
    assert event.target_user_id == target.pk
    assert event.details["invitation_id"] == issued.invitation.pk


@pytest.mark.django_db
def test_repeat_revoke_is_a_safe_no_op_without_duplicate_audit_event():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).count() == 1


@pytest.mark.django_db
def test_revoking_an_expired_invitation_is_a_no_op_without_mutation_or_audit():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).exists()


@pytest.mark.django_db
def test_revoking_an_accepted_invitation_raises_and_does_not_mutate():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(accepted_at=timezone.now())

    with pytest.raises(services.InvitationAlreadyAcceptedError):
        services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    issued.invitation.refresh_from_db()
    assert issued.invitation.revoked_at is None
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).exists()


@pytest.mark.django_db
def test_explicit_revoke_does_not_change_target_lifecycle_fields():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    original_password_hash = target.password

    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    target.refresh_from_db()
    assert target.password == original_password_hash
    assert target.setup_completed_at is None
    assert target.is_active is True


# -- Structural validity --------------------------------------------------


@pytest.mark.django_db
def test_fresh_invitation_is_valid():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    assert issued.invitation.is_valid is True


@pytest.mark.django_db
def test_revoked_invitation_is_invalid():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(revoked_at=timezone.now())

    invitation = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert invitation.is_valid is False


@pytest.mark.django_db
def test_accepted_invitation_is_invalid():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(accepted_at=timezone.now())

    invitation = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert invitation.is_valid is False


@pytest.mark.django_db
def test_expired_invitation_is_invalid():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    invitation = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert invitation.is_valid is False


@pytest.mark.django_db
def test_expires_at_equal_to_now_is_invalid(monkeypatch):
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    frozen_now = timezone.now() + timedelta(minutes=5)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(expires_at=frozen_now)
    invitation = UserInvitation.objects.get(pk=issued.invitation.pk)

    # `expires_at > now` is the authorized rule -- exact equality must be
    # invalid, not valid. Patch `accounts.models.timezone.now` (rather
    # than comparing against a freshly re-read real clock, which would be
    # flaky) so `is_valid` observes exactly `expires_at == now`.
    from accounts import models as accounts_models

    monkeypatch.setattr(accounts_models.timezone, "now", lambda: frozen_now)
    assert invitation.is_valid is False


@pytest.mark.django_db
def test_checking_validity_does_not_mutate_the_row():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    before = UserInvitation.objects.get(pk=issued.invitation.pk)

    _ = before.is_valid
    _ = before.is_valid

    after = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert after.created_at == before.created_at
    assert after.expires_at == before.expires_at
    assert after.accepted_at == before.accepted_at
    assert after.revoked_at == before.revoked_at


# -- Lookup ---------------------------------------------------------------


@pytest.mark.django_db
def test_lookup_resolves_a_valid_raw_token():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)

    resolved = services.invitation_for_raw_token(issued.raw_token)

    assert resolved is not None
    assert resolved.pk == issued.invitation.pk


@pytest.mark.django_db
def test_lookup_unknown_token_returns_none():
    assert services.invitation_for_raw_token("not-a-real-token") is None


@pytest.mark.django_db
def test_lookup_revoked_token_returns_none():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    services.revoke_user_invitation(invitation=issued.invitation, actor=admin)

    assert services.invitation_for_raw_token(issued.raw_token) is None


@pytest.mark.django_db
def test_lookup_expired_token_returns_none():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    assert services.invitation_for_raw_token(issued.raw_token) is None


@pytest.mark.django_db
def test_lookup_accepted_token_returns_none():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    UserInvitation.objects.filter(pk=issued.invitation.pk).update(accepted_at=timezone.now())

    assert services.invitation_for_raw_token(issued.raw_token) is None


@pytest.mark.django_db
def test_lookup_does_not_mutate_state():
    admin = create_admin()
    target = create_setup_incomplete_target()
    issued = services.issue_user_invitation(target=target, actor=admin)
    before = UserInvitation.objects.get(pk=issued.invitation.pk)

    services.invitation_for_raw_token(issued.raw_token)

    after = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert after.revoked_at == before.revoked_at
    assert after.accepted_at == before.accepted_at
    assert after.expires_at == before.expires_at


@pytest.mark.django_db
def test_lookup_handles_empty_and_non_string_input_safely():
    assert services.invitation_for_raw_token("") is None
    assert services.invitation_for_raw_token(None) is None


# -- Real PostgreSQL concurrency ------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_reissue_for_same_target_leaves_exactly_one_valid_invitation(monkeypatch):
    admin = create_admin("concurrent-issue-admin")
    target = create_setup_incomplete_target("concurrent-issue-target")
    assert not UserInvitation.objects.filter(user=target).exists()

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_generate = services._generate_invitation_token
    call_count = {"n": 0}

    def pausing_generate():
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second reissue attempt never proceeded"
        return original_generate()

    monkeypatch.setattr(services, "_generate_invitation_token", pausing_generate)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.issue_user_invitation(target=target, actor=admin)
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first issuance attempt never reached the lock"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.issue_user_invitation(target=target, actor=admin)
        except Exception as exc:  # pragma: no cover
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)
    proceed.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert "error" not in outcome_a, outcome_a.get("error")
    assert "error" not in outcome_b, outcome_b.get("error")

    all_invitations = list(UserInvitation.objects.filter(user=target))
    valid_invitations = [inv for inv in all_invitations if inv.is_valid]
    assert len(all_invitations) == 2
    assert len(valid_invitations) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_revoke_and_reissue_leaves_deterministic_valid_state(monkeypatch):
    admin = create_admin("concurrent-race-admin")
    target = create_setup_incomplete_target("concurrent-race-target")
    issued = services.issue_user_invitation(target=target, actor=admin)
    connections.close_all()

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services._lock_invitation_for_update
    call_count = {"n": 0}

    def pausing_lock(invitation_pk):
        locked = original_lock(invitation_pk)
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "reissue attempt never proceeded"
        return locked

    monkeypatch.setattr(services, "_lock_invitation_for_update", pausing_lock)

    outcome_revoke = {}

    def run_revoke():
        try:
            outcome_revoke["result"] = services.revoke_user_invitation(
                invitation=issued.invitation, actor=admin
            )
        except Exception as exc:  # pragma: no cover
            outcome_revoke["error"] = exc
        finally:
            connections.close_all()

    thread_revoke = _run_in_thread(run_revoke)
    assert lock_acquired.wait(timeout=5), "revoke attempt never reached the invitation lock"

    outcome_reissue = {}

    def run_reissue():
        try:
            outcome_reissue["result"] = services.issue_user_invitation(target=target, actor=admin)
        except Exception as exc:  # pragma: no cover
            outcome_reissue["error"] = exc
        finally:
            connections.close_all()

    thread_reissue = _run_in_thread(run_reissue)
    time.sleep(0.3)
    proceed.set()
    thread_revoke.join(timeout=5)
    thread_reissue.join(timeout=5)

    assert "error" not in outcome_revoke, outcome_revoke.get("error")
    assert "error" not in outcome_reissue, outcome_reissue.get("error")

    original = UserInvitation.objects.get(pk=issued.invitation.pk)
    assert original.revoked_at is not None
    assert original.is_valid is False

    all_invitations = list(UserInvitation.objects.filter(user=target))
    valid_invitations = [inv for inv in all_invitations if inv.is_valid]
    assert len(all_invitations) == 2
    assert len(valid_invitations) == 1
    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_INVITATION_REVOKED).count() == 1
