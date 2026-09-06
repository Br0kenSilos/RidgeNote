"""Account deletion state and recovery foundation.

Covers the three `User` fields and their database constraints, the
`schedule_user_deletion()`/`cancel_user_deletion()`/`user_deletion_status()`
service layer, the last-active-administrator protection, and the
two audit events. Real PostgreSQL concurrency scenarios (genuinely
separate connections/threads) are isolated at the bottom of this file,
following the same technique established by `notes/tests/test_purge_race.py`.
Permanent account purge (`purge_user_account()`) is out of scope here --
see `accounts/tests/test_account_purge.py`.
"""

import threading
import time
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from accounts import services
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"


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


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- Model and constraints ----------------------------------------------


@pytest.mark.django_db
def test_new_user_has_all_deletion_fields_null():
    user = create_account("fresh-user")
    assert user.deletion_scheduled_at is None
    assert user.deletion_recovery_deadline is None
    assert user.deletion_restore_is_active is None


@pytest.mark.django_db
def test_scheduled_at_without_deadline_violates_constraint():
    user = create_account("only-scheduled-at")
    user.is_active = False
    user.deletion_scheduled_at = timezone.now()
    user.deletion_restore_is_active = True
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save()


@pytest.mark.django_db
def test_deadline_without_scheduled_at_violates_constraint():
    user = create_account("only-deadline")
    user.is_active = False
    user.deletion_recovery_deadline = timezone.now() + timedelta(days=7)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save()


@pytest.mark.django_db
def test_pending_deletion_requires_inactive_account():
    user = create_account("active-but-pending")
    now = timezone.now()
    user.deletion_scheduled_at = now
    user.deletion_recovery_deadline = now + timedelta(days=7)
    user.deletion_restore_is_active = True
    # `is_active` remains True -- violates the pending-implies-inactive rule.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save()


@pytest.mark.django_db
def test_restore_flag_without_pending_deletion_violates_constraint():
    user = create_account("stray-restore-flag")
    user.deletion_restore_is_active = True
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save()


@pytest.mark.django_db
def test_pending_deletion_requires_restore_flag():
    user = create_account("missing-restore-flag")
    now = timezone.now()
    user.is_active = False
    user.deletion_scheduled_at = now
    user.deletion_recovery_deadline = now + timedelta(days=7)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save()


@pytest.mark.django_db
def test_valid_previously_active_pending_state_saves():
    user = create_account("valid-previously-active")
    now = timezone.now()
    user.is_active = False
    user.deletion_scheduled_at = now
    user.deletion_recovery_deadline = now + timedelta(days=7)
    user.deletion_restore_is_active = True
    user.save()
    user.refresh_from_db()
    assert user.deletion_restore_is_active is True


@pytest.mark.django_db
def test_valid_previously_disabled_pending_state_saves():
    user = create_account("valid-previously-disabled")
    now = timezone.now()
    user.is_active = False
    user.deletion_scheduled_at = now
    user.deletion_recovery_deadline = now + timedelta(days=7)
    user.deletion_restore_is_active = False
    user.save()
    user.refresh_from_db()
    assert user.deletion_restore_is_active is False


# -- Scheduling ------------------------------------------------------------


@pytest.mark.django_db
def test_schedule_deletion_of_active_account():
    admin = create_admin()
    user = create_account("schedule-active")
    before = timezone.now()

    result = services.schedule_user_deletion(target=user, actor=admin)

    result.refresh_from_db()
    assert result.is_active is False
    assert result.deletion_restore_is_active is True
    assert result.deletion_scheduled_at is not None
    assert result.deletion_scheduled_at >= before
    assert result.deletion_recovery_deadline == (
        result.deletion_scheduled_at + services.ACCOUNT_DELETION_RECOVERY_PERIOD
    )


@pytest.mark.django_db
def test_schedule_deletion_of_active_account_increments_session_generation():
    admin = create_admin()
    user = create_account("schedule-session-gen")
    starting_generation = user.session_generation

    result = services.schedule_user_deletion(target=user, actor=admin)

    assert result.session_generation == starting_generation + 1


@pytest.mark.django_db
def test_schedule_deletion_of_active_account_sets_fresh_disabled_at():
    admin = create_admin()
    user = create_account("schedule-disabled-at")
    assert user.disabled_at is None

    result = services.schedule_user_deletion(target=user, actor=admin)

    assert result.disabled_at is not None


@pytest.mark.django_db
def test_schedule_deletion_of_already_disabled_account():
    admin = create_admin()
    original_disabled_at = timezone.now() - timedelta(days=3)
    user = create_account(
        "schedule-already-disabled", is_active=False, disabled_at=original_disabled_at
    )
    starting_generation = user.session_generation

    result = services.schedule_user_deletion(target=user, actor=admin)

    assert result.is_active is False
    assert result.deletion_restore_is_active is False
    # Prior disabled timestamp is preserved, not overwritten.
    assert result.disabled_at == original_disabled_at
    # No live session existed to revoke, so no generation bump either.
    assert result.session_generation == starting_generation


@pytest.mark.django_db
def test_schedule_deletion_rejects_already_pending_account():
    admin = create_admin()
    user = create_account("already-pending")
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()
    original_deadline = user.deletion_recovery_deadline

    with pytest.raises(services.AlreadyPendingDeletionError):
        services.schedule_user_deletion(target=user, actor=admin)

    user.refresh_from_db()
    assert user.deletion_recovery_deadline == original_deadline


@pytest.mark.django_db
def test_schedule_deletion_deadline_is_exactly_seven_days_later():
    admin = create_admin()
    user = create_account("exact-deadline")

    result = services.schedule_user_deletion(target=user, actor=admin)

    assert result.deletion_recovery_deadline - result.deletion_scheduled_at == timedelta(days=7)


@pytest.mark.django_db
def test_schedule_deletion_leaves_owned_notes_folders_tags_unchanged():
    from notes import services as note_services
    from notes.models import Folder, Note, Tag

    admin = create_admin()
    user = create_account("schedule-owned-data")
    folder = note_services.create_folder(owner=user, name="Kept Folder")
    note = note_services.create_note(owner=user)
    note_services.assign_note_folder(note=note, folder=folder)
    tag = note_services.get_or_create_tag(owner=user, name="kept-tag", color="slate")
    note_services.assign_tag_to_note(note=note, tag=tag)
    note_services.move_note_to_trash(note=note)

    services.schedule_user_deletion(target=user, actor=admin)

    assert Note.objects.filter(pk=note.pk, owner=user).exists()
    assert Folder.objects.filter(pk=folder.pk, owner=user).exists()
    assert Tag.objects.filter(pk=tag.pk, owner=user).exists()
    note.refresh_from_db()
    assert note.trashed_at is not None
    folder.refresh_from_db()
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_schedule_deletion_emits_scheduled_audit_event():
    admin = create_admin()
    user = create_account("scheduled-audit")

    services.schedule_user_deletion(target=user, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ACCOUNT_DELETION_SCHEDULED)
    assert event.actor == admin
    assert event.target_user == user
    assert event.details["was_active"] is True


@pytest.mark.django_db
def test_schedule_deletion_rejects_last_active_admin():
    admin = create_admin("solo-admin")

    with pytest.raises(services.LastActiveAdminError):
        services.schedule_user_deletion(target=admin, actor=admin)

    admin.refresh_from_db()
    assert admin.is_active is True
    assert admin.deletion_scheduled_at is None


@pytest.mark.django_db
def test_schedule_deletion_allowed_when_another_active_admin_remains():
    actor = create_admin("actor-admin")
    other_admin = create_admin("other-admin")

    result = services.schedule_user_deletion(target=other_admin, actor=actor)

    assert result.is_active is False
    assert result.deletion_scheduled_at is not None


# -- Cancellation ------------------------------------------------------------


@pytest.mark.django_db
def test_cancel_deletion_before_deadline_restores_active():
    admin = create_admin()
    user = create_account("cancel-before-deadline")
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()

    result = services.cancel_user_deletion(target=user, actor=admin)

    assert result.is_active is True
    assert result.disabled_at is None
    assert result.deletion_scheduled_at is None
    assert result.deletion_recovery_deadline is None
    assert result.deletion_restore_is_active is None


@pytest.mark.django_db
def test_cancel_deletion_after_deadline_restores_active():
    admin = create_admin()
    user = create_account("cancel-after-deadline")
    services.schedule_user_deletion(target=user, actor=admin)
    User.objects.filter(pk=user.pk).update(
        deletion_scheduled_at=timezone.now() - timedelta(days=30),
        deletion_recovery_deadline=timezone.now() - timedelta(days=23),
    )
    user.refresh_from_db()
    assert services.user_deletion_status(target=user).is_purge_eligible is True

    result = services.cancel_user_deletion(target=user, actor=admin)

    assert result.is_active is True
    assert result.deletion_scheduled_at is None


@pytest.mark.django_db
def test_cancel_deletion_of_previously_disabled_account_remains_disabled():
    admin = create_admin()
    original_disabled_at = timezone.now() - timedelta(days=10)
    user = create_account(
        "cancel-remains-disabled", is_active=False, disabled_at=original_disabled_at
    )
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()

    result = services.cancel_user_deletion(target=user, actor=admin)

    assert result.is_active is False
    # The original disable timestamp survives untouched throughout.
    assert result.disabled_at == original_disabled_at
    assert result.deletion_scheduled_at is None
    assert result.deletion_restore_is_active is None


@pytest.mark.django_db
def test_cancel_deletion_does_not_restore_old_sessions():
    admin = create_admin()
    user = create_account("cancel-no-old-sessions")
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()
    generation_while_pending = user.session_generation

    result = services.cancel_user_deletion(target=user, actor=admin)

    assert result.session_generation == generation_while_pending


@pytest.mark.django_db
def test_cancel_deletion_leaves_owned_data_unchanged():
    from notes import services as note_services
    from notes.models import Note

    admin = create_admin()
    user = create_account("cancel-owned-data")
    note = note_services.create_note(owner=user)
    note_services.rename_note(note=note, title="Owned Data Note")
    note_services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    services.schedule_user_deletion(target=user, actor=admin)
    services.cancel_user_deletion(target=user, actor=admin)

    note.refresh_from_db()
    assert Note.objects.filter(pk=note.pk).exists()
    assert note.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_cancel_deletion_emits_cancelled_audit_event():
    admin = create_admin()
    user = create_account("cancelled-audit")
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()

    services.cancel_user_deletion(target=user, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ACCOUNT_DELETION_CANCELLED)
    assert event.actor == admin
    assert event.target_user == user
    assert event.details["restored_active"] is True


@pytest.mark.django_db
def test_cancel_deletion_rejects_non_pending_account():
    admin = create_admin()
    user = create_account("not-pending")

    with pytest.raises(services.NotPendingDeletionError):
        services.cancel_user_deletion(target=user, actor=admin)


# -- Status helper -----------------------------------------------------------


@pytest.mark.django_db
def test_status_of_active_account():
    user = create_account("status-active")

    status = services.user_deletion_status(target=user)

    assert status.is_pending is False
    assert status.scheduled_at is None
    assert status.recovery_deadline is None
    assert status.is_purge_eligible is False
    assert status.restore_is_active is None


@pytest.mark.django_db
def test_status_of_ordinarily_disabled_account():
    user = create_account("status-disabled", is_active=False, disabled_at=timezone.now())

    status = services.user_deletion_status(target=user)

    assert status.is_pending is False
    assert status.is_purge_eligible is False


@pytest.mark.django_db
def test_status_pending_before_deadline():
    admin = create_admin()
    user = create_account("status-pending-before")

    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()
    status = services.user_deletion_status(target=user)

    assert status.is_pending is True
    assert status.is_purge_eligible is False
    assert status.restore_is_active is True


@pytest.mark.django_db
def test_status_pending_after_deadline_is_purge_eligible():
    admin = create_admin()
    user = create_account("status-pending-after")
    services.schedule_user_deletion(target=user, actor=admin)
    User.objects.filter(pk=user.pk).update(
        deletion_scheduled_at=timezone.now() - timedelta(days=10),
        deletion_recovery_deadline=timezone.now() - timedelta(days=3),
    )
    user.refresh_from_db()

    status = services.user_deletion_status(target=user)

    assert status.is_pending is True
    assert status.is_purge_eligible is True


@pytest.mark.django_db
def test_status_does_not_mutate_or_require_refresh():
    admin = create_admin()
    user = create_account("status-no-mutation")
    services.schedule_user_deletion(target=user, actor=admin)
    user.refresh_from_db()

    services.user_deletion_status(target=user)
    user_after = User.objects.get(pk=user.pk)

    assert user_after.deletion_scheduled_at == user.deletion_scheduled_at


# -- Real PostgreSQL concurrency ---------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_schedule_attempts_serialize_and_second_is_rejected(monkeypatch):
    admin = create_admin("concurrent-schedule-admin")
    user = create_account("concurrent-schedule-target")

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock
    call_count = {"n": 0}

    def pausing_lock():
        original_lock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second scheduling attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.schedule_user_deletion(target=user, actor=admin)
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first scheduling attempt never reached the lock"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.schedule_user_deletion(target=user, actor=admin)
        except Exception as exc:
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)
    proceed.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert "error" not in outcome_a, outcome_a.get("error")
    assert isinstance(outcome_b.get("error"), services.AlreadyPendingDeletionError)
    assert (
        AuditEvent.objects.filter(
            event_type=AuditEvent.EVENT_ACCOUNT_DELETION_SCHEDULED, target_user=user
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_scheduling_last_admin_race_never_leaves_zero_active_admins(monkeypatch):
    """Two admins concurrently try to schedule deletion for each other. The
    administrator-operation advisory lock must serialize both attempts so
    that whichever transaction commits first is correctly seen by the
    second -- the deployment must never end up with zero active
    administrators."""
    admin_x = create_admin("race-admin-x")
    admin_y = create_admin("race-admin-y")

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_lock = services.acquire_admin_operation_lock
    call_count = {"n": 0}

    def pausing_lock():
        original_lock()
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second admin's scheduling attempt never proceeded"

    monkeypatch.setattr(services, "acquire_admin_operation_lock", pausing_lock)

    outcome_x = {}

    def schedule_y_by_x():
        try:
            outcome_x["result"] = services.schedule_user_deletion(target=admin_y, actor=admin_x)
        except Exception as exc:
            outcome_x["error"] = exc
        finally:
            connections.close_all()

    thread_x = _run_in_thread(schedule_y_by_x)
    assert lock_acquired.wait(timeout=5), "first admin's scheduling attempt never reached the lock"

    outcome_y = {}

    def schedule_x_by_y():
        try:
            outcome_y["result"] = services.schedule_user_deletion(target=admin_x, actor=admin_y)
        except Exception as exc:
            outcome_y["error"] = exc
        finally:
            connections.close_all()

    thread_y = _run_in_thread(schedule_x_by_y)
    time.sleep(0.3)
    proceed.set()
    thread_x.join(timeout=5)
    thread_y.join(timeout=5)

    # Exactly one of the two scheduling attempts must succeed; the other
    # must be rejected because it would otherwise leave zero active admins.
    successes = [o for o in (outcome_x, outcome_y) if "error" not in o]
    failures = [o for o in (outcome_x, outcome_y) if "error" in o]
    assert len(successes) == 1, (outcome_x, outcome_y)
    assert len(failures) == 1, (outcome_x, outcome_y)
    assert isinstance(failures[0]["error"], services.LastActiveAdminError)
    assert services.active_admin_count() == 1
