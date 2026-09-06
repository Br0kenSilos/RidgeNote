"""Administrator folder restore.

`restore_folder_for_administrator()` restores a single administrator-recoverable
Folder row only: it locks the folder row, re-evaluates eligibility, resolves a
safe top-level name (the original name if still available, otherwise a
deterministic collision-safe `(Restored)` fallback), clears the folder's own
lifecycle fields, and records exactly one audit event -- all inside one outer
transaction. It never queries or mutates any Note row; contained notes keep
their own `folder_id`/`trashed_at`/`emptied_at` exactly as they were,
regardless of their individual lifecycle stage.

Several scenarios here require genuine, separate-connection concurrency to
exercise real PostgreSQL row-locking and unique-constraint behavior, following
the same pattern established by `test_trash_restore_race.py` and
`test_admin_note_restore.py`. Constraint-discrimination branches that are
otherwise only reachable via a rare race are exercised deterministically with
a controlled fake `IntegrityError` carrying the real constraint name.
"""

import threading
import time
from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Folder, Note

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


def create_admin(username="admin", **kwargs):
    return create_account(username, role=User.ROLE_ADMIN, **kwargs)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def trash_and_hide(folder, *, days_ago=45):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))


def trash_and_empty(folder):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())


class _FakeConstraintDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeConstraintCause(Exception):
    def __init__(self, constraint_name):
        self.diag = _FakeConstraintDiag(constraint_name)


def _fake_integrity_error(constraint_name):
    exc = IntegrityError("duplicate key value violates unique constraint")
    exc.__cause__ = _FakeConstraintCause(constraint_name)
    return exc


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- eligibility -------------------------------------------------------------------


@pytest.mark.django_db
def test_administrator_can_restore_age_based_recoverable_folder():
    owner = create_account("frestore-age-owner")
    admin = create_admin("frestore-age-admin")
    folder = services.create_folder(owner=owner, name="Age Based Folder")
    trash_and_hide(folder, days_ago=45)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.folder_id == folder.id
    folder.refresh_from_db()
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_administrator_can_restore_empty_trash_recoverable_folder():
    owner = create_account("frestore-emptied-owner")
    admin = create_admin("frestore-emptied-admin")
    folder = services.create_folder(owner=owner, name="Emptied Folder")
    trash_and_empty(folder)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.folder_id == folder.id
    folder.refresh_from_db()
    assert folder.trashed_at is None
    assert folder.emptied_at is None


@pytest.mark.django_db
def test_active_folder_restore_rejected():
    owner = create_account("frestore-active-owner")
    admin = create_admin("frestore-active-admin")
    folder = services.create_folder(owner=owner, name="Active Folder")

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)


@pytest.mark.django_db
def test_owner_visible_folder_restore_rejected():
    owner = create_account("frestore-owner-visible-owner")
    admin = create_admin("frestore-owner-visible-admin")
    folder = services.create_folder(owner=owner, name="Owner Visible Folder")
    services.move_folder_to_trash(folder=folder)

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)


@pytest.mark.django_db
def test_beyond_90_day_folder_restore_rejected():
    owner = create_account("frestore-too-old-owner")
    admin = create_admin("frestore-too-old-admin")
    folder = services.create_folder(owner=owner, name="Too Old Folder")
    trash_and_hide(folder, days_ago=91)

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)


@pytest.mark.django_db
def test_missing_folder_restore_rejected_safely():
    admin = create_admin("frestore-missing-admin")

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_for_administrator(folder_id=999_999, actor=admin)


@pytest.mark.django_db(transaction=True)
def test_second_administrator_restore_rejected_after_first_succeeds(monkeypatch):
    owner = create_account("frestore-second-admin-owner")
    admin_a = create_admin("frestore-second-admin-a")
    admin_b = create_admin("frestore-second-admin-b")
    folder = services.create_folder(owner=owner, name="Second Admin Folder")
    trash_and_empty(folder)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services._is_hidden_but_administrator_recoverable

    def pausing_check(*, trashed_at, emptied_at, now):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second restore never signaled proceed"
        return original_check(trashed_at=trashed_at, emptied_at=emptied_at, now=now)

    monkeypatch.setattr(services, "_is_hidden_but_administrator_recoverable", pausing_check)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.restore_folder_for_administrator(
                folder_id=folder.id, actor=admin_a
            )
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first restore did not reach the locked eligibility check"

    outcome_b = {}

    def run_b():
        try:
            services.restore_folder_for_administrator(folder_id=folder.id, actor=admin_b)
            outcome_b["ok"] = True
        except services.TrashItemNotRestorableError:
            outcome_b["rejected"] = True
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
    assert outcome_a["result"].folder_id == folder.id
    assert "error" not in outcome_b, outcome_b.get("error")
    assert outcome_b.get("rejected") is True

    assert (
        AuditEvent.objects.filter(
            event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE,
            target_username_snapshot=owner.username,
        ).count()
        == 1
    )


# -- lifecycle ----------------------------------------------------------------------


@pytest.mark.django_db
def test_trashed_at_cleared():
    owner = create_account("frestore-lifecycle-trashed-owner")
    admin = create_admin("frestore-lifecycle-trashed-admin")
    folder = services.create_folder(owner=owner, name="Trashed Clear Folder")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_emptied_at_cleared():
    owner = create_account("frestore-lifecycle-emptied-owner")
    admin = create_admin("frestore-lifecycle-emptied-admin")
    folder = services.create_folder(owner=owner, name="Emptied Clear Folder")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    assert folder.emptied_at is None


@pytest.mark.django_db
def test_marker_remains_false():
    owner = create_account("frestore-marker-owner")
    admin = create_admin("frestore-marker-admin")
    folder = services.create_folder(owner=owner, name="Marker Folder")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    assert folder.is_recovery_folder is False


@pytest.mark.django_db
def test_restored_folder_becomes_owner_visible():
    owner = create_account("frestore-owner-visible-listing-owner")
    admin = create_admin("frestore-owner-visible-listing-admin")
    folder = services.create_folder(owner=owner, name="Owner Visible After Restore")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    active_names = [f.name for f in Folder.objects.filter(owner=owner, trashed_at__isnull=True)]
    assert "Owner Visible After Restore" in active_names


@pytest.mark.django_db
def test_folder_disappears_from_administrator_recovery_listing():
    owner = create_account("frestore-listing-disappear-owner")
    admin = create_admin("frestore-listing-disappear-admin")
    folder = services.create_folder(owner=owner, name="Disappearing Recovery Folder")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    items = services.list_administrator_recoverable_folders()
    assert all(item["title"] != "Disappearing Recovery Folder" for item in items)


# -- contained-note non-mutation ------------------------------------------------------


@pytest.mark.django_db
def test_recoverable_note_becomes_active_again_and_normally_surfaces_after_folder_restore():
    # An active Note may reference only an active Folder
    # or no Folder: `assign_note_folder()` rejects an
    # already-trashed destination, so a Note referencing an
    # already-trashed Folder cannot be constructed through the service
    # layer at all.
    #
    # The guarantee this test exists to prove --
    # restoring a Folder correctly returns one of its own recoverable
    # Notes to fully active, normally-surfaced state, not just correct
    # raw model fields -- remains reachable and is proven here via the
    # legitimate path: assign the Note while the Folder is still
    # active, trash the Folder (which cascades the Note into Trash
    # alongside it, exactly like the sibling test below --
    # `test_administrator_recoverable_marked_note_is_restored_together_with_folder`
    # -- then restore. `notes_grouped_for_tree()` surfacing is this
    # test's own distinct addition beyond that sibling test.
    owner = create_account("frestore-active-note-owner")
    admin = create_admin("frestore-active-note-admin")
    folder = services.create_folder(owner=owner, name="Active Note Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    before_folder_id = note.folder_id

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    note.refresh_from_db()
    assert note.folder_id == before_folder_id == folder.id
    assert note.trashed_at is None
    assert note.emptied_at is None
    folders, unfiled = services.notes_grouped_for_tree(owner=owner)
    restored_folder = next(f for f in folders if f.id == folder.id)
    assert note in list(restored_folder.notes.all())


@pytest.mark.django_db
def test_owner_visible_trashed_note_remains_trashed():
    owner = create_account("frestore-owner-visible-note-owner")
    admin = create_admin("frestore-owner-visible-note-admin")
    folder = services.create_folder(owner=owner, name="Owner Visible Note Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    before_folder_id, before_trashed_at, before_emptied_at = (
        note.folder_id,
        note.trashed_at,
        note.emptied_at,
    )
    assert before_emptied_at is None

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    note.refresh_from_db()
    assert note.folder_id == before_folder_id
    assert note.trashed_at == before_trashed_at
    assert note.emptied_at == before_emptied_at
    assert services.note_is_visible_and_self_restorable(note) is True


@pytest.mark.django_db
def test_administrator_recoverable_marked_note_is_restored_together_with_folder():
    # A note carrying this folder's own trash-cascade
    # marker, and itself administrator-recoverable, is restored
    # together with the folder rather than left trashed.
    owner = create_account("frestore-admin-recoverable-note-owner")
    admin = create_admin("frestore-admin-recoverable-note-admin")
    folder = services.create_folder(owner=owner, name="Admin Recoverable Note Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    before_folder_id = note.folder_id

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    note.refresh_from_db()
    assert note.folder_id == before_folder_id
    assert note.trashed_at is None
    assert note.emptied_at is None
    assert note.trashed_via_folder_id is None
    assert result.restored_note_ids == [note.id]


@pytest.mark.django_db
def test_independently_trashed_note_unchanged():
    owner = create_account("frestore-independent-note-owner")
    admin = create_admin("frestore-independent-note-admin")
    folder = services.create_folder(owner=owner, name="Independent Note Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Independent Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    before_folder_id, before_trashed_at, before_emptied_at = (
        note.folder_id,
        note.trashed_at,
        note.emptied_at,
    )

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    note.refresh_from_db()
    assert note.folder_id == before_folder_id
    assert note.trashed_at == before_trashed_at
    assert note.emptied_at == before_emptied_at


@pytest.mark.django_db
def test_beyond_90_day_note_unchanged():
    owner = create_account("frestore-beyond-90-note-owner")
    admin = create_admin("frestore-beyond-90-note-admin")
    folder = services.create_folder(owner=owner, name="Beyond 90 Note Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=91))
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    before_folder_id, before_trashed_at, before_emptied_at = (
        note.folder_id,
        note.trashed_at,
        note.emptied_at,
    )

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    note.refresh_from_db()
    assert note.folder_id == before_folder_id
    assert note.trashed_at == before_trashed_at
    assert note.emptied_at == before_emptied_at


# -- naming ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_original_name_retained_when_available():
    owner = create_account("frestore-naming-original-owner")
    admin = create_admin("frestore-naming-original-admin")
    folder = services.create_folder(owner=owner, name="Original Name Folder")
    trash_and_empty(folder)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.restored_name == "Original Name Folder"
    assert result.fallback_naming_used is False


@pytest.mark.django_db
def test_case_insensitive_collision_uses_restored_fallback():
    owner = create_account("frestore-naming-collision-owner")
    admin = create_admin("frestore-naming-collision-admin")
    folder = services.create_folder(owner=owner, name="Collision Folder")
    trash_and_empty(folder)
    services.create_folder(owner=owner, name="collision folder")

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.restored_name == "Collision Folder (Restored)"
    assert result.fallback_naming_used is True


@pytest.mark.django_db
def test_multiple_sequential_suffix_collisions_handled():
    owner = create_account("frestore-naming-multi-collision-owner")
    admin = create_admin("frestore-naming-multi-collision-admin")
    folder = services.create_folder(owner=owner, name="Multi Collision Folder")
    trash_and_empty(folder)
    for attempt in (1, 2, 3):
        candidate = services._administrator_folder_restore_name_candidate(
            original_name="Multi Collision Folder", attempt=attempt
        )
        services.create_folder(owner=owner, name=candidate)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.restored_name == "Multi Collision Folder (Restored 3)"


@pytest.mark.django_db
def test_near_255_character_name_truncates_correctly():
    owner = create_account("frestore-naming-truncate-owner")
    admin = create_admin("frestore-naming-truncate-admin")
    long_name = "N" * 250
    folder = services.create_folder(owner=owner, name=long_name)
    trash_and_empty(folder)
    services.create_folder(owner=owner, name=long_name)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert len(result.restored_name) == 255
    assert result.restored_name.endswith(" (Restored)")
    assert result.restored_name == ("N" * 244) + " (Restored)"


@pytest.mark.django_db
def test_naming_exhaustion_raises_narrow_exception(monkeypatch):
    owner = create_account("frestore-naming-exhaustion-owner")
    monkeypatch.setattr(services, "ADMINISTRATOR_FOLDER_RESTORE_NAMING_MAX_ATTEMPTS", 3)
    folder = services.create_folder(owner=owner, name="Exhaustion Folder")
    trash_and_empty(folder)
    for attempt in (1, 2, 3):
        candidate = services._administrator_folder_restore_name_candidate(
            original_name="Exhaustion Folder", attempt=attempt
        )
        services.create_folder(owner=owner, name=candidate)

    with pytest.raises(services.FolderRestoreNamingExhaustedError):
        services.restore_folder_for_administrator(
            folder_id=folder.id, actor=create_admin("frestore-naming-exhaustion-admin")
        )


@pytest.mark.django_db(transaction=True)
def test_two_same_name_folders_restore_concurrently_to_distinct_names(monkeypatch):
    """Genuine cross-transaction race: two different trashed folders (same
    owner, same original name) are restored at nearly the same time. The
    second restore's candidate-name computation is paused just long enough
    for the first restore to fully commit the exact original name; the
    second then hits the real `notes_folder_owner_name_ci_uq` conflict on
    its own save and must retry to the deterministic `(Restored)` fallback
    -- never crash, never lose either folder."""
    owner = create_account("frestore-concurrent-naming-owner")
    admin = create_admin("frestore-concurrent-naming-admin")
    folder_a = services.create_folder(owner=owner, name="Concurrent Naming Folder")
    trash_and_empty(folder_a)
    # The active-folder-name uniqueness constraint only applies while
    # trashed_at is null, so folder_b can only be renamed to match folder_a's
    # name after folder_a is trashed and its name no longer participates.
    folder_b = services.create_folder(owner=owner, name="Concurrent Naming Folder 2")
    Folder.objects.filter(pk=folder_b.pk).update(name="Concurrent Naming Folder")
    trash_and_empty(folder_b)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_candidate = services._administrator_folder_restore_name_candidate

    def pausing_candidate(*, original_name, attempt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "first candidate call never signaled proceed"
        return original_candidate(original_name=original_name, attempt=attempt)

    monkeypatch.setattr(services, "_administrator_folder_restore_name_candidate", pausing_candidate)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.restore_folder_for_administrator(
                folder_id=folder_a.id, actor=admin
            )
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first restore did not reach naming"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.restore_folder_for_administrator(
                folder_id=folder_b.id, actor=admin
            )
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

    names = {outcome_a["result"].restored_name, outcome_b["result"].restored_name}
    assert names == {"Concurrent Naming Folder", "Concurrent Naming Folder (Restored)"}


@pytest.mark.django_db(transaction=True)
def test_owner_create_race_causes_safe_retry(monkeypatch):
    """Genuine cross-transaction race: the owner creates an active folder
    claiming the administrator's selected candidate name while the
    administrator's restore is paused mid-resolution. The restore must
    retry to the fallback name without altering the owner's new folder."""
    owner = create_account("frestore-owner-race-owner")
    admin = create_admin("frestore-owner-race-admin")
    folder = services.create_folder(owner=owner, name="Owner Race Folder")
    trash_and_empty(folder)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_candidate = services._administrator_folder_restore_name_candidate

    def pausing_candidate(*, original_name, attempt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "candidate call never signaled proceed"
        return original_candidate(original_name=original_name, attempt=attempt)

    monkeypatch.setattr(services, "_administrator_folder_restore_name_candidate", pausing_candidate)

    outcome = {}

    def run_restore():
        try:
            outcome["result"] = services.restore_folder_for_administrator(
                folder_id=folder.id, actor=admin
            )
        except Exception as exc:  # pragma: no cover
            outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    assert lock_acquired.wait(timeout=5), "restore did not reach naming"

    owner_outcome = {}

    def run_owner_create():
        try:
            owner_outcome["folder"] = services.create_folder(owner=owner, name="Owner Race Folder")
        except Exception as exc:  # pragma: no cover
            owner_outcome["error"] = exc
        finally:
            connections.close_all()

    owner_thread = _run_in_thread(run_owner_create)
    time.sleep(0.3)

    proceed.set()
    restore_thread.join(timeout=5)
    owner_thread.join(timeout=5)

    assert "error" not in outcome, outcome.get("error")
    assert "error" not in owner_outcome, owner_outcome.get("error")
    assert outcome["result"].restored_name == "Owner Race Folder (Restored)"

    owner_folder = owner_outcome["folder"]
    owner_folder.refresh_from_db()
    assert owner_folder.name == "Owner Race Folder"
    assert owner_folder.trashed_at is None


# -- constraint discrimination ------------------------------------------------------


@pytest.mark.django_db
def test_folder_name_constraint_conflict_retries_next_candidate(monkeypatch):
    owner = create_account("frestore-name-constraint-owner")
    admin = create_admin("frestore-name-constraint-admin")
    folder = services.create_folder(owner=owner, name="Constraint Folder")
    trash_and_empty(folder)

    original_save = Folder.save
    call_count = {"n": 0}

    def flaky_save(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _fake_integrity_error(services.FOLDER_NAME_CONFLICT_CONSTRAINT)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Folder, "save", flaky_save)

    result = services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert result.restored_name == "Constraint Folder (Restored)"
    assert call_count["n"] == 2


@pytest.mark.django_db
def test_unrelated_integrity_error_is_reraised(monkeypatch):
    owner = create_account("frestore-unrelated-constraint-owner")
    admin = create_admin("frestore-unrelated-constraint-admin")
    folder = services.create_folder(owner=owner, name="Unrelated Constraint Folder")
    trash_and_empty(folder)

    def failing_save(self, *args, **kwargs):
        raise _fake_integrity_error("some_unrelated_constraint")

    monkeypatch.setattr(Folder, "save", failing_save)

    with pytest.raises(IntegrityError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    assert folder.trashed_at is not None


# -- audit ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_successful_restore_creates_exactly_one_audit_event():
    owner = create_account("frestore-audit-count-owner")
    admin = create_admin("frestore-audit-count-admin")
    folder = services.create_folder(owner=owner, name="Audit Count Folder")
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE).count()
        == 1
    )


@pytest.mark.django_db
def test_forced_audit_failure_rolls_back_restore(monkeypatch):
    owner = create_account("frestore-audit-rollback-owner")
    admin = create_admin("frestore-audit-rollback-admin")
    folder = services.create_folder(owner=owner, name="Audit Rollback Folder")
    trash_and_empty(folder)

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(services.account_services, "record_audit_event", failing_record_audit_event)

    with pytest.raises(RuntimeError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.emptied_at is not None
    assert folder.name == "Audit Rollback Folder"
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE
    ).exists()


@pytest.mark.django_db
def test_no_audit_event_on_failed_restore():
    admin = create_admin("frestore-audit-no-event-admin")

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_for_administrator(folder_id=999_999, actor=admin)

    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE
    ).exists()


@pytest.mark.django_db
def test_audit_details_contain_required_metadata():
    owner = create_account("frestore-audit-details-owner")
    admin = create_admin("frestore-audit-details-admin")
    folder = services.create_folder(owner=owner, name="Audit Details Folder")
    trash_and_empty(folder)
    folder.refresh_from_db()
    original_trashed_at = folder.trashed_at

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE)
    assert event.actor_id == admin.id
    assert event.target_user_id == owner.id
    assert event.details["folder_id"] == folder.id
    assert event.details["original_name"] == "Audit Details Folder"
    assert event.details["restored_name"] == "Audit Details Folder"
    assert event.details["original_trashed_at"] == original_trashed_at.isoformat()
    assert event.details["emptied_at_cleared"] is True
    assert event.details["fallback_naming_used"] is False


@pytest.mark.django_db
def test_audit_details_exclude_note_and_content_metadata():
    owner = create_account("frestore-audit-content-exclusion-owner")
    admin = create_admin("frestore-audit-content-exclusion-admin")
    folder = services.create_folder(owner=owner, name="Audit Content Exclusion Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="SECRET_NOTE_TITLE_XYZ")
    services.assign_note_folder(note=note, folder=folder)
    trash_and_empty(folder)

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE)
    forbidden_keys = {
        "note_count",
        "note_id",
        "note_ids",
        "note_titles",
        "body_json",
        "tags",
        "pinned",
    }
    assert forbidden_keys.isdisjoint(event.details.keys())
    assert "SECRET_NOTE_TITLE_XYZ" not in str(event.details)


# -- permissions and HTTP behavior -------------------------------------------------


@pytest.mark.django_db
def test_anonymous_denied():
    owner = create_account("frestore-http-anon-owner")
    folder = services.create_folder(owner=owner, name="Http Anon Folder")
    trash_and_empty(folder)

    response = Client().post(reverse("notes:admin_folder_restore", args=[folder.id]))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
    folder.refresh_from_db()
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_inactive_denied():
    owner = create_account("frestore-http-inactive-owner")
    inactive_admin = create_admin("frestore-http-inactive-admin", is_active=False)
    folder = services.create_folder(owner=owner, name="Http Inactive Folder")
    trash_and_empty(folder)

    response = authenticated_client(inactive_admin).post(
        reverse("notes:admin_folder_restore", args=[folder.id])
    )

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_active_non_admin_denied():
    owner = create_account("frestore-http-non-admin-owner")
    plain_user = create_account("frestore-http-non-admin-user")
    folder = services.create_folder(owner=owner, name="Http Non Admin Folder")
    trash_and_empty(folder)

    response = authenticated_client(plain_user).post(
        reverse("notes:admin_folder_restore", args=[folder.id])
    )

    assert response.status_code == 403
    folder.refresh_from_db()
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_administrator_can_restore_across_owners():
    owner = create_account("frestore-http-cross-owner-owner")
    admin = create_admin("frestore-http-cross-owner-admin")
    folder = services.create_folder(owner=owner, name="Http Cross Owner Folder")
    trash_and_empty(folder)

    response = authenticated_client(admin).post(
        reverse("notes:admin_folder_restore", args=[folder.id])
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery")
    folder.refresh_from_db()
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_get_rejected():
    owner = create_account("frestore-http-get-owner")
    admin = create_admin("frestore-http-get-admin")
    folder = services.create_folder(owner=owner, name="Http Get Folder")
    trash_and_empty(folder)

    response = authenticated_client(admin).get(
        reverse("notes:admin_folder_restore", args=[folder.id])
    )

    assert response.status_code == 405
    folder.refresh_from_db()
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_recovery_table_folder_restore_form_includes_csrf_token():
    owner = create_account("frestore-http-csrf-owner")
    admin = create_admin("frestore-http-csrf-admin")
    folder = services.create_folder(owner=owner, name="Http Csrf Folder")
    trash_and_empty(folder)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    # The Restore link is a dialog-opening
    # button (`data-restore-trigger`) carrying its target route as a
    # `data-restore-action` attribute instead of a form `action` -- the
    # CSRF token lives in the one shared dialog form in
    # `base.html`, present on every page regardless of row count.
    assert f'data-restore-action="/admin/recovery/folders/{folder.id}/restore/"' in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_success_message_states_no_trashed_notes_were_restored_when_none_are_marked():
    # This folder has no notes at all, so
    # the message states the actual (zero) restored count.
    owner = create_account("frestore-http-message-owner")
    admin = create_admin("frestore-http-message-admin")
    folder = services.create_folder(owner=owner, name="Http Message Folder")
    trash_and_empty(folder)

    response = authenticated_client(admin).post(
        reverse("notes:admin_folder_restore", args=[folder.id]), follow=True
    )
    content = response.content.decode()

    assert "Folder restored" in content
    assert "No trashed notes were restored" in content


@pytest.mark.django_db
def test_success_message_states_actual_restored_note_count():
    owner = create_account("frestore-http-message-count-owner")
    admin = create_admin("frestore-http-message-count-admin")
    folder = services.create_folder(owner=owner, name="Http Message Count Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_and_empty(folder)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).post(
        reverse("notes:admin_folder_restore", args=[folder.id]), follow=True
    )
    content = response.content.decode()

    assert "Folder restored" in content
    assert "1 trashed note was also restored" in content


@pytest.mark.django_db
def test_stale_double_post_handled_safely():
    owner = create_account("frestore-http-double-owner")
    admin = create_admin("frestore-http-double-admin")
    folder = services.create_folder(owner=owner, name="Http Double Folder")
    trash_and_empty(folder)

    client = authenticated_client(admin)
    first_response = client.post(reverse("notes:admin_folder_restore", args=[folder.id]))
    second_response = client.post(reverse("notes:admin_folder_restore", args=[folder.id]))

    assert first_response.status_code == 302
    assert second_response.status_code == 302

    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE).count()
        == 1
    )
