"""Real concurrency proof that ACTIVE NOTE ->
TRASHED FOLDER can never commit, for the two paths that can newly
create it (`assign_note_folder()`, `create_note()`) racing
`move_folder_to_trash()`.

Follows the exact established pattern from `test_trash_restore_race.py`:
genuinely separate database connections/threads, `threading.Event`
synchronization, and a real PostgreSQL row lock (never a mock) as the
actual correctness mechanism -- the brief `time.sleep()` calls below are
only a best-effort scheduling nudge to give the second thread's blocking
DB call time to actually reach Postgres and attempt the lock, exactly as
documented in the existing race tests; they are never what proves
correctness.

The reverse interleaving for both paths (the folder is already fully
trashed and committed before the move/create even begins) needs no
thread at all -- it is already covered by the plain sequential tests in
`notes/tests/test_folders.py`
(`test_assign_note_folder_rejects_an_already_trashed_destination`,
`test_create_note_rejects_an_already_trashed_folder`).
"""

from __future__ import annotations

import threading
import time

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connections
from django.utils import timezone

from notes import services

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
        username=username, password=password, role=role, **kwargs
    )


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


@pytest.mark.django_db(transaction=True)
def test_move_wins_folder_lock_then_folder_trash_also_transitions_the_moved_note(monkeypatch):
    """Move acquires the destination folder's lock first (paused there,
    still holding it, via a real transaction on its own connection).
    While it holds the lock, a real, concurrent `move_folder_to_trash()`
    call is issued from a second thread/connection -- it must block on
    the locked folder row until the move commits. Once the move commits
    (folder still active at that point, so the move succeeds and the
    note is now in the folder), folder-trash proceeds and its own
    associated-note cascade must catch the just-moved note too. The
    final state must never be an active note referencing a trashed
    folder."""
    owner = create_account("move-trash-race-move-wins-owner")
    folder = services.create_folder(owner=owner, name="Destination")
    note = services.create_note(owner=owner)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_assert = services._assert_not_trashed

    def pausing_assert(item):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "folder-trash thread never signaled proceed"
        return original_assert(item)

    monkeypatch.setattr(services, "_assert_not_trashed", pausing_assert)

    move_outcome = {}

    def run_move():
        try:
            services.assign_note_folder(note=note, folder=folder)
            move_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            move_outcome["error"] = exc
        finally:
            connections.close_all()

    move_thread = _run_in_thread(run_move)

    assert lock_acquired.wait(timeout=5), "move did not reach the locked eligibility check"

    trash_outcome = {}

    def run_trash():
        try:
            services.move_folder_to_trash(folder=folder)
            trash_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            trash_outcome["error"] = exc
        finally:
            connections.close_all()

    trash_thread = _run_in_thread(run_trash)

    # Not the synchronization mechanism itself (the Event and the real
    # Postgres row lock are) -- only a best-effort scheduling nudge to
    # give folder-trash's blocking DB call time to actually reach
    # Postgres and attempt to lock the folder row before we let move
    # finish and commit.
    time.sleep(0.3)

    proceed.set()
    move_thread.join(timeout=5)
    trash_thread.join(timeout=5)

    assert "error" not in move_outcome, move_outcome.get("error")
    assert move_outcome.get("ok") is True
    assert "error" not in trash_outcome, trash_outcome.get("error")
    assert trash_outcome.get("ok") is True

    note.refresh_from_db()
    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert note.folder_id == folder.id
    assert note.trashed_at is not None, (
        "invariant violated: note ended active while referencing a trashed folder"
    )


@pytest.mark.django_db(transaction=True)
def test_create_wins_folder_lock_then_folder_trash_also_transitions_the_new_note(monkeypatch):
    """Create acquires the destination folder's lock first (paused there
    via a real transaction on its own connection, immediately after the
    folder lock resolves but before the note insert). While it holds the
    lock, a real, concurrent `move_folder_to_trash()` call must block on
    the locked folder row until create commits. Once create commits (the
    folder was still active at lock time, so creation succeeds), the
    just-created note is a real row in the folder, and folder-trash's
    cascade -- which only runs after it acquires the lock create just
    released -- must catch it too."""
    owner = create_account("create-trash-race-create-wins-owner")
    folder = services.create_folder(owner=owner, name="Destination")

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_canonical_empty_document = services.documents.canonical_empty_document

    def pausing_canonical_empty_document():
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "folder-trash thread never signaled proceed"
        return original_canonical_empty_document()

    monkeypatch.setattr(
        services.documents, "canonical_empty_document", pausing_canonical_empty_document
    )

    create_outcome = {}

    def run_create():
        try:
            create_outcome["note"] = services.create_note(owner=owner, folder=folder)
            create_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            create_outcome["error"] = exc
        finally:
            connections.close_all()

    create_thread = _run_in_thread(run_create)

    assert lock_acquired.wait(timeout=5), "create did not reach the locked folder validation"

    trash_outcome = {}

    def run_trash():
        try:
            services.move_folder_to_trash(folder=folder)
            trash_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion
            trash_outcome["error"] = exc
        finally:
            connections.close_all()

    trash_thread = _run_in_thread(run_trash)

    # Same best-effort scheduling nudge as above -- not the correctness
    # mechanism itself.
    time.sleep(0.3)

    proceed.set()
    create_thread.join(timeout=5)
    trash_thread.join(timeout=5)

    assert "error" not in create_outcome, create_outcome.get("error")
    assert create_outcome.get("ok") is True
    assert "error" not in trash_outcome, trash_outcome.get("error")
    assert trash_outcome.get("ok") is True

    new_note = create_outcome["note"]
    new_note.refresh_from_db()
    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert new_note.folder_id == folder.id
    assert new_note.trashed_at is not None, (
        "invariant violated: note ended active while referencing a trashed folder"
    )
