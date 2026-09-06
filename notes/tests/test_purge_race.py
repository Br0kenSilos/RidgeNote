"""One-shot final purge, PostgreSQL concurrency.

Row-level locking (`select_for_update()`) and the session-level advisory
lock (`pg_try_advisory_lock`/`pg_advisory_unlock`) only have real teeth
across genuinely separate database connections -- a single connection
never blocks on a lock it already holds. Every scenario below therefore
uses real background threads, each with its own Django database
connection, synchronized with `threading.Event`s and a short bounded
`time.sleep()` scheduling nudge, following the same technique established
by `test_trash_restore_race.py` and `test_admin_note_restore.py`.
"""

import threading
import time
from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.utils import timezone

import notes.purge as purge_module
from notes import documents, services
from notes.models import Folder, Note
from notes.purge import PURGE_CYCLE_LOCK_ID, run_purge_cycle

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


def _rename_if_still_placeholder(note):
    # Only rename if the caller left the note as a genuinely untouched
    # placeholder -- a caller that already gave it a meaningful title (to
    # assert on later) must not have that title silently overwritten here.
    if note.title == documents.generated_title_for_timestamp(note.created_at):
        services.rename_note(note=note, title="Recoverable Note")


def trash_note_days_ago(note, *, days_ago):
    _rename_if_still_placeholder(note)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    note.refresh_from_db()
    return note


def trash_and_empty_note(note):
    _rename_if_still_placeholder(note)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    return note


def trash_folder_days_ago(folder, *, days_ago):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    folder.refresh_from_db()
    return folder


def trash_and_empty_folder(folder):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    folder.refresh_from_db()
    return folder


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


def _pausing_purge_eligibility_check(*, lock_acquired: threading.Event, proceed: threading.Event):
    original = purge_module._is_purge_eligible
    call_count = {"n": 0}

    def pausing(*, trashed_at, cutoff):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "purge thread never signaled proceed"
        return original(trashed_at=trashed_at, cutoff=cutoff)

    return pausing


# -- owner note restore vs. note purge, both lock orders ---------------------


@pytest.mark.django_db(transaction=True)
def test_note_purge_locks_first_owner_restore_loses(monkeypatch):
    owner = create_account("purge-race-note-owner-a")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        purge_module,
        "_is_purge_eligible",
        _pausing_purge_eligibility_check(lock_acquired=lock_acquired, proceed=proceed),
    )

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge did not reach the locked eligibility check"

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_from_trash(note=note)
            restore_outcome["ok"] = True
        except Exception as exc:
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    time.sleep(0.3)
    proceed.set()
    purge_thread.join(timeout=5)
    restore_thread.join(timeout=5)

    assert "error" not in purge_outcome, purge_outcome.get("error")
    assert purge_outcome["result"].notes_purged == 1
    # The row was deleted by purge before restore's lock acquisition ever
    # completed -- restore observes a missing row and fails safely.
    assert "error" in restore_outcome
    assert isinstance(restore_outcome["error"], Note.DoesNotExist)


@pytest.mark.django_db(transaction=True)
def test_owner_restore_locks_first_note_purge_still_purges_after_release(monkeypatch):
    """Owner self-restore (<=30 days) and final purge (>90 days) are
    disjoint, non-overlapping eligibility windows by design -- a note old
    enough to be purge-eligible can never simultaneously be self-restorable,
    so owner restore's own revalidation always rejects it once it holds the
    row lock. This proves the row lock still correctly serializes the two
    operations rather than corrupting state: restore fails safely with its
    own not-restorable error while holding the lock, releases it, and purge
    then observes the still-eligible row and purges it."""
    owner = create_account("purge-race-note-owner-b")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "purge thread never signaled proceed"
        return original_check(note_arg)

    monkeypatch.setattr(services, "note_is_visible_and_self_restorable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_from_trash(note=note)
            restore_outcome["ok"] = True
        except services.TrashItemNotRestorableError:
            restore_outcome["rejected"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    time.sleep(0.3)
    proceed.set()
    restore_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert restore_outcome.get("rejected") is True, restore_outcome
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()


# -- administrator note restore vs. note purge, both lock orders -------------


@pytest.mark.django_db(transaction=True)
def test_note_purge_locks_first_admin_restore_loses(monkeypatch):
    owner = create_account("purge-race-admin-note-owner-a")
    admin = create_admin("purge-race-admin-note-admin-a")
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=100))
    note.refresh_from_db()

    lock_acquired = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        purge_module,
        "_is_purge_eligible",
        _pausing_purge_eligibility_check(lock_acquired=lock_acquired, proceed=proceed),
    )

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge did not reach the locked eligibility check"

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_for_administrator(note_id=note.id, actor=admin)
            restore_outcome["ok"] = True
        except Exception as exc:
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    time.sleep(0.3)
    proceed.set()
    purge_thread.join(timeout=5)
    restore_thread.join(timeout=5)

    assert "error" not in purge_outcome, purge_outcome.get("error")
    assert purge_outcome["result"].notes_purged == 1
    assert "error" in restore_outcome
    # Unlike owner restore, `restore_note_for_administrator()` catches its
    # own `Note.DoesNotExist` and converts it to the same
    # `TrashItemNotRestorableError` it raises for any other ineligible row.
    assert isinstance(restore_outcome["error"], services.TrashItemNotRestorableError)


@pytest.mark.django_db(transaction=True)
def test_admin_restore_locks_first_note_purge_still_purges_after_release(monkeypatch):
    """Administrator recovery (<=90 days) and final purge (>90 days) share
    the exact day-90 boundary as their logical complement -- a note old
    enough to be purge-eligible can never simultaneously be
    administrator-recoverable, so administrator restore's own revalidation
    always rejects it once it holds the row lock. As with the owner-restore
    equivalent above, this proves the row lock still correctly serializes
    the two operations: restore fails safely while holding the lock,
    releases it, and purge then purges the still-eligible row."""
    owner = create_account("purge-race-admin-note-owner-b")
    admin = create_admin("purge-race-admin-note-admin-b")
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=100))
    note.refresh_from_db()

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services._is_hidden_but_administrator_recoverable

    def pausing_check(*, trashed_at, emptied_at, now):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "purge thread never signaled proceed"
        return original_check(trashed_at=trashed_at, emptied_at=emptied_at, now=now)

    monkeypatch.setattr(services, "_is_hidden_but_administrator_recoverable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_for_administrator(note_id=note.id, actor=admin)
            restore_outcome["ok"] = True
        except services.TrashItemNotRestorableError:
            restore_outcome["rejected"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    time.sleep(0.3)
    proceed.set()
    restore_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert restore_outcome.get("rejected") is True, restore_outcome
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()


# -- folder restore (owner or administrator) vs. folder purge, both orders --


@pytest.mark.django_db(transaction=True)
def test_folder_purge_locks_first_owner_folder_restore_loses(monkeypatch):
    owner = create_account("purge-race-folder-owner-a")
    folder = services.create_folder(owner=owner, name="Purge Race Folder A")
    trash_folder_days_ago(folder, days_ago=100)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        purge_module,
        "_is_purge_eligible",
        _pausing_purge_eligibility_check(lock_acquired=lock_acquired, proceed=proceed),
    )

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert lock_acquired.wait(timeout=5), "purge did not reach the locked eligibility check"

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_folder_from_trash(folder=folder)
            restore_outcome["ok"] = True
        except Exception as exc:
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    time.sleep(0.3)
    proceed.set()
    purge_thread.join(timeout=5)
    restore_thread.join(timeout=5)

    assert "error" not in purge_outcome, purge_outcome.get("error")
    assert purge_outcome["result"].folders_purged == 1
    assert "error" in restore_outcome
    assert isinstance(restore_outcome["error"], Folder.DoesNotExist)


@pytest.mark.django_db(transaction=True)
def test_owner_folder_restore_locks_first_folder_purge_still_purges_after_release(monkeypatch):
    """Owner self-restore (<=30 days) and final purge (>90 days) never
    overlap -- see the equivalent note-level test's docstring above for the
    full reasoning. This proves the folder row lock still correctly
    serializes the two operations."""
    owner = create_account("purge-race-folder-owner-b")
    folder = services.create_folder(owner=owner, name="Purge Race Folder B")
    trash_folder_days_ago(folder, days_ago=100)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.folder_is_visible_and_self_restorable

    def pausing_check(folder_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "purge thread never signaled proceed"
        return original_check(folder_arg)

    monkeypatch.setattr(services, "folder_is_visible_and_self_restorable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_folder_from_trash(folder=folder)
            restore_outcome["ok"] = True
        except services.TrashItemNotRestorableError:
            restore_outcome["rejected"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    time.sleep(0.3)
    proceed.set()
    restore_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert restore_outcome.get("rejected") is True, restore_outcome
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.folders_purged == 1
    assert not Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_admin_folder_restore_locks_first_folder_purge_still_purges_after_release(monkeypatch):
    """Administrator folder recovery (<=90 days) and final purge (>90 days)
    share the exact day-90 boundary as their logical complement -- see the
    equivalent note-level test's docstring above for the full reasoning."""
    owner = create_account("purge-race-admin-folder-owner")
    admin = create_admin("purge-race-admin-folder-admin")
    folder = services.create_folder(owner=owner, name="Purge Race Admin Folder")
    trash_and_empty_folder(folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=100))
    folder.refresh_from_db()

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services._is_hidden_but_administrator_recoverable

    def pausing_check(*, trashed_at, emptied_at, now):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "purge thread never signaled proceed"
        return original_check(trashed_at=trashed_at, emptied_at=emptied_at, now=now)

    monkeypatch.setattr(services, "_is_hidden_but_administrator_recoverable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)
            restore_outcome["ok"] = True
        except services.TrashItemNotRestorableError:
            restore_outcome["rejected"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)
    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    time.sleep(0.3)
    proceed.set()
    restore_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert restore_outcome.get("rejected") is True, restore_outcome
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.folders_purged == 1
    assert not Folder.objects.filter(pk=folder.pk).exists()


# -- candidate restored between batch selection and lock acquisition --------


@pytest.mark.django_db(transaction=True)
def test_note_restored_between_batch_selection_and_lock_is_skipped_not_purged(monkeypatch):
    """The note is purge-eligible (>90 days), so `restore_note_from_trash()`
    itself would reject it as not self-restorable -- the "restore" here is
    performed as a direct row update (matching the established bypass
    pattern used elsewhere for states unreachable via the owner-restore
    service, e.g. `test_folder_restore_wins_race_against_concurrent_empty_trash`),
    isolating the scenario under test: a candidate becoming ineligible
    between purge's batch-selection query and its per-row lock
    acquisition, in a second genuinely separate thread/connection."""
    owner = create_account("purge-race-selection-gap-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    selection_done = threading.Event()
    restore_done = threading.Event()
    original_lock = purge_module._lock_note_for_purge
    call_count = {"n": 0}

    def pausing_lock(*, note_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # The candidate-id batch selection query has already run by the
            # time this per-row lock function is reached; pausing here
            # reproduces the gap between batch selection and lock
            # acquisition.
            selection_done.set()
            assert restore_done.wait(timeout=5), "restore thread never completed"
        return original_lock(note_id=note_id)

    monkeypatch.setattr(purge_module, "_lock_note_for_purge", pausing_lock)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert selection_done.wait(timeout=5), "purge did not reach its locked read"

    def run_restore():
        try:
            Note.objects.filter(pk=note.pk).update(trashed_at=None, emptied_at=None)
        finally:
            connections.close_all()
            restore_done.set()

    restore_thread = _run_in_thread(run_restore)
    restore_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.notes_purged == 0
    assert result.notes_failed == 0
    note.refresh_from_db()
    assert note.trashed_at is None


# -- two purge cycles invoked simultaneously ---------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_purge_cycles_serialize_via_advisory_lock(monkeypatch):
    owner = create_account("purge-race-two-cycles-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        purge_module,
        "_is_purge_eligible",
        _pausing_purge_eligibility_check(lock_acquired=lock_acquired, proceed=proceed),
    )

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first cycle did not reach the locked eligibility check"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)
    thread_b.join(timeout=5)
    proceed.set()
    thread_a.join(timeout=5)

    assert "error" not in outcome_a, outcome_a.get("error")
    assert "error" not in outcome_b, outcome_b.get("error")
    assert outcome_a["result"].lock_acquired is True
    assert outcome_a["result"].notes_purged == 1
    assert outcome_b["result"].lock_acquired is False
    assert outcome_b["result"].notes_purged == 0
    assert outcome_b["result"].notes_failed == 0


# -- folder reference correctly blocks purge regardless of Note lifecycle ---


@pytest.mark.django_db
def test_folder_with_a_trashed_referencing_note_is_never_purged():
    """Verifies the still-required property that a Folder referenced by
    a Trashed Note is never purged.

    `assign_note_folder()`, `create_note()`, and `duplicate_note()` all
    reject any destination Folder that is not active, so an Active Note
    can never be newly pointed at an already-Trashed, purge-eligible
    Folder -- concurrently or otherwise. The live application exposes
    no operation that can create that reference while racing purge's
    own row lock (held during its `_folder_is_referenced()` check), so
    the invariant is exercised sequentially rather than by
    manufacturing an unreachable race.

    The one legitimate way a Trashed Folder can be referenced at all is
    the reference `move_folder_to_trash()` itself preserves when it
    cascades its own active Notes into Trash alongside it --
    `_folder_is_referenced()` intentionally still counts that reference
    regardless of the Note's own lifecycle."""
    owner = create_account("purge-race-reference-owner")
    folder = services.create_folder(owner=owner, name="Reference Race Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_folder_days_ago(folder, days_ago=100)

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert result.folders_purged == 0
    assert result.folders_blocked == 1
    assert Folder.objects.filter(pk=folder.pk).exists()


# -- advisory lock actually released after cycle completes ------------------


@pytest.mark.django_db(transaction=True)
def test_advisory_lock_released_after_cycle_completes():
    owner = create_account("purge-race-lock-release-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)
    assert result.lock_acquired is True
    connections.close_all()

    second_acquired = {}

    def try_second_acquire():
        try:
            with connection.cursor() as cursor:
                cursor.execute("select pg_try_advisory_lock(%s)", [PURGE_CYCLE_LOCK_ID])
                acquired = cursor.fetchone()[0]
                second_acquired["value"] = acquired
                if acquired:
                    cursor.execute("select pg_advisory_unlock(%s)", [PURGE_CYCLE_LOCK_ID])
        finally:
            connections.close_all()

    thread = _run_in_thread(try_second_acquire)
    thread.join(timeout=5)

    assert second_acquired.get("value") is True, (
        "advisory lock was not released after the purge cycle completed"
    )


# -- pending account deletion scheduled after candidate selection ------------


@pytest.mark.django_db(transaction=True)
def test_note_pending_deletion_scheduled_after_selection_is_skipped_not_purged(monkeypatch):
    """A Note candidate selected while its
    owner was still active must not be purged if account deletion is
    scheduled and committed before the per-candidate transaction reaches
    its own User lock. Deterministically reproduces the gap between batch
    selection and the User lock, in a second genuinely separate
    thread/connection, following the same technique as
    `test_note_restored_between_batch_selection_and_lock_is_skipped_not_purged`
    above."""
    admin = create_admin("purge-race-pending-note-admin")
    owner = create_account("purge-race-pending-note-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    selection_done = threading.Event()
    scheduling_done = threading.Event()
    original_lock_user = purge_module._lock_user_for_purge
    call_count = {"n": 0}

    def pausing_lock_user(*, owner_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            selection_done.set()
            assert scheduling_done.wait(timeout=5), "scheduling thread never completed"
        return original_lock_user(owner_id=owner_id)

    monkeypatch.setattr(purge_module, "_lock_user_for_purge", pausing_lock_user)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert selection_done.wait(timeout=5), "purge did not reach its User lock"

    schedule_outcome = {}

    def run_schedule():
        try:
            account_services.schedule_user_deletion(target=owner, actor=admin)
            schedule_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            schedule_outcome["error"] = exc
        finally:
            connections.close_all()
            scheduling_done.set()

    schedule_thread = _run_in_thread(run_schedule)
    schedule_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert "error" not in schedule_outcome, schedule_outcome.get("error")
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.notes_purged == 0
    assert result.notes_failed == 0
    assert Note.objects.filter(pk=note.pk).exists()
    owner.refresh_from_db()
    assert owner.deletion_scheduled_at is not None


@pytest.mark.django_db(transaction=True)
def test_folder_pending_deletion_scheduled_after_selection_is_skipped_not_purged(monkeypatch):
    """Folder equivalent of the note test above -- the folder is left
    unreferenced so it would otherwise be purgeable, isolating the
    pending-deletion race from the unrelated folder-reference check."""
    admin = create_admin("purge-race-pending-folder-admin")
    owner = create_account("purge-race-pending-folder-owner")
    folder = services.create_folder(owner=owner, name="Pending Race Folder")
    trash_folder_days_ago(folder, days_ago=100)

    selection_done = threading.Event()
    scheduling_done = threading.Event()
    original_lock_user = purge_module._lock_user_for_purge
    call_count = {"n": 0}

    def pausing_lock_user(*, owner_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            selection_done.set()
            assert scheduling_done.wait(timeout=5), "scheduling thread never completed"
        return original_lock_user(owner_id=owner_id)

    monkeypatch.setattr(purge_module, "_lock_user_for_purge", pausing_lock_user)

    purge_outcome = {}

    def run_purge():
        try:
            purge_outcome["result"] = run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            purge_outcome["error"] = exc
        finally:
            connections.close_all()

    purge_thread = _run_in_thread(run_purge)
    assert selection_done.wait(timeout=5), "purge did not reach its User lock"

    schedule_outcome = {}

    def run_schedule():
        try:
            account_services.schedule_user_deletion(target=owner, actor=admin)
            schedule_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            schedule_outcome["error"] = exc
        finally:
            connections.close_all()
            scheduling_done.set()

    schedule_thread = _run_in_thread(run_schedule)
    schedule_thread.join(timeout=5)
    purge_thread.join(timeout=5)

    assert "error" not in schedule_outcome, schedule_outcome.get("error")
    assert "error" not in purge_outcome, purge_outcome.get("error")
    result = purge_outcome["result"]
    assert result.folders_purged == 0
    assert result.folders_failed == 0
    assert Folder.objects.filter(pk=folder.pk).exists()
    owner.refresh_from_db()
    assert owner.deletion_scheduled_at is not None
