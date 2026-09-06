"""Full-overwrite owner-library restore.

Owns everything needed to atomically replace one owner's entire
RidgeNote library (folders, tags, notes, relationships, pin state,
lifecycle state, timestamps, and the recovery-folder marker) with the
contents of an already-validated, already-parsed
`notes.library_backup.LibraryBackupManifest` -- restore-specific
semantic pre-validation, the locking/transaction strategy, deletion,
recreation, export-ID-to-new-PK mapping, timestamp restoration, and
the content-free audit event.

Deliberately excluded: HTTP/view/form logic, multipart parsing, ZIP
validation (owned by `notes.library_backup_upload`), and Markdown of
any kind -- only native `body_json` is ever restored. No PostgreSQL
advisory lock is used -- ordinary relational locks (an owner-row
`select_for_update()` plus row-level locks over every current owner
row) are proven sufficient by a dedicated concurrency probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from notes import documents
from notes.library_backup import LIFECYCLE_ACTIVE, LibraryBackupManifest
from notes.models import Folder, Note, Tag

_BULK_BATCH_SIZE = 1000


class LibraryBackupRestoreValidationError(Exception):
    """Raised by restore-specific semantic pre-validation, before any
    destructive database operation begins. Carries only a safe,
    machine-readable `code` -- never a folder/tag name, note title,
    ID, path, or body."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class LibraryBackupRestoreResult:
    restored_at: datetime
    folder_count: int
    tag_count: int
    note_count: int


# ---------------------------------------------------------------------------
# Restore-specific semantic pre-validation (no database access)
# ---------------------------------------------------------------------------


def validate_library_backup_for_restore(manifest: LibraryBackupManifest) -> None:
    """Runs the restore-specific semantic checks not already guaranteed
    by `notes.library_backup.parse_library_backup_manifest()`:
    case-insensitive active-folder name collisions, case-insensitive
    tag name collisions, at most one recovery-folder marker, and no
    active note referencing a trashed folder. Raises
    `LibraryBackupRestoreValidationError` on the first failure. Pure
    inspection of the parsed manifest -- no database access, safe to
    call before any transaction begins."""
    seen_folder_names: set[str] = set()
    for folder in manifest.folders:
        if folder.lifecycle != LIFECYCLE_ACTIVE:
            continue
        key = folder.name.lower()
        if key in seen_folder_names:
            raise LibraryBackupRestoreValidationError(
                "duplicate_active_folder_name",
                "The backup contains two active folders with the same name.",
            )
        seen_folder_names.add(key)

    seen_tag_names: set[str] = set()
    for tag in manifest.tags:
        key = tag.name.lower()
        if key in seen_tag_names:
            raise LibraryBackupRestoreValidationError(
                "duplicate_tag_name", "The backup contains two tags with the same name."
            )
        seen_tag_names.add(key)

    marker_count = sum(1 for folder in manifest.folders if folder.is_recovery_folder)
    if marker_count > 1:
        raise LibraryBackupRestoreValidationError(
            "multiple_recovery_markers",
            "The backup contains more than one recovery-folder marker.",
        )

    # An active note must never reference a
    # trashed folder -- the reverse (a trashed note referencing an
    # active folder) is normal and stays allowed. Reject rather than
    # silently normalize to Unfiled or reactivate the folder, matching
    # this function's existing reject-invalid-state posture.
    folder_lifecycle_by_id = {folder.id: folder.lifecycle for folder in manifest.folders}
    for note in manifest.notes:
        if note.lifecycle != LIFECYCLE_ACTIVE or note.folder_id is None:
            continue
        if folder_lifecycle_by_id.get(note.folder_id) != LIFECYCLE_ACTIVE:
            raise LibraryBackupRestoreValidationError(
                "active_note_references_trashed_folder",
                "The backup contains an active note referencing a trashed folder.",
            )


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def _delete_current_library(owner: User) -> None:
    # Note.folder uses on_delete=RESTRICT, so every Note must be
    # deleted before any Folder; Tag has no incoming RESTRICT and is
    # safe to delete last. Note-Tag M2M through rows disappear via
    # Django's own delete collector when the Note side is deleted.
    Note.objects.filter(owner=owner).delete()
    Folder.objects.filter(owner=owner).delete()
    Tag.objects.filter(owner=owner).delete()


# ---------------------------------------------------------------------------
# Recreation
# ---------------------------------------------------------------------------


def _create_folders(owner: User, manifest: LibraryBackupManifest) -> dict[str, Folder]:
    created = [
        Folder(
            owner=owner,
            name=folder.name,
            trashed_at=folder.trashed_at,
            emptied_at=folder.emptied_at,
            is_recovery_folder=folder.is_recovery_folder,
            created_at=folder.created_at,
        )
        for folder in manifest.folders
    ]
    Folder.objects.bulk_create(created, batch_size=_BULK_BATCH_SIZE)
    # bulk_create() still invokes auto_now_add's pre_save(), silently
    # overwriting created_at with "now" -- confirmed by direct runtime
    # probe. bulk_update() does not call
    # pre_save() at all, so reassigning the archived value and
    # correcting via bulk_update() is the only path that preserves it.
    for manifest_folder, folder in zip(manifest.folders, created, strict=True):
        folder.created_at = manifest_folder.created_at
    Folder.objects.bulk_update(created, ["created_at"], batch_size=_BULK_BATCH_SIZE)
    return {
        manifest_folder.id: folder
        for manifest_folder, folder in zip(manifest.folders, created, strict=True)
    }


def _create_tags(owner: User, manifest: LibraryBackupManifest) -> dict[str, Tag]:
    # `tag.color` is always a valid, defaulted value by the time it
    # reaches here (the manifest parser guarantees this -- see
    # notes/library_backup.py's _parse_tags()), so Django's own
    # Tag.color model default must never be what actually applies for
    # any manifest-carried tag.
    created = [Tag(owner=owner, name=tag.name, color=tag.color) for tag in manifest.tags]
    Tag.objects.bulk_create(created, batch_size=_BULK_BATCH_SIZE)
    return {manifest_tag.id: tag for manifest_tag, tag in zip(manifest.tags, created, strict=True)}


def _create_notes(
    owner: User, manifest: LibraryBackupManifest, folder_map: dict[str, Folder]
) -> list[Note]:
    created = [
        Note(
            owner=owner,
            folder=folder_map[note.folder_id] if note.folder_id is not None else None,
            title=note.title,
            body_json=note.body_json,
            body_plain_text=documents.derive_plain_text(note.body_json),
            editor_schema_version=documents.EDITOR_SCHEMA_VERSION,
            version=1,
            pinned=note.pinned,
            created_at=note.created_at,
            modified_at=note.modified_at,
            trashed_at=note.trashed_at,
            emptied_at=note.emptied_at,
        )
        for note in manifest.notes
    ]
    Note.objects.bulk_create(created, batch_size=_BULK_BATCH_SIZE)
    # Same auto_now/auto_now_add correction as folders, plus
    # modified_at (auto_now, rewritten on every ordinary save/insert).
    for manifest_note, note in zip(manifest.notes, created, strict=True):
        note.created_at = manifest_note.created_at
        note.modified_at = manifest_note.modified_at
    Note.objects.bulk_update(created, ["created_at", "modified_at"], batch_size=_BULK_BATCH_SIZE)
    return created


def _create_note_tag_relations(
    manifest: LibraryBackupManifest, notes: list[Note], tag_map: dict[str, Tag]
) -> None:
    through_model = Note.tags.through
    rows = [
        through_model(note_id=note.pk, tag_id=tag_map[tag_id].pk)
        for note, manifest_note in zip(notes, manifest.notes, strict=True)
        for tag_id in manifest_note.tag_ids
    ]
    through_model.objects.bulk_create(rows, batch_size=_BULK_BATCH_SIZE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def restore_library_backup_for_owner(
    owner: User,
    manifest: LibraryBackupManifest,
    *,
    request: HttpRequest,
) -> LibraryBackupRestoreResult:
    """Atomically replaces `owner`'s entire library with `manifest`'s
    contents. `manifest` must already be fully validated -- both by
    `notes.library_backup_upload.load_library_backup_for_restore()`
    and by `validate_library_backup_for_restore()` above -- this
    function performs no further ZIP/manifest-level validation, only
    the destructive mutation itself, inside one transaction.

    Locking: an owner-row `select_for_update()` plus row-level
    `select_for_update()` over every one of the owner's current
    Folder/Note/Tag rows -- no PostgreSQL advisory lock. The owner-row
    lock alone serializes concurrent restores for the same owner and
    blocks any concurrent same-owner create from committing (via
    PostgreSQL's deferred foreign-key constraint check), confirmed by
    direct concurrency probe.

    Writes exactly one content-free `EVENT_LIBRARY_BACKUP_RESTORED`
    audit event, inside the same transaction, after every recreated
    row and relationship exists -- if the audit write fails, the
    entire restore rolls back. Any exception anywhere in this function
    propagates after the transaction rolls back completely; there is
    no custom rollback logic."""
    with transaction.atomic():
        locked_owner = User.objects.select_for_update().get(pk=owner.pk)
        # Materialized (not merely filtered) so the SELECT FOR UPDATE
        # actually executes and locks every current row before any
        # deletion begins.
        list(Note.objects.select_for_update().filter(owner=locked_owner))
        list(Folder.objects.select_for_update().filter(owner=locked_owner))
        list(Tag.objects.select_for_update().filter(owner=locked_owner))

        _delete_current_library(locked_owner)

        folder_map = _create_folders(locked_owner, manifest)
        tag_map = _create_tags(locked_owner, manifest)
        notes = _create_notes(locked_owner, manifest, folder_map)
        _create_note_tag_relations(manifest, notes, tag_map)

        marker_count = Folder.objects.filter(owner=locked_owner, is_recovery_folder=True).count()
        if marker_count > 1:
            raise LibraryBackupRestoreValidationError(
                "multiple_recovery_markers",
                "The backup contains more than one recovery-folder marker.",
            )

        account_services.record_audit_event(
            AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED,
            actor=locked_owner,
            target_user=locked_owner,
            request=request,
        )

        result = LibraryBackupRestoreResult(
            restored_at=timezone.now(),
            folder_count=len(folder_map),
            tag_count=len(tag_map),
            note_count=len(notes),
        )
    return result
