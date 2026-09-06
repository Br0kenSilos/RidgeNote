"""Library Backup manifest snapshot
consistency.

`build_library_backup_manifest()` now performs all of its Folder/Tag/
Note reads inside one dedicated PostgreSQL transaction with
`REPEATABLE READ` isolation, set as that transaction's first statement.
The tests below prove the resulting MVCC snapshot guarantee under real
PostgreSQL: a concurrent write or Library Restore that commits *after*
the snapshot's first query must never be partially visible to the
later reads in the same transaction -- the backup either reflects the
complete state from before that commit, or (if the snapshot had not
yet started) the complete state after it, never a structurally-valid
mixture of both. As established by `test_purge_race.py`/
`test_folder_trash_race.py`, real cross-connection concurrency requires
`@pytest.mark.django_db(transaction=True)` and real background threads
synchronized with `threading.Event`s -- an ordinary `django_db` test
runs inside a single shared connection/transaction and cannot
demonstrate genuine committed-elsewhere visibility.
"""

from __future__ import annotations

import threading

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.utils import timezone

import notes.library_backup as library_backup_module
from notes import services
from notes.library_backup import (
    LibraryBackupManifest,
    LibraryBackupNote,
    LibraryBackupSnapshotNestedTransactionError,
    build_library_backup_manifest,
    parse_library_backup_manifest,
)
from notes.library_backup_restore import restore_library_backup_for_owner
from notes.models import Note

PASSWORD = "LongUniquePassword123!"


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


def _pausing(original, *, lock_acquired: threading.Event, proceed: threading.Event):
    """Wraps `original` so the FIRST call still actually executes (this
    is the read that establishes the transaction's REPEATABLE READ
    snapshot) before pausing -- pausing beforehand would let a
    concurrent commit land before the snapshot exists at all, which
    proves nothing about snapshot consistency."""
    call_count = {"n": 0}

    def pausing(*args, **kwargs):
        call_count["n"] += 1
        result = original(*args, **kwargs)
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "concurrent thread never signaled proceed"
        return result

    return pausing


# -- Folder-map coherence -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_folder_created_and_referenced_mid_snapshot_is_absent_not_a_keyerror(monkeypatch):
    owner = create_account("snapshot-race-folder-owner")
    existing_note = services.create_note(owner=owner)
    services.rename_note(note=existing_note, title="Pre-Snapshot Note")

    snapshot_started = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        library_backup_module,
        "_select_folders_for_snapshot",
        _pausing(
            library_backup_module._select_folders_for_snapshot,
            lock_acquired=snapshot_started,
            proceed=proceed,
        ),
    )

    backup_outcome = {}

    def run_backup():
        try:
            backup_outcome["manifest"] = build_library_backup_manifest(owner)
        except Exception as exc:  # pragma: no cover
            backup_outcome["error"] = exc
        finally:
            connections.close_all()

    backup_thread = _run_in_thread(run_backup)
    assert snapshot_started.wait(timeout=5), "backup did not reach its Folder snapshot read"

    write_outcome = {}

    def run_concurrent_write():
        try:
            new_folder = services.create_folder(owner=owner, name="Concurrent Folder")
            new_note = services.create_note(owner=owner, folder=new_folder)
            services.rename_note(note=new_note, title="Concurrent Note")
            write_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            write_outcome["error"] = exc
        finally:
            connections.close_all()

    write_thread = _run_in_thread(run_concurrent_write)
    write_thread.join(timeout=5)
    proceed.set()
    backup_thread.join(timeout=5)

    assert "error" not in write_outcome, write_outcome.get("error")
    assert "error" not in backup_outcome, backup_outcome.get("error")
    manifest = backup_outcome["manifest"]

    # No KeyError was raised (the backup completed), and it remains
    # coherent with the snapshot established before the concurrent
    # commit -- the new Folder/Note are entirely absent, never
    # half-present, and the pre-existing note is still there.
    note_titles = {note["title"] for note in manifest["notes"]}
    folder_names = {folder["name"] for folder in manifest["folders"]}
    assert "Pre-Snapshot Note" in note_titles
    assert "Concurrent Note" not in note_titles
    assert "Concurrent Folder" not in folder_names

    parsed = parse_library_backup_manifest(manifest)
    assert len(parsed.notes) == 1
    assert len(parsed.folders) == 0


# -- Tag-map coherence ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_tag_created_and_assigned_mid_snapshot_is_absent_not_a_keyerror(monkeypatch):
    owner = create_account("snapshot-race-tag-owner")
    existing_note = services.create_note(owner=owner)
    services.rename_note(note=existing_note, title="Pre-Snapshot Tag Note")

    snapshot_started = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        library_backup_module,
        "_select_tags_for_snapshot",
        _pausing(
            library_backup_module._select_tags_for_snapshot,
            lock_acquired=snapshot_started,
            proceed=proceed,
        ),
    )

    backup_outcome = {}

    def run_backup():
        try:
            backup_outcome["manifest"] = build_library_backup_manifest(owner)
        except Exception as exc:  # pragma: no cover
            backup_outcome["error"] = exc
        finally:
            connections.close_all()

    backup_thread = _run_in_thread(run_backup)
    assert snapshot_started.wait(timeout=5), "backup did not reach its Tag snapshot read"

    write_outcome = {}

    def run_concurrent_write():
        try:
            note = Note.objects.get(pk=existing_note.pk)
            new_tag = services.get_or_create_tag(owner=owner, name="Concurrent Tag", color="blue")
            services.assign_tag_to_note(note=note, tag=new_tag)
            write_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            write_outcome["error"] = exc
        finally:
            connections.close_all()

    write_thread = _run_in_thread(run_concurrent_write)
    write_thread.join(timeout=5)
    proceed.set()
    backup_thread.join(timeout=5)

    assert "error" not in write_outcome, write_outcome.get("error")
    assert "error" not in backup_outcome, backup_outcome.get("error")
    manifest = backup_outcome["manifest"]

    tag_names = {tag["name"] for tag in manifest["tags"]}
    assert "Concurrent Tag" not in tag_names
    note_payload = next(
        note for note in manifest["notes"] if note["title"] == "Pre-Snapshot Tag Note"
    )
    assert note_payload["tag_ids"] == []

    parsed = parse_library_backup_manifest(manifest)
    assert len(parsed.tags) == 0


# -- Concurrent Library Restore --------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_library_restore_backup_sees_coherent_pre_restore_state(monkeypatch):
    owner = create_account("snapshot-race-restore-owner")
    original_note = services.create_note(owner=owner)
    services.rename_note(note=original_note, title="Original Note")

    snapshot_started = threading.Event()
    proceed = threading.Event()
    monkeypatch.setattr(
        library_backup_module,
        "_select_folders_for_snapshot",
        _pausing(
            library_backup_module._select_folders_for_snapshot,
            lock_acquired=snapshot_started,
            proceed=proceed,
        ),
    )

    backup_outcome = {}

    def run_backup():
        try:
            backup_outcome["manifest"] = build_library_backup_manifest(owner)
        except Exception as exc:  # pragma: no cover
            backup_outcome["error"] = exc
        finally:
            connections.close_all()

    backup_thread = _run_in_thread(run_backup)
    assert snapshot_started.wait(timeout=5), "backup did not reach its Folder snapshot read"

    restore_manifest = LibraryBackupManifest(
        format_identifier="ridgenote-library-backup",
        format_version=1,
        exported_at=timezone.now(),
        scope=("active", "trash", "recovery"),
        folders=(),
        tags=(),
        notes=(
            LibraryBackupNote(
                id="note-000001",
                title="Restored Note",
                body_json={"type": "doc", "content": [{"type": "paragraph"}]},
                folder_id=None,
                tag_ids=(),
                pinned=False,
                lifecycle="active",
                trashed_at=None,
                emptied_at=None,
                created_at=timezone.now(),
                modified_at=timezone.now(),
                markdown_path="notes/active/Unfiled/Restored Note.md",
            ),
        ),
    )

    restore_outcome = {}

    def run_concurrent_restore():
        try:
            restore_library_backup_for_owner(owner, restore_manifest, request=None)
            restore_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_concurrent_restore)
    restore_thread.join(timeout=5)
    proceed.set()
    backup_thread.join(timeout=5)

    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert "error" not in backup_outcome, backup_outcome.get("error")
    manifest = backup_outcome["manifest"]
    parsed = parse_library_backup_manifest(manifest)

    # The backup's transaction snapshot was already established before
    # the concurrent restore committed, so it must reflect the complete
    # pre-restore state -- the original note, not the restored one --
    # never a mixture of both.
    note_titles = {note.title for note in parsed.notes}
    assert note_titles == {"Original Note"}
    assert "Restored Note" not in note_titles


# -- Nested-transaction guard --------------------------------------------------


@pytest.mark.django_db
def test_snapshot_raises_when_called_from_inside_an_active_transaction():
    """Deliberately nests the call inside an explicit `transaction.atomic()`
    block -- on top of the ambient one `@pytest.mark.django_db` itself
    already provides -- so the guard's trigger condition
    (`connection.in_atomic_block`) is unambiguous and does not rely on
    incidental knowledge of the test harness's own wrapping."""
    owner = create_account("snapshot-guard-owner")

    with transaction.atomic():
        with pytest.raises(LibraryBackupSnapshotNestedTransactionError):
            build_library_backup_manifest(owner)


# -- Isolation does not leak to later use of the same connection --------------


@pytest.mark.django_db(transaction=True)
def test_snapshot_isolation_does_not_leak_to_later_queries_on_same_connection():
    owner = create_account("snapshot-leak-owner")

    build_library_backup_manifest(owner)

    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        (isolation,) = cursor.fetchone()

    assert isolation == "read committed"
