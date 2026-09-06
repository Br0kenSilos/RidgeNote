"""Full-overwrite owner-library restore.

Covers `notes.library_backup_restore`: restore-specific semantic
pre-validation, the transaction/locking strategy, deletion,
recreation, export-ID-to-new-PK mapping, timestamp restoration,
derived-field behavior, audit, rollback-on-failure, and the
concurrency guarantees this module provides
(owner-row `select_for_update()` closes the same-owner new-insert gap
via PostgreSQL's deferred foreign-key constraint check; no advisory
lock is used).
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.db import connections
from django.utils import timezone

from notes import documents, library_backup_restore, services
from notes.library_backup import (
    LibraryBackupFolder,
    LibraryBackupManifest,
    LibraryBackupNote,
    LibraryBackupTag,
)
from notes.library_backup_restore import (
    LibraryBackupRestoreValidationError,
    restore_library_backup_for_owner,
    validate_library_backup_for_restore,
)
from notes.library_backup_upload import load_library_backup_for_restore
from notes.models import DEFAULT_TAG_COLOR, Folder, Note, Tag

PASSWORD = "LongUniquePassword123!"

_EXPORTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


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


def _folder(
    id="folder-000001",
    name="Folder",
    *,
    lifecycle="active",
    trashed_at=None,
    emptied_at=None,
    is_recovery_folder=False,
    created_at=_EXPORTED_AT,
):
    return LibraryBackupFolder(
        id=id,
        name=name,
        lifecycle=lifecycle,
        trashed_at=trashed_at,
        emptied_at=emptied_at,
        is_recovery_folder=is_recovery_folder,
        created_at=created_at,
    )


def _tag(id="tag-000001", name="tag", *, color=DEFAULT_TAG_COLOR):
    return LibraryBackupTag(id=id, name=name, color=color)


def _note(
    id="note-000001",
    title="Note",
    *,
    body_json=None,
    folder_id=None,
    tag_ids=(),
    pinned=False,
    lifecycle="active",
    trashed_at=None,
    emptied_at=None,
    created_at=_EXPORTED_AT,
    modified_at=_EXPORTED_AT,
    markdown_path="notes/active/Unfiled/Note.md",
):
    return LibraryBackupNote(
        id=id,
        title=title,
        body_json=body_json or {"type": "doc", "content": [{"type": "paragraph"}]},
        folder_id=folder_id,
        tag_ids=tag_ids,
        pinned=pinned,
        lifecycle=lifecycle,
        trashed_at=trashed_at,
        emptied_at=emptied_at,
        created_at=created_at,
        modified_at=modified_at,
        markdown_path=markdown_path,
    )


def _manifest(*, folders=(), tags=(), notes=()):
    return LibraryBackupManifest(
        format_identifier="ridgenote-library-backup",
        format_version=1,
        exported_at=_EXPORTED_AT,
        scope=("active", "trash", "recovery"),
        folders=folders,
        tags=tags,
        notes=notes,
    )


def _snapshot(owner):
    return {
        "folders": list(
            Folder.objects.filter(owner=owner)
            .order_by("pk")
            .values("id", "name", "trashed_at", "emptied_at", "is_recovery_folder", "created_at")
        ),
        "notes": list(
            Note.objects.filter(owner=owner)
            .order_by("pk")
            .values(
                "id",
                "title",
                "body_json",
                "folder_id",
                "pinned",
                "trashed_at",
                "emptied_at",
                "created_at",
                "modified_at",
            )
        ),
        "tags": list(Tag.objects.filter(owner=owner).order_by("pk").values("id", "name")),
        "note_tags": list(
            Note.tags.through.objects.filter(note__owner=owner)
            .order_by("pk")
            .values("note_id", "tag_id")
        ),
        "audit_count": AuditEvent.objects.count(),
    }


# ---------------------------------------------------------------------------
# Restore-specific semantic pre-validation
# ---------------------------------------------------------------------------


def test_valid_manifest_passes_semantic_validation():
    manifest = _manifest(folders=(_folder(),), tags=(_tag(),))
    validate_library_backup_for_restore(manifest)  # must not raise


def test_active_folder_name_collision_rejected():
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "Shared Name"),
            _folder("folder-000002", "shared name"),
        )
    )
    with pytest.raises(LibraryBackupRestoreValidationError) as excinfo:
        validate_library_backup_for_restore(manifest)
    assert excinfo.value.code == "duplicate_active_folder_name"


def test_trash_folder_name_collision_with_active_is_allowed():
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "Same Name", lifecycle="active"),
            _folder(
                "folder-000002",
                "Same Name",
                lifecycle="trash",
                trashed_at=_EXPORTED_AT,
            ),
        )
    )
    validate_library_backup_for_restore(manifest)  # must not raise


def test_two_trash_folders_same_name_allowed():
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "Same Name", lifecycle="trash", trashed_at=_EXPORTED_AT),
            _folder("folder-000002", "Same Name", lifecycle="trash", trashed_at=_EXPORTED_AT),
        )
    )
    validate_library_backup_for_restore(manifest)  # must not raise


def test_tag_name_collision_rejected():
    manifest = _manifest(tags=(_tag("tag-000001", "Shared"), _tag("tag-000002", "shared")))
    with pytest.raises(LibraryBackupRestoreValidationError) as excinfo:
        validate_library_backup_for_restore(manifest)
    assert excinfo.value.code == "duplicate_tag_name"


def test_zero_recovery_markers_allowed():
    manifest = _manifest(folders=(_folder(is_recovery_folder=False),))
    validate_library_backup_for_restore(manifest)  # must not raise


def test_one_recovery_marker_allowed():
    manifest = _manifest(folders=(_folder(is_recovery_folder=True),))
    validate_library_backup_for_restore(manifest)  # must not raise


def test_multiple_recovery_markers_rejected():
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "A", is_recovery_folder=True),
            _folder("folder-000002", "B", is_recovery_folder=True),
        )
    )
    with pytest.raises(LibraryBackupRestoreValidationError) as excinfo:
        validate_library_backup_for_restore(manifest)
    assert excinfo.value.code == "multiple_recovery_markers"


def test_active_note_referencing_trashed_folder_rejected():
    manifest = _manifest(
        folders=(_folder("folder-000001", "Trashed", lifecycle="trash", trashed_at=_EXPORTED_AT),),
        notes=(_note("note-000001", "Note", folder_id="folder-000001", lifecycle="active"),),
    )
    with pytest.raises(LibraryBackupRestoreValidationError) as excinfo:
        validate_library_backup_for_restore(manifest)
    assert excinfo.value.code == "active_note_references_trashed_folder"


def test_trashed_note_referencing_active_folder_is_allowed():
    manifest = _manifest(
        folders=(_folder("folder-000001", "Active", lifecycle="active"),),
        notes=(
            _note(
                "note-000001",
                "Note",
                folder_id="folder-000001",
                lifecycle="trash",
                trashed_at=_EXPORTED_AT,
                markdown_path="notes/trash/Active/Note.md",
            ),
        ),
    )
    validate_library_backup_for_restore(manifest)  # must not raise


def test_active_note_referencing_active_folder_is_allowed():
    manifest = _manifest(
        folders=(_folder("folder-000001", "Active", lifecycle="active"),),
        notes=(_note("note-000001", "Note", folder_id="folder-000001", lifecycle="active"),),
    )
    validate_library_backup_for_restore(manifest)  # must not raise


def test_active_unfiled_note_is_allowed():
    manifest = _manifest(notes=(_note("note-000001", "Note", folder_id=None, lifecycle="active"),))
    validate_library_backup_for_restore(manifest)  # must not raise


@pytest.mark.django_db
def test_semantic_validation_failure_mutates_nothing():
    owner = create_account("semantic-no-mutation-owner")
    services.create_note(owner=owner)
    before = _snapshot(owner)
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "A", is_recovery_folder=True),
            _folder("folder-000002", "B", is_recovery_folder=True),
        )
    )
    with pytest.raises(LibraryBackupRestoreValidationError):
        validate_library_backup_for_restore(manifest)
    assert _snapshot(owner) == before


# ---------------------------------------------------------------------------
# Successful restore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_empty_current_to_populated_backup():
    owner = create_account("empty-to-populated-owner")
    manifest = _manifest(
        folders=(_folder("folder-000001", "Work"),),
        tags=(_tag("tag-000001", "important"),),
        notes=(
            _note(
                "note-000001",
                "My Note",
                folder_id="folder-000001",
                tag_ids=("tag-000001",),
                pinned=True,
            ),
        ),
    )
    result = restore_library_backup_for_owner(owner, manifest, request=None)
    assert result.folder_count == 1
    assert result.tag_count == 1
    assert result.note_count == 1

    folder = Folder.objects.get(owner=owner)
    note = Note.objects.get(owner=owner)
    tag = Tag.objects.get(owner=owner)
    assert folder.name == "Work"
    assert note.title == "My Note"
    assert note.folder_id == folder.id
    assert note.pinned is True
    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "color",
    [
        "slate",
        "blue",
        "teal",
        "green",
        "yellow",
        "amber",
        "orange",
        "red",
        "rose",
        "violet",
        "brown",
    ],
)
def test_restore_preserves_tag_color(color):
    owner = create_account(f"restore-tag-color-{color}-owner")
    manifest = _manifest(tags=(_tag("tag-000001", "Colored", color=color),))

    restore_library_backup_for_owner(owner, manifest, request=None)

    tag = Tag.objects.get(owner=owner)
    assert tag.color == color


@pytest.mark.django_db
def test_restore_defaults_tag_color_for_legacy_archive_without_color():
    owner = create_account("restore-tag-legacy-color-owner")
    manifest = _manifest(tags=(_tag("tag-000001", "Legacy"),))

    restore_library_backup_for_owner(owner, manifest, request=None)

    tag = Tag.objects.get(owner=owner)
    assert tag.color == DEFAULT_TAG_COLOR


@pytest.mark.django_db
def test_restore_preserves_tag_name_casing_verbatim():
    owner = create_account("restore-tag-casing-owner")
    manifest = _manifest(tags=(_tag("tag-000001", "MiXeD CaSe"),))

    restore_library_backup_for_owner(owner, manifest, request=None)

    tag = Tag.objects.get(owner=owner)
    assert tag.name == "MiXeD CaSe"


@pytest.mark.django_db
def test_populated_current_to_empty_backup():
    owner = create_account("populated-to-empty-owner")
    services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Old Folder")

    manifest = _manifest()
    result = restore_library_backup_for_owner(owner, manifest, request=None)

    assert result.folder_count == 0
    assert result.note_count == 0
    assert not Folder.objects.filter(owner=owner).exists()
    assert not Note.objects.filter(owner=owner).exists()
    assert User.objects.filter(pk=owner.pk).exists()
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED).count() == 1
    )


@pytest.mark.django_db
def test_restore_does_not_alter_owner_timezone_preference():
    # `timezone_name` is account-level state, never
    # part of the manifest -- a restore must never touch it, regardless
    # of what the restored library contains.
    owner = create_account("restore-timezone-preserved-owner", timezone_name="America/New_York")
    manifest = _manifest(
        folders=(_folder("folder-000001", "Work"),),
        notes=(_note("note-000001", "My Note", folder_id="folder-000001"),),
    )

    restore_library_backup_for_owner(owner, manifest, request=None)

    owner.refresh_from_db()
    assert owner.timezone_name == "America/New_York"


@pytest.mark.django_db
def test_populated_current_to_different_populated_backup():
    owner = create_account("populated-to-different-owner")
    old_note = services.create_note(owner=owner)
    old_note_id = old_note.id

    manifest = _manifest(
        folders=(_folder("folder-000001", "New Folder"),),
        notes=(_note("note-000001", "New Note", folder_id="folder-000001"),),
    )
    restore_library_backup_for_owner(owner, manifest, request=None)

    assert not Note.objects.filter(pk=old_note_id).exists()
    new_note = Note.objects.get(owner=owner)
    assert new_note.title == "New Note"
    assert new_note.pk != old_note_id


# ---------------------------------------------------------------------------
# Other-user isolation and account/security preservation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_other_owner_library_untouched():
    owner = create_account("isolation-owner")
    other = create_account("isolation-other-owner")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Other's Note")
    other_folder = services.create_folder(owner=other, name="Other's Folder")

    manifest = _manifest(notes=(_note("note-000001", "Mine"),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    other_note.refresh_from_db()
    other_folder.refresh_from_db()
    assert other_note.title == "Other's Note"
    assert other_folder.name == "Other's Folder"
    assert Note.objects.filter(owner=other).count() == 1
    assert Folder.objects.filter(owner=other).count() == 1


@pytest.mark.django_db
def test_account_and_security_fields_unchanged():
    owner = create_account("account-preserved-owner")
    password_hash_before = owner.password
    role_before = owner.role
    session_generation_before = owner.session_generation

    manifest = _manifest(notes=(_note(),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    owner.refresh_from_db()
    assert owner.password == password_hash_before
    assert owner.role == role_before
    assert owner.session_generation == session_generation_before
    assert owner.username == "account-preserved-owner"


# ---------------------------------------------------------------------------
# ID remapping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_manifest_ids_never_used_as_real_pks():
    owner = create_account("id-remap-owner")
    manifest = _manifest(
        folders=(_folder("folder-000001", "F"),),
        tags=(_tag("tag-000001", "T"),),
        notes=(_note("note-000001", "N", folder_id="folder-000001", tag_ids=("tag-000001",)),),
    )
    restore_library_backup_for_owner(owner, manifest, request=None)

    folder = Folder.objects.get(owner=owner)
    tag = Tag.objects.get(owner=owner)
    note = Note.objects.get(owner=owner)
    assert isinstance(folder.pk, int) and folder.pk != "folder-000001"
    assert isinstance(tag.pk, int)
    assert isinstance(note.pk, int)
    assert note.folder_id == folder.pk
    assert list(note.tags.values_list("pk", flat=True)) == [tag.pk]


@pytest.mark.django_db
def test_restored_pks_exceed_prior_pks():
    owner = create_account("id-remap-pk-order-owner")
    old_folder = services.create_folder(owner=owner, name="Old")
    old_note = services.create_note(owner=owner)
    old_folder_id, old_note_id = old_folder.id, old_note.id

    manifest = _manifest(folders=(_folder(),), notes=(_note(),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    new_folder = Folder.objects.get(owner=owner)
    new_note = Note.objects.get(owner=owner)
    assert new_folder.pk > old_folder_id
    assert new_note.pk > old_note_id


# ---------------------------------------------------------------------------
# Timestamp preservation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_timestamps_preserved_to_the_microsecond():
    owner = create_account("timestamp-owner")
    created = datetime(2019, 4, 2, 3, 4, 5, 123456, tzinfo=UTC)
    modified = datetime(2022, 8, 3, 4, 5, 6, 654321, tzinfo=UTC)
    trashed = datetime(2023, 1, 1, 0, 0, 0, 111111, tzinfo=UTC)

    manifest = _manifest(
        folders=(_folder(created_at=created),),
        notes=(
            _note(
                created_at=created,
                modified_at=modified,
                lifecycle="trash",
                trashed_at=trashed,
            ),
        ),
    )
    restore_library_backup_for_owner(owner, manifest, request=None)

    folder = Folder.objects.get(owner=owner)
    note = Note.objects.get(owner=owner)
    assert folder.created_at == created
    assert note.created_at == created
    assert note.modified_at == modified
    assert note.trashed_at == trashed


@pytest.mark.django_db
def test_non_utc_offset_timestamp_round_trips_to_same_instant():
    owner = create_account("non-utc-timestamp-owner")
    non_utc = datetime(2023, 6, 1, 12, 0, 0, 500000, tzinfo=dt_timezone(timedelta(hours=-5)))
    manifest = _manifest(notes=(_note(created_at=non_utc, modified_at=non_utc),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    note = Note.objects.get(owner=owner)
    assert note.created_at == non_utc
    assert note.modified_at == non_utc


@pytest.mark.django_db
def test_restored_note_modified_at_advances_normally_on_later_save():
    owner = create_account("post-restore-save-owner")
    archived = datetime(2019, 1, 1, tzinfo=UTC)
    manifest = _manifest(notes=(_note(created_at=archived, modified_at=archived),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    note = Note.objects.get(owner=owner)
    services.save_note(note=note, title="Updated", body_json=note.body_json, version=note.version)

    note.refresh_from_db()
    assert note.modified_at > archived
    assert note.version == 2


# ---------------------------------------------------------------------------
# Derived fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_derived_fields_correct():
    owner = create_account("derived-fields-owner")
    body_json = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
    }
    manifest = _manifest(notes=(_note(body_json=body_json),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    note = Note.objects.get(owner=owner)
    assert note.body_json == body_json
    assert note.body_plain_text == documents.derive_plain_text(body_json)
    assert note.editor_schema_version == documents.EDITOR_SCHEMA_VERSION
    assert note.version == 1


# ---------------------------------------------------------------------------
# Markdown is never authoritative
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_markdown_content_never_used_for_restored_body(tmp_path):
    owner = create_account("markdown-authority-owner")
    real_body = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Real body"}]}],
    }
    manifest_dict = {
        "format_identifier": "ridgenote-library-backup",
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "scope": {"active": True, "trash": True, "recovery": True},
        "folders": [],
        "tags": [],
        "notes": [
            {
                "id": "note-000001",
                "title": "Sample",
                "body_json": real_body,
                "folder_id": None,
                "tag_ids": [],
                "pinned": False,
                "lifecycle": "active",
                "trashed_at": None,
                "emptied_at": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "modified_at": "2026-01-01T00:00:00+00:00",
                "markdown_path": "notes/active/Unfiled/Sample.md",
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest_dict).encode("utf-8"))
        archive.writestr("README.txt", b"RidgeNote Library Backup\n")
        # Deliberately unrelated Markdown text -- must never affect the
        # restored body_json.
        archive.writestr(
            "notes/active/Unfiled/Sample.md", b"# Totally different\n\nUnrelated text.\n"
        )
    raw = buf.getvalue()

    validated = load_library_backup_for_restore(io.BytesIO(raw), compressed_size=len(raw))
    restore_library_backup_for_owner(owner, validated.manifest, request=None)

    note = Note.objects.get(owner=owner)
    assert note.body_json == real_body
    assert "Totally different" not in note.body_plain_text


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_exactly_one_content_free_audit_event_on_success():
    owner = create_account("audit-success-owner")
    manifest = _manifest(notes=(_note("note-000001", "Sensitive Title"),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED)
    assert events.count() == 1
    event = events.first()
    assert event.actor_id == owner.id
    assert event.target_user_id == owner.id
    assert event.details == {}
    assert "Sensitive Title" not in str(event.details)


@pytest.mark.django_db
def test_no_audit_event_on_semantic_validation_failure():
    manifest = _manifest(
        folders=(
            _folder("folder-000001", "A", is_recovery_folder=True),
            _folder("folder-000002", "B", is_recovery_folder=True),
        )
    )
    with pytest.raises(LibraryBackupRestoreValidationError):
        validate_library_backup_for_restore(manifest)
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED).count() == 0
    )


@pytest.mark.django_db
def test_audit_failure_rolls_back_entire_restore(monkeypatch):
    owner = create_account("audit-failure-owner")
    services.create_note(owner=owner)
    before = _snapshot(owner)

    def _boom(*args, **kwargs):
        raise RuntimeError("audit boom")

    monkeypatch.setattr(library_backup_restore.account_services, "record_audit_event", _boom)

    manifest = _manifest(notes=(_note(),))
    with pytest.raises(RuntimeError, match="audit boom"):
        restore_library_backup_for_owner(owner, manifest, request=None)

    assert _snapshot(owner) == before


# ---------------------------------------------------------------------------
# Rollback failure injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "_delete_current_library",
        "_create_folders",
        "_create_tags",
        "_create_notes",
        "_create_note_tag_relations",
    ],
)
@pytest.mark.django_db
def test_rollback_on_injected_failure(monkeypatch, target):
    owner = create_account(f"rollback-{target}-owner")
    original_note = services.create_note(owner=owner)
    services.rename_note(note=original_note, title="Original")
    original_folder = services.create_folder(owner=owner, name="Original Folder")
    before = _snapshot(owner)

    def _boom(*args, **kwargs):
        raise RuntimeError(f"boom at {target}")

    monkeypatch.setattr(library_backup_restore, target, _boom)

    manifest = _manifest(
        folders=(_folder("folder-000001", "New Folder"),),
        tags=(_tag("tag-000001", "new-tag"),),
        notes=(
            _note("note-000001", "New Note", folder_id="folder-000001", tag_ids=("tag-000001",)),
        ),
    )
    with pytest.raises(RuntimeError, match=f"boom at {target}"):
        restore_library_backup_for_owner(owner, manifest, request=None)

    assert _snapshot(owner) == before
    original_note.refresh_from_db()
    original_folder.refresh_from_db()
    assert original_note.title == "Original"
    assert original_folder.name == "Original Folder"
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED).count() == 0
    )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_restore_vs_restore_serializes_and_final_state_is_one_complete_restore():
    owner = create_account("restore-vs-restore-owner")

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_delete = library_backup_restore._delete_current_library
    call_count = {"n": 0}

    def pausing_delete(owner_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second restore never proceeded"
        return original_delete(owner_arg)

    manifest_a = _manifest(notes=(_note("note-000001", "From A"),))
    manifest_b = _manifest(notes=(_note("note-000001", "From B"),))

    library_backup_restore._delete_current_library = pausing_delete
    try:
        outcome_a = {}
        outcome_b = {}

        def run_a():
            try:
                outcome_a["result"] = restore_library_backup_for_owner(
                    owner, manifest_a, request=None
                )
            except Exception as exc:  # pragma: no cover - failure path only
                outcome_a["error"] = exc
            finally:
                connections.close_all()

        thread_a = _run_in_thread(run_a)
        assert lock_acquired.wait(timeout=5), "first restore never reached delete"

        def run_b():
            try:
                outcome_b["result"] = restore_library_backup_for_owner(
                    owner, manifest_b, request=None
                )
            except Exception as exc:  # pragma: no cover - failure path only
                outcome_b["error"] = exc
            finally:
                connections.close_all()

        thread_b = _run_in_thread(run_b)
        time.sleep(0.3)
        proceed.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
    finally:
        library_backup_restore._delete_current_library = original_delete

    assert "result" in outcome_a
    assert "result" in outcome_b
    # Both restores complete (owner-row lock serializes them, neither
    # errors); final state is whichever committed last -- exactly one
    # note, with a title from one of the two manifests, and exactly two
    # audit events (one per completed restore).
    notes_after = list(Note.objects.filter(owner=owner))
    assert len(notes_after) == 1
    assert notes_after[0].title in ("From A", "From B")
    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED).count() == 2
    )


@pytest.mark.django_db(transaction=True)
def test_restore_vs_new_note_create_commit_blocks_until_restore_finishes():
    owner = create_account("restore-vs-create-owner")

    lock_acquired = threading.Event()
    proceed = threading.Event()
    original_delete = library_backup_restore._delete_current_library

    def pausing_delete(owner_arg):
        lock_acquired.set()
        assert proceed.wait(timeout=5), "concurrent create never proceeded"
        return original_delete(owner_arg)

    library_backup_restore._delete_current_library = pausing_delete
    try:
        manifest = _manifest(notes=(_note("note-000001", "Restored"),))
        restore_outcome = {}

        def run_restore():
            try:
                restore_outcome["result"] = restore_library_backup_for_owner(
                    owner, manifest, request=None
                )
            finally:
                connections.close_all()

        thread = _run_in_thread(run_restore)
        assert lock_acquired.wait(timeout=5), "restore never reached delete"

        create_outcome = {}

        def run_create():
            try:
                note = services.create_note(owner=owner)
                create_outcome["note_id"] = note.id
            except Exception as exc:  # pragma: no cover - diagnostic only
                create_outcome["error"] = exc
            finally:
                connections.close_all()

        create_thread = _run_in_thread(run_create)
        # Real proof of blocking (not merely "the thread eventually
        # finished"): join with a short timeout while restore is still
        # deliberately paused mid-transaction, holding the owner row
        # locked. create_note()'s own commit must still be blocked by
        # the deferred foreign-key check on the locked owner row, so
        # the thread must still be alive here.
        create_thread.join(timeout=2)
        assert create_thread.is_alive(), (
            "concurrent create committed before restore released the owner-row lock"
        )

        proceed.set()
        thread.join(timeout=5)
        create_thread.join(timeout=5)
    finally:
        library_backup_restore._delete_current_library = original_delete

    assert "result" in restore_outcome
    # Whichever order the two transactions ultimately committed in, the
    # final state must be internally consistent: either the restored
    # note alone (create's row was deleted along with everything else),
    # or the restored note plus the concurrently created one (create
    # committed after restore finished) -- never a partial/mixed state
    # and never an unexpected IntegrityError.
    final_notes = list(Note.objects.filter(owner=owner))
    assert len(final_notes) in (1, 2)
    assert any(note.title == "Restored" for note in final_notes)


# ---------------------------------------------------------------------------
# Stale-tab safety after restore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_old_note_id_gone_after_restore_new_note_unaffected():
    owner = create_account("stale-tab-owner")
    old_note = services.create_note(owner=owner)
    old_note_id = old_note.id

    manifest = _manifest(notes=(_note("note-000001", "Restored Note"),))
    restore_library_backup_for_owner(owner, manifest, request=None)

    with pytest.raises(Note.DoesNotExist):
        Note.objects.get(pk=old_note_id, owner=owner)

    new_note = Note.objects.get(owner=owner)
    assert new_note.pk != old_note_id
    assert new_note.title == "Restored Note"
