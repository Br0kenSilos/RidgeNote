"""controlled permanent account purge.

Covers `purge_user_account()`: eligible purge, all rejection/failure paths,
ORM cascade behavior, audit payload shape, username reuse, and the real
two-connection PostgreSQL concurrency scenarios required by the contract.
Administrator self-action protections (self-demotion,
self-disablement, self-scheduling) are exercised elsewhere
(`accounts/tests/test_accounts.py`, `accounts/tests/test_account_deletion.py`)
and are only regression-verified here for the purge-specific interaction.
"""

import threading
import time
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone
from notes import documents
from notes.models import Folder, Note, Tag

from accounts import services
from accounts.models import AuditEvent, User
from accounts.tests.test_account_deletion import (
    _run_in_thread,
    create_account,
    create_admin,
)


def _schedule_and_make_eligible(target, *, actor=None):
    actor = actor or target
    services.schedule_user_deletion(target=target, actor=actor)
    target.refresh_from_db()
    target.deletion_recovery_deadline = timezone.now() - timedelta(seconds=1)
    target.save(update_fields=["deletion_recovery_deadline"])
    target.refresh_from_db()
    return target


def _force_malformed_lifecycle(user):
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE accounts_user DROP CONSTRAINT "
            "accounts_user_deletion_restore_flag_paired_ck"
        )
        cursor.execute(
            "UPDATE accounts_user SET deletion_restore_is_active = NULL WHERE id = %s",
            [user.pk],
        )
        cursor.execute(
            "ALTER TABLE accounts_user ADD CONSTRAINT "
            "accounts_user_deletion_restore_flag_paired_ck CHECK ("
            "(deletion_scheduled_at IS NULL AND deletion_restore_is_active IS NULL) "
            "OR (deletion_scheduled_at IS NOT NULL AND deletion_restore_is_active IS NOT NULL)"
            ") NOT VALID"
        )


@pytest.mark.django_db
def test_purge_removes_eligible_account_and_returns_result_counts():
    admin = create_admin("purge-admin")
    other_admin = create_admin("purge-other-admin")
    target = create_account("purge-target")
    Note.objects.create(
        owner=target,
        title="active note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
    )
    Note.objects.create(
        owner=target,
        title="trashed note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        trashed_at=timezone.now(),
    )
    Folder.objects.create(owner=target, name="active folder")
    Folder.objects.create(owner=target, name="trashed folder", trashed_at=timezone.now())
    Tag.objects.create(owner=target, name="tag-one")

    _schedule_and_make_eligible(target, actor=other_admin)

    result = services.purge_user_account(target=target, actor=admin)

    assert result.username_snapshot == "purge-target"
    assert result.notes_active == 1
    assert result.notes_trashed == 1
    assert result.folders_active == 1
    assert result.folders_trashed == 1
    assert result.tags == 1
    assert not User.objects.filter(pk=target.pk).exists()

    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED)
    assert events.count() == 1
    event = events.first()
    assert event.actor_id == admin.pk
    assert event.target_user_id is None
    assert event.target_username_snapshot == "purge-target"
    assert event.details == {
        "username_snapshot": "purge-target",
        "notes_active": 1,
        "notes_trashed": 1,
        "folders_active": 1,
        "folders_trashed": 1,
        "tags": 1,
    }


@pytest.mark.django_db
def test_purge_cascades_notes_folders_tags_and_m2m_without_restrict_error():
    admin = create_admin("cascade-admin")
    target = create_account("cascade-target")
    folder = Folder.objects.create(owner=target, name="folder")
    note = Note.objects.create(
        owner=target,
        title="note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        folder=folder,
    )
    tag = Tag.objects.create(owner=target, name="tag")
    note.tags.add(tag)

    _schedule_and_make_eligible(target)

    services.purge_user_account(target=target, actor=admin)

    assert not Note.objects.filter(pk=note.pk).exists()
    assert not Folder.objects.filter(pk=folder.pk).exists()
    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not Note.tags.through.objects.filter(note_id=note.pk).exists()


@pytest.mark.django_db
def test_purge_rejects_before_deadline():
    admin = create_admin("deadline-admin")
    target = create_account("deadline-target")
    services.schedule_user_deletion(target=target, actor=admin)
    target.refresh_from_db()

    with pytest.raises(services.PurgeDeadlineNotElapsedError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_DEADLINE)
    assert events.count() == 1
    assert events.first().details["reason"] == "deadline_not_elapsed"


@pytest.mark.django_db
def test_purge_rejects_when_not_pending_with_no_audit_event():
    admin = create_admin("not-pending-admin")
    target = create_account("not-pending-target")

    with pytest.raises(services.NotPendingDeletionError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()
    assert not AuditEvent.objects.filter(target_user=target).exists()


@pytest.mark.django_db
def test_purge_rejects_self_purge_with_other_admin_present():
    # Self-purge is only reachable while the actor is still active: actor
    # authorization is revalidated *before* the self-purge check, and
    # scheduling deletion for a target (a purge precondition) deactivates
    # that same account -- so an admin who is already pending deletion
    # would instead fail actor revalidation first, not the self-purge
    # check. This exercises the reachable path: a still-active admin
    # attempting to purge themselves before ever being scheduled.
    admin = create_admin("self-purge-admin")
    create_admin("self-purge-other-admin")

    with pytest.raises(services.SelfPurgeNotAllowedError):
        services.purge_user_account(target=admin, actor=admin)

    assert User.objects.filter(pk=admin.pk).exists()
    admin.refresh_from_db()
    assert admin.is_active is True
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_SELF).count()
        == 1
    )


@pytest.mark.django_db
def test_purge_rejects_self_purge_even_as_only_active_admin():
    admin = create_admin("lone-self-purge-admin")

    with pytest.raises(services.SelfPurgeNotAllowedError):
        services.purge_user_account(target=admin, actor=admin)

    assert User.objects.filter(pk=admin.pk).exists()
    admin.refresh_from_db()
    assert admin.is_active is True


@pytest.mark.django_db
def test_purge_rejects_when_actor_no_longer_exists():
    admin = create_admin("vanishing-admin")
    other_admin = create_admin("vanishing-admin-authorizer")
    target = create_account("vanishing-actor-target")
    _schedule_and_make_eligible(target, actor=other_admin)

    stale_actor_pk = admin.pk
    User.objects.filter(pk=admin.pk).delete()
    stale_actor = User(pk=stale_actor_pk, username="vanishing-admin", role=User.ROLE_ADMIN)

    with pytest.raises(services.PurgeActorNotAuthorizedError):
        services.purge_user_account(target=target, actor=stale_actor)

    assert User.objects.filter(pk=target.pk).exists()
    events = AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_ACTOR_UNAUTHORIZED
    )
    assert events.count() == 1
    assert events.first().actor_id is None


@pytest.mark.django_db
def test_purge_rejects_when_actor_inactive():
    admin = create_admin("inactive-actor-admin")
    other_admin = create_admin("inactive-actor-authorizer")
    target = create_account("inactive-actor-target")
    _schedule_and_make_eligible(target, actor=other_admin)

    admin.is_active = False
    admin.save(update_fields=["is_active"])

    with pytest.raises(services.PurgeActorNotAuthorizedError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_purge_rejects_when_actor_demoted():
    admin = create_admin("demoted-actor-admin")
    other_admin = create_admin("demoted-actor-authorizer")
    target = create_account("demoted-actor-target")
    _schedule_and_make_eligible(target, actor=other_admin)

    admin.role = User.ROLE_USER
    admin.save(update_fields=["role"])

    with pytest.raises(services.PurgeActorNotAuthorizedError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_purge_routes_malformed_lifecycle_through_failure_audit():
    admin = create_admin("malformed-admin")
    other_admin = create_admin("malformed-authorizer")
    target = create_account("malformed-target")
    _schedule_and_make_eligible(target, actor=other_admin)
    _force_malformed_lifecycle(target)

    with pytest.raises(services.InvalidAccountDeletionStateError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()
    failure_events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PURGE_FAILED)
    assert failure_events.count() == 1
    assert failure_events.first().details["exception"] == "InvalidAccountDeletionStateError"
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_DEADLINE
    ).exists()


@pytest.mark.django_db
def test_purge_rolls_back_and_writes_failure_audit_on_unexpected_error(monkeypatch):
    admin = create_admin("rollback-admin")
    target = create_account("rollback-target")
    Note.objects.create(
        owner=target,
        title="note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
    )
    _schedule_and_make_eligible(target)

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated deletion failure")

    monkeypatch.setattr(User, "delete", _boom)

    with pytest.raises(RuntimeError):
        services.purge_user_account(target=target, actor=admin)

    assert User.objects.filter(pk=target.pk).exists()
    assert Note.objects.filter(owner=target).exists()
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED
    ).exists()
    failure_events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PURGE_FAILED)
    assert failure_events.count() == 1
    assert failure_events.first().details["exception"] == "RuntimeError"


@pytest.mark.django_db
def test_username_reusable_immediately_after_purge():
    admin = create_admin("reuse-admin")
    target = create_account("reuse-target")
    _schedule_and_make_eligible(target)

    services.purge_user_account(target=target, actor=admin)

    replacement = create_account("reuse-target")
    assert replacement.pk != target.pk
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED,
        target_username_snapshot="reuse-target",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_purge_attempts_serialize_and_second_fails_safely(monkeypatch):
    admin = create_admin("dual-purge-admin")
    target = create_account("dual-purge-target")
    _schedule_and_make_eligible(target)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock
    call_count = {"n": 0}

    def pausing_lock():
        original_lock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second purge attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.purge_user_account(target=target, actor=admin)
        except Exception as exc:
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first purge attempt never reached the lock"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.purge_user_account(target=target, actor=admin)
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
    assert isinstance(failures[0]["error"], User.DoesNotExist)
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_purge_versus_cancellation_race_serializes_and_leaves_no_partial_state(monkeypatch):
    admin = create_admin("purge-vs-cancel-admin")
    target = create_account("purge-vs-cancel-target")
    _schedule_and_make_eligible(target)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock
    call_count = {"n": 0}

    def pausing_lock():
        original_lock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = services.purge_user_account(target=target, actor=admin)
        except Exception as exc:
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_purge = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge attempt never reached the lock"

    cancel_outcome = {}

    def run_cancel():
        try:
            cancel_outcome["result"] = services.cancel_user_deletion(target=target, actor=admin)
        except Exception as exc:
            cancel_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_cancel = _run_in_thread(run_cancel)
    time.sleep(0.3)
    proceed.set()
    thread_purge.join(timeout=5)
    thread_cancel.join(timeout=5)

    if "result" in purge_outcome:
        assert not User.objects.filter(pk=target.pk).exists()
        assert isinstance(cancel_outcome.get("error"), User.DoesNotExist)
    else:
        assert User.objects.filter(pk=target.pk).exists()
        assert "result" in cancel_outcome
        target.refresh_from_db()
        assert target.deletion_scheduled_at is None


@pytest.mark.django_db(transaction=True)
def test_mutual_administrator_purge_race_leaves_exactly_one_active_admin(monkeypatch):
    # "Mutual" purge: admin_a (still active) purges admin_b (already
    # pending deletion, inactive) while, concurrently, admin_b's own
    # account is used as the *actor* attempting to purge admin_a. Since
    # a purge-eligible target must already be inactive, and an inactive
    # account can never pass actor revalidation, admin_b's side is
    # guaranteed to fail via `PurgeActorNotAuthorizedError` regardless of
    # ordering -- the advisory lock must still serialize both attempts
    # without deadlocking, and exactly one active admin (admin_a) must
    # remain afterward.
    admin_a = create_admin("mutual-purge-a")
    admin_b = create_admin("mutual-purge-b")
    _schedule_and_make_eligible(admin_b, actor=admin_a)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock
    call_count = {"n": 0}

    def pausing_lock():
        original_lock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second admin's purge attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    outcome_x = {}

    def purge_b_by_a():
        try:
            outcome_x["result"] = services.purge_user_account(target=admin_b, actor=admin_a)
        except Exception as exc:
            outcome_x["error"] = exc
        finally:
            connections.close_all()

    thread_x = _run_in_thread(purge_b_by_a)
    assert lock_acquired.wait(timeout=5), "first admin's purge attempt never reached the lock"

    outcome_y = {}

    def purge_a_by_b():
        try:
            outcome_y["result"] = services.purge_user_account(target=admin_a, actor=admin_b)
        except Exception as exc:
            outcome_y["error"] = exc
        finally:
            connections.close_all()

    thread_y = _run_in_thread(purge_a_by_b)
    time.sleep(0.3)
    proceed.set()
    thread_x.join(timeout=5)
    thread_y.join(timeout=5)

    assert "result" in outcome_x, outcome_x.get("error")
    assert isinstance(outcome_y.get("error"), services.PurgeActorNotAuthorizedError)
    assert not User.objects.filter(pk=admin_b.pk).exists()
    assert User.objects.filter(role=User.ROLE_ADMIN, is_active=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_actor_demoted_mid_purge_fails_safely(monkeypatch):
    admin = create_admin("mid-race-actor-admin")
    other_admin = create_admin("mid-race-authorizer")
    target = create_account("mid-race-target")
    _schedule_and_make_eligible(target, actor=other_admin)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock

    def pausing_lock():
        original_lock()
        lock_acquired.set()
        assert proceed.wait(timeout=5), "purge attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    outcome = {}

    def run_purge():
        try:
            outcome["result"] = services.purge_user_account(target=target, actor=admin)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            connections.close_all()

    thread = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge attempt never reached the lock"

    with transaction.atomic():
        User.objects.filter(pk=admin.pk).update(role=User.ROLE_USER)

    proceed.set()
    thread.join(timeout=5)

    assert isinstance(outcome.get("error"), services.PurgeActorNotAuthorizedError)
    assert User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_purge_versus_administrator_note_restore_race_leaves_no_partial_state(monkeypatch):
    from notes import services as notes_services

    admin = create_admin("purge-vs-restore-note-admin")
    target = create_account("purge-vs-restore-note-target")
    note = Note.objects.create(
        owner=target,
        title="racing note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        trashed_at=timezone.now() - timedelta(days=1),
    )
    _schedule_and_make_eligible(target)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock

    def pausing_lock():
        original_lock()
        lock_acquired.set()
        assert proceed.wait(timeout=5), "purge attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = services.purge_user_account(target=target, actor=admin)
        except Exception as exc:
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_purge = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge attempt never reached the lock"

    restore_outcome = {}

    def run_restore():
        try:
            restore_outcome["result"] = notes_services.restore_note_for_administrator(
                note_id=note.pk, actor=admin
            )
        except Exception as exc:
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_restore = _run_in_thread(run_restore)
    time.sleep(0.3)
    proceed.set()
    thread_purge.join(timeout=5)
    thread_restore.join(timeout=5)

    assert "result" in purge_outcome, purge_outcome.get("error")
    assert not Note.objects.filter(pk=note.pk).exists()
    if "result" in restore_outcome:
        assert not Note.objects.filter(pk=note.pk).exists()
    else:
        assert "error" in restore_outcome


@pytest.mark.django_db(transaction=True)
def test_purge_versus_administrator_folder_restore_race_leaves_no_partial_state(monkeypatch):
    from notes import services as notes_services

    admin = create_admin("purge-vs-restore-folder-admin")
    target = create_account("purge-vs-restore-folder-target")
    folder = Folder.objects.create(
        owner=target,
        name="racing folder",
        trashed_at=timezone.now() - timedelta(days=1),
    )
    _schedule_and_make_eligible(target)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock

    def pausing_lock():
        original_lock()
        lock_acquired.set()
        assert proceed.wait(timeout=5), "purge attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = services.purge_user_account(target=target, actor=admin)
        except Exception as exc:
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_purge = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge attempt never reached the lock"

    restore_outcome = {}

    def run_restore():
        try:
            restore_outcome["result"] = notes_services.restore_folder_for_administrator(
                folder_id=folder.pk, actor=admin
            )
        except Exception as exc:
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    thread_restore = _run_in_thread(run_restore)
    time.sleep(0.3)
    proceed.set()
    thread_purge.join(timeout=5)
    thread_restore.join(timeout=5)

    assert "result" in purge_outcome, purge_outcome.get("error")
    assert not Folder.objects.filter(pk=folder.pk).exists()
    if "result" in restore_outcome:
        assert not Folder.objects.filter(pk=folder.pk).exists()
    else:
        assert "error" in restore_outcome


@pytest.mark.django_db
def test_pending_owner_excluded_from_ordinary_item_purge_regression():
    from notes import purge as notes_purge

    target = create_account("pending-owner-item-purge-target")
    Note.objects.create(
        owner=target,
        title="pending owner note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        trashed_at=timezone.now() - timedelta(days=365),
    )
    services.schedule_user_deletion(target=target, actor=create_admin("pending-owner-purge-admin"))

    result = notes_purge.run_purge_cycle(dry_run=False, note_batch_size=500, folder_batch_size=500)

    assert Note.objects.filter(owner=target).exists()
    assert result.notes_purged == 0
