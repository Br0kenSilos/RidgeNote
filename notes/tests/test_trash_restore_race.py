"""Owner restore concurrency.

`restore_note_from_trash()`/`restore_folder_from_trash()` perform their
complete lifecycle read, eligibility check, and write inside one
`transaction.atomic()` block using `select_for_update()`, matching the
project's existing `save_note()` pattern.

Row-level locking only has teeth across genuinely separate database
transactions/connections -- a single connection never blocks on a lock it
already holds itself, so a same-connection monkeypatch-based interleaving
(as used to reproduce the original defect) cannot demonstrate that the fix
actually serializes concurrent access. The "restore wins" tests below
therefore use real background threads (each with its own Django database
connection) synchronized with `threading.Event`s, so the row lock's real
Postgres semantics are genuinely exercised. The "Empty Trash wins first"
tests are plain sequential tests: once Empty Trash has committed before
restore's locked read even begins, the observable outcome is identical to
the case where restore's lock acquisition blocks on an in-flight Empty
Trash transaction and only proceeds after it commits -- Postgres guarantees
the locked read sees the same state either way, so a sequential test is a
faithful, non-flaky way to cover that direction.
"""

import threading
import time

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connections
from django.utils import timezone

from notes import services
from notes.models import Note

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


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- genuine cross-transaction race: restore wins ----------------------------


@pytest.mark.django_db(transaction=True)
def test_note_restore_wins_race_against_concurrent_empty_trash(monkeypatch):
    """Deterministically interleaves the real restore and real Empty Trash
    services across two genuinely separate database connections/threads,
    pausing restore immediately after it acquires its row lock (via
    select_for_update) but before it evaluates eligibility or writes.

    While restore holds the lock, a real, concurrent empty_trash_for_owner()
    call is issued from a second thread/connection -- it must block on the
    locked row until restore's transaction commits. Once restore commits
    (trashed_at cleared), Empty Trash's own eligibility filter no longer
    matches this row, so it must not transition it. The note must end active
    with emptied_at still None.
    """
    owner = create_account("restore-race-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Restore Race Note")
    services.move_note_to_trash(note=note)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "empty trash thread never signaled proceed"
        return original_check(note_arg)

    monkeypatch.setattr(services, "note_is_visible_and_self_restorable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_from_trash(note=note)
            restore_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)

    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    empty_trash_outcome = {}

    def run_empty_trash():
        try:
            empty_trash_outcome["counts"] = services.empty_trash_for_owner(owner=owner)
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            empty_trash_outcome["error"] = exc
        finally:
            connections.close_all()

    empty_trash_thread = _run_in_thread(run_empty_trash)

    # Not the synchronization mechanism itself (the Events and the real
    # Postgres row lock are) -- only a best-effort scheduling nudge to give
    # Empty Trash's blocking DB call time to actually reach Postgres and
    # attempt to lock the row before we let restore finish and commit.
    time.sleep(0.3)

    proceed.set()
    restore_thread.join(timeout=5)
    empty_trash_thread.join(timeout=5)

    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert restore_outcome.get("ok") is True
    assert "error" not in empty_trash_outcome, empty_trash_outcome.get("error")

    note.refresh_from_db()
    assert note.trashed_at is None, f"expected trashed_at is None, got {note.trashed_at!r}"
    assert note.emptied_at is None, (
        "invariant violated: note ended active but retained a stale non-null "
        f"emptied_at={note.emptied_at!r}"
    )


@pytest.mark.django_db(transaction=True)
def test_folder_restore_wins_race_against_concurrent_empty_trash(monkeypatch):
    """Folder equivalent of the note race test above. Also confirms an
    unrelated, already-active note that still references the folder is
    unaffected by the concurrent Empty Trash call. The active-note state is
    constructed directly (bypassing `restore_note_from_trash()`, which
    never produces this state itself,
    and bypassing `assign_note_folder()`, which also
    rejects it) since this test's own subject is the
    folder-restore/Empty-Trash race, not owner-restore or move destination
    behavior."""
    owner = create_account("restore-race-folder-owner")
    folder = services.create_folder(owner=owner, name="Restore Race Folder")
    unrelated_active_note = services.create_note(owner=owner)
    services.assign_note_folder(note=unrelated_active_note, folder=folder)

    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=unrelated_active_note.pk).update(trashed_at=None)
    unrelated_active_note.refresh_from_db()
    assert unrelated_active_note.trashed_at is None
    assert unrelated_active_note.folder_id == folder.id

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.folder_is_visible_and_self_restorable

    def pausing_check(folder_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "empty trash thread never signaled proceed"
        return original_check(folder_arg)

    monkeypatch.setattr(services, "folder_is_visible_and_self_restorable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_folder_from_trash(folder=folder)
            restore_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)

    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    empty_trash_outcome = {}

    def run_empty_trash():
        try:
            empty_trash_outcome["counts"] = services.empty_trash_for_owner(owner=owner)
        except Exception as exc:  # pragma: no cover
            empty_trash_outcome["error"] = exc
        finally:
            connections.close_all()

    empty_trash_thread = _run_in_thread(run_empty_trash)

    time.sleep(0.3)

    proceed.set()
    restore_thread.join(timeout=5)
    empty_trash_thread.join(timeout=5)

    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert restore_outcome.get("ok") is True
    assert "error" not in empty_trash_outcome, empty_trash_outcome.get("error")

    folder.refresh_from_db()
    assert folder.trashed_at is None, f"expected trashed_at is None, got {folder.trashed_at!r}"
    assert folder.emptied_at is None, (
        "invariant violated: folder ended active but retained a stale non-null "
        f"emptied_at={folder.emptied_at!r}"
    )

    unrelated_active_note.refresh_from_db()
    assert unrelated_active_note.trashed_at is None
    assert unrelated_active_note.emptied_at is None


# -- Empty Trash wins first (sequential -- see module docstring) -------------


@pytest.mark.django_db
def test_note_restore_rejected_when_already_emptied_by_prior_empty_trash():
    owner = create_account("restore-race-note-emptied-first-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Emptied First Note")
    services.move_note_to_trash(note=note)

    services.empty_trash_for_owner(owner=owner)
    note.refresh_from_db()
    assert note.trashed_at is not None
    assert note.emptied_at is not None

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_from_trash(note=note)

    note.refresh_from_db()
    assert note.trashed_at is not None
    assert note.emptied_at is not None


@pytest.mark.django_db
def test_folder_restore_rejected_when_already_emptied_by_prior_empty_trash():
    owner = create_account("restore-race-folder-emptied-first-owner")
    folder = services.create_folder(owner=owner, name="Emptied First Folder")
    services.move_folder_to_trash(folder=folder)

    services.empty_trash_for_owner(owner=owner)
    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.emptied_at is not None

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.emptied_at is not None


# -- normal, non-racing controls ----------------------------------------------


@pytest.mark.django_db
def test_note_restore_without_interleaving_leaves_clean_state():
    owner = create_account("restore-race-note-control-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Control Note")
    services.move_note_to_trash(note=note)

    services.restore_note_from_trash(note=note)

    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.emptied_at is None


@pytest.mark.django_db
def test_folder_restore_without_interleaving_leaves_clean_state():
    owner = create_account("restore-race-folder-control-owner")
    folder = services.create_folder(owner=owner, name="Restore Race Control Folder")
    services.move_folder_to_trash(folder=folder)

    services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is None
    assert folder.emptied_at is None


@pytest.mark.django_db
def test_note_restore_returns_synchronized_caller_instance():
    """The caller-provided instance must reflect committed state afterward,
    not just the internally locked instance used for the check/write."""
    owner = create_account("restore-race-note-sync-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Sync Instance Note")
    services.move_note_to_trash(note=note)

    stale_reference = Note.objects.get(pk=note.pk)  # separate Python object, same row
    returned = services.restore_note_from_trash(note=stale_reference)

    assert returned is stale_reference
    assert stale_reference.trashed_at is None
