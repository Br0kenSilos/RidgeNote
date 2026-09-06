"""Library-backup manifest and archive-path foundation.

This module owns the version-1 library-backup manifest: its schema
constants, an owner-scoped serializer (`build_library_backup_manifest`),
a decoded-mapping parser/validator (`parse_library_backup_manifest`),
and the frozen, normalized result types both produce/consume. It also
provides archive path-segment sanitization
(`sanitize_backup_path_segment`) and deterministic note-to-Markdown-path
assignment (`build_backup_markdown_paths`), and the note contract
includes a required `markdown_path` field.

Deliberately excluded from this module: JSON text/bytes decoding,
Markdown *rendering* (owned by `notes.markdown` instead), ZIP
generation/parsing, any route, view, or UI, and any restore/mutation
logic. This module is not imported by any route or view -- it is
reachable only from its own tests today.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from notes import documents
from notes.models import DEFAULT_TAG_COLOR, TAG_COLOR_VALUES, Folder, Note, Tag

LIBRARY_BACKUP_FORMAT_IDENTIFIER = "ridgenote-library-backup"
LIBRARY_BACKUP_FORMAT_VERSION = 1
LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT = 50

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_TRASH = "trash"
LIFECYCLE_RECOVERY = "recovery"
LIFECYCLE_STATES = frozenset({LIFECYCLE_ACTIVE, LIFECYCLE_TRASH, LIFECYCLE_RECOVERY})

_FOLDER_ID_PREFIX = "folder"
_TAG_ID_PREFIX = "tag"
_NOTE_ID_PREFIX = "note"
_ID_WIDTH = 6

# ---------------------------------------------------------------------------
# Archive-path constants
# ---------------------------------------------------------------------------

_MARKDOWN_EXTENSION = ".md"
_UNFILED_SEGMENT = "Unfiled"
_UNTITLED_NOTE_FALLBACK = "Untitled"
_UNNAMED_FOLDER_FALLBACK = "Unnamed folder"

# Generic folder/path segments (used for folder-directory allocation,
# and what the public `sanitize_backup_path_segment()` enforces) are
# capped at 80. Note filename bases get their own, more generous 120
# cap -- applied by the private `_sanitize_note_filename_base()`
# below, which shares the public function's sanitization pipeline but
# truncates to a different length. The complete filename, including a
# collision suffix and the `.md` extension, is separately capped at
# 130 by `_allocate_unique_filename()`.
_MAX_SEGMENT_LENGTH = 80
_MAX_NOTE_FILENAME_BASE_LENGTH = 120
_MAX_FILENAME_LENGTH = 130
_MAX_ARCHIVE_PATH_LENGTH = 240
_COLLISION_SUFFIX_RESERVE = 7  # room for " (9999)"

# Shared with notes/library_backup_upload.py: these three
# names are deliberately not private, so the upload validator can reuse
# the exact same reserved-name/length/collision rules this module
# already established for writer-side paths, rather than duplicating
# them for reader-side (uploaded) archive member names.
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
MAX_ARCHIVE_PATH_LENGTH = _MAX_ARCHIVE_PATH_LENGTH

_LINE_BREAK_CONTROL_RE = re.compile(r"[\t\n\r\v\f]")
_OTHER_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_UNSAFE_CHARS_RE = re.compile(r'[/\\<>:"|?*]')
_REPLACEMENT_RUN_RE = re.compile(r"-{2,}")
_WHITESPACE_RUN_RE = re.compile(r" {2,}")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_TOP_LEVEL_KEYS = frozenset(
    {
        "format_identifier",
        "format_version",
        "exported_at",
        "scope",
        "folders",
        "notes",
        "tags",
    }
)
_SCOPE_KEYS = frozenset({"active", "trash", "recovery"})
_FOLDER_KEYS = frozenset(
    {"id", "name", "lifecycle", "trashed_at", "emptied_at", "is_recovery_folder", "created_at"}
)
_TAG_KEYS = frozenset({"id", "name"})
# Additive, backward-compatible field: allowed but never required,
# so any pre-correction version-1
# archive -- which never had this key at all -- remains fully parseable.
_TAG_OPTIONAL_KEYS = frozenset({"color"})
_NOTE_KEYS = frozenset(
    {
        "id",
        "title",
        "body_json",
        "folder_id",
        "tag_ids",
        "pinned",
        "lifecycle",
        "trashed_at",
        "emptied_at",
        "created_at",
        "modified_at",
        "markdown_path",
    }
)


# ---------------------------------------------------------------------------
# Frozen normalized result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryBackupFolder:
    id: str
    name: str
    lifecycle: str
    trashed_at: datetime | None
    emptied_at: datetime | None
    is_recovery_folder: bool
    created_at: datetime


@dataclass(frozen=True)
class LibraryBackupTag:
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class LibraryBackupNote:
    id: str
    title: str
    body_json: dict[str, Any]
    folder_id: str | None
    tag_ids: tuple[str, ...]
    pinned: bool
    lifecycle: str
    trashed_at: datetime | None
    emptied_at: datetime | None
    created_at: datetime
    modified_at: datetime
    markdown_path: str


@dataclass(frozen=True)
class LibraryBackupManifest:
    format_identifier: str
    format_version: int
    exported_at: datetime
    scope: tuple[str, ...]
    folders: tuple[LibraryBackupFolder, ...]
    tags: tuple[LibraryBackupTag, ...]
    notes: tuple[LibraryBackupNote, ...]


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryBackupValidationErrorRecord:
    code: str
    message: str
    path: str | None = None


class LibraryBackupValidationError(Exception):
    """Raised by `parse_library_backup_manifest()` when the input fails
    structural or semantic validation. Carries up to
    `LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT` accumulated error records
    (never note titles, bodies, or other manifest content) rather than
    failing on the first problem found, so a future preview UI can show
    everything wrong with an archive at once."""

    def __init__(self, errors: tuple[LibraryBackupValidationErrorRecord, ...], *, truncated: bool):
        self.errors = errors
        self.truncated = truncated
        super().__init__(f"{len(errors)} library backup validation error(s)")


class LibraryBackupSnapshotNestedTransactionError(Exception):
    """Raised by `build_library_backup_manifest()` when it is called from
    inside an already-active `transaction.atomic()` block. This is an
    internal programming-contract violation, not a user-facing
    validation error: the manifest snapshot needs a real, dedicated
    PostgreSQL transaction so it can set `REPEATABLE READ` isolation as
    that transaction's first statement -- a nested `atomic()` call only
    creates a savepoint on the caller's already-open transaction, which
    cannot have its isolation level changed at all once a statement has
    already run on it. Callers must invoke this function from outside
    any existing transaction."""


class _ErrorCollector:
    """Accumulates validation errors up to the module's cap, then stops
    collecting (but does not raise) so validation can keep scanning for
    a stable, complete report shape rather than aborting on the first
    error found deep inside a large manifest."""

    def __init__(self) -> None:
        self._errors: list[LibraryBackupValidationErrorRecord] = []

    def add(self, code: str, message: str, *, path: str | None = None) -> None:
        if len(self._errors) >= LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT:
            return
        self._errors.append(
            LibraryBackupValidationErrorRecord(code=code, message=message, path=path)
        )

    @property
    def has_errors(self) -> bool:
        return bool(self._errors)

    @property
    def truncated(self) -> bool:
        return len(self._errors) >= LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT

    def raise_if_any(self) -> None:
        if self._errors:
            raise LibraryBackupValidationError(tuple(self._errors), truncated=self.truncated)


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


def _isoformat_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _lifecycle_for(*, trashed_at: datetime | None, emptied_at: datetime | None) -> str:
    if trashed_at is None:
        return LIFECYCLE_ACTIVE
    if emptied_at is None:
        return LIFECYCLE_TRASH
    return LIFECYCLE_RECOVERY


def build_library_backup_manifest(owner, *, exported_at: datetime | None = None) -> dict[str, Any]:
    """Serializes `owner`'s complete RidgeNote library (active, Trash,
    and recovery content) into a plain, JSON-serializable version-1
    library-backup manifest. Read-only: no file I/O, no audit event, no
    mutation, and independent of any `HttpRequest`. `exported_at`, when
    omitted, defaults to the current timezone-aware time; when supplied,
    must already be timezone-aware (never assumed naive-as-UTC).

 all reads happen inside one dedicated,
    short-lived PostgreSQL transaction with `REPEATABLE READ` isolation
    (set as that transaction's first statement, before any ORM query),
    so the Folder/Tag/Note rows -- and every relationship between them
    -- are read from a single coherent MVCC snapshot, never a mixture of
    states from a concurrent write or Library Restore. The transaction
    is entered and exited entirely within this call; nothing outside
    this function ever runs inside it, and no lazy queryset escapes it.
    Raises `LibraryBackupSnapshotNestedTransactionError` if called from
    inside an already-active `transaction.atomic()` block -- see that
    exception's docstring."""
    if connection.in_atomic_block:
        raise LibraryBackupSnapshotNestedTransactionError(
            "build_library_backup_manifest() must not be called from inside an "
            "already-active transaction.atomic() block."
        )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return _build_library_backup_manifest_snapshot(owner, exported_at=exported_at)


def _select_folders_for_snapshot(*, owner) -> list[Folder]:
    return list(Folder.objects.filter(owner=owner).order_by(Lower("name").asc(), "id"))


def _select_tags_for_snapshot(*, owner) -> list[Tag]:
    return list(Tag.objects.filter(owner=owner).order_by(Lower("name").asc(), "id"))


def _select_notes_for_snapshot(*, owner) -> list[Note]:
    return list(
        Note.objects.filter(owner=owner)
        .select_related("folder")
        .prefetch_related("tags")
        .order_by("-modified_at", "-id")
    )


def _build_library_backup_manifest_snapshot(
    owner, *, exported_at: datetime | None
) -> dict[str, Any]:
    """The actual manifest-assembly reads and serialization, run only
    from inside `build_library_backup_manifest()`'s own `REPEATABLE
    READ` transaction above. Not part of the public contract -- callers
    must always go through `build_library_backup_manifest()` so the
    snapshot guarantee cannot be bypassed by accident."""
    if exported_at is None:
        exported_at = timezone.now()

    folders = _select_folders_for_snapshot(owner=owner)
    tags = _select_tags_for_snapshot(owner=owner)
    notes = _select_notes_for_snapshot(owner=owner)

    folder_export_ids = {
        folder.id: f"{_FOLDER_ID_PREFIX}-{index:0{_ID_WIDTH}d}"
        for index, folder in enumerate(folders, start=1)
    }
    tag_export_ids = {
        tag.id: f"{_TAG_ID_PREFIX}-{index:0{_ID_WIDTH}d}" for index, tag in enumerate(tags, start=1)
    }

    folder_records = [
        LibraryBackupFolder(
            id=folder_export_ids[folder.id],
            name=folder.name,
            lifecycle=_lifecycle_for(trashed_at=folder.trashed_at, emptied_at=folder.emptied_at),
            trashed_at=folder.trashed_at,
            emptied_at=folder.emptied_at,
            is_recovery_folder=folder.is_recovery_folder,
            created_at=folder.created_at,
        )
        for folder in folders
    ]

    tag_records = [
        LibraryBackupTag(id=tag_export_ids[tag.id], name=tag.name, color=tag.color) for tag in tags
    ]

    note_records = [
        LibraryBackupNote(
            id=f"{_NOTE_ID_PREFIX}-{index:0{_ID_WIDTH}d}",
            title=note.title,
            body_json=note.body_json,
            folder_id=folder_export_ids[note.folder_id] if note.folder_id else None,
            tag_ids=tuple(tag_export_ids[tag.id] for tag in note.tags.all()),
            pinned=note.pinned,
            lifecycle=_lifecycle_for(trashed_at=note.trashed_at, emptied_at=note.emptied_at),
            trashed_at=note.trashed_at,
            emptied_at=note.emptied_at,
            created_at=note.created_at,
            modified_at=note.modified_at,
            markdown_path="",
        )
        for index, note in enumerate(notes, start=1)
    ]

    preliminary_manifest = LibraryBackupManifest(
        format_identifier=LIBRARY_BACKUP_FORMAT_IDENTIFIER,
        format_version=LIBRARY_BACKUP_FORMAT_VERSION,
        exported_at=exported_at,
        scope=(LIFECYCLE_ACTIVE, LIFECYCLE_TRASH, LIFECYCLE_RECOVERY),
        folders=tuple(folder_records),
        tags=tuple(tag_records),
        notes=tuple(note_records),
    )
    markdown_paths = build_backup_markdown_paths(preliminary_manifest)

    folder_payloads = [
        {
            "id": folder.id,
            "name": folder.name,
            "lifecycle": folder.lifecycle,
            "trashed_at": _isoformat_utc(folder.trashed_at) if folder.trashed_at else None,
            "emptied_at": _isoformat_utc(folder.emptied_at) if folder.emptied_at else None,
            "is_recovery_folder": folder.is_recovery_folder,
            "created_at": _isoformat_utc(folder.created_at),
        }
        for folder in folder_records
    ]

    tag_payloads = [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tag_records]

    note_payloads = [
        {
            "id": note.id,
            "title": note.title,
            "body_json": note.body_json,
            "folder_id": note.folder_id,
            "tag_ids": list(note.tag_ids),
            "pinned": note.pinned,
            "lifecycle": note.lifecycle,
            "trashed_at": _isoformat_utc(note.trashed_at) if note.trashed_at else None,
            "emptied_at": _isoformat_utc(note.emptied_at) if note.emptied_at else None,
            "created_at": _isoformat_utc(note.created_at),
            "modified_at": _isoformat_utc(note.modified_at),
            "markdown_path": markdown_paths[note.id],
        }
        for note in note_records
    ]

    return {
        "format_identifier": LIBRARY_BACKUP_FORMAT_IDENTIFIER,
        "format_version": LIBRARY_BACKUP_FORMAT_VERSION,
        "exported_at": _isoformat_utc(exported_at),
        "scope": {"active": True, "trash": True, "recovery": True},
        "folders": folder_payloads,
        "notes": note_payloads,
        "tags": tag_payloads,
    }


# ---------------------------------------------------------------------------
# Archive path-segment sanitization and Markdown-path allocation
# ---------------------------------------------------------------------------


def sanitize_backup_path_segment(value: str, *, fallback: str) -> str:
    """Returns `value` transformed into one safe, generic archive path
    segment (a folder name, capped at `_MAX_SEGMENT_LENGTH`) usable on
    Windows, Linux, and macOS: NFC-normalized, free of control
    characters and reserved/unsafe characters, never a hidden dotfile,
    never a Windows-reserved device name, length-bounded, and never
    empty (`fallback` is used when the result would otherwise be empty
    or degenerate, e.g. `.`, `..`, or whitespace-only input). Note
    filename bases use the separate, more generous private
    `_sanitize_note_filename_base()` below instead of this function,
    per the authorized 80/120/130 length contract."""
    return _sanitize_segment(value, fallback=fallback, max_length=_MAX_SEGMENT_LENGTH)


def _sanitize_note_filename_base(value: str, *, fallback: str) -> str:
    """Same sanitization pipeline as `sanitize_backup_path_segment()`,
    but truncated to `_MAX_NOTE_FILENAME_BASE_LENGTH` (120) instead of
    the generic 80-character segment cap -- note filename bases are
    authorized a larger ceiling than ordinary folder segments."""
    return _sanitize_segment(value, fallback=fallback, max_length=_MAX_NOTE_FILENAME_BASE_LENGTH)


def _sanitize_segment(value: str, *, fallback: str, max_length: int) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = _LINE_BREAK_CONTROL_RE.sub(" ", normalized)
    normalized = _OTHER_CONTROL_RE.sub("", normalized)
    normalized = _PATH_UNSAFE_CHARS_RE.sub("-", normalized)
    normalized = _REPLACEMENT_RUN_RE.sub("-", normalized)
    normalized = _WHITESPACE_RUN_RE.sub(" ", normalized)
    normalized = normalized.strip()
    normalized = normalized.rstrip(". ")

    if normalized in ("", ".", ".."):
        normalized = fallback

    if normalized.startswith("."):
        normalized = "_" + normalized[1:]

    normalized = _protect_reserved_windows_name(normalized)
    normalized = _truncate_segment(normalized, max_length=max_length)

    if not normalized:
        normalized = fallback

    return normalized


def _protect_reserved_windows_name(segment: str) -> str:
    stem = segment.split(".", 1)[0]
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        return f"_{segment}"
    return segment


def _truncate_segment(segment: str, *, max_length: int) -> str:
    if len(segment) <= max_length:
        return segment
    truncated = segment[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.6:
        truncated = truncated[:last_space]
    return truncated.rstrip(". ") or truncated


def _collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().rstrip(". ")


# Public alias -- shared with notes/library_backup_upload.py,
# which needs the exact same case/Unicode-insensitive collision rule
# for raw uploaded archive member names.
collision_key = _collision_key


def _allocate_unique_directory_name(base: str, used_keys: set[str]) -> str:
    if _collision_key(base) not in used_keys:
        return base
    attempt = 2
    while True:
        suffix = f" ({attempt})"
        max_base_len = _MAX_SEGMENT_LENGTH - len(suffix)
        trimmed_base = base[:max_base_len].rstrip(". ") if len(base) > max_base_len else base
        candidate = f"{trimmed_base}{suffix}"
        if _collision_key(candidate) not in used_keys:
            return candidate
        attempt += 1


def _allocate_unique_filename(base: str, used_keys: set[str], *, extension: str) -> str:
    # `base` comes from `_sanitize_note_filename_base()` and is already
    # <= _MAX_NOTE_FILENAME_BASE_LENGTH (120), so the unsuffixed
    # candidate (base + ".md") is always comfortably under the
    # authorized 130-character final-filename cap without needing
    # trimming here. The cap is still enforced defensively in case a
    # caller ever passes a longer base directly.
    max_unsuffixed_len = _MAX_FILENAME_LENGTH - len(extension)
    if len(base) > max_unsuffixed_len:
        trimmed_base = base[:max_unsuffixed_len].rstrip(". ")
    else:
        trimmed_base = base
    candidate = f"{trimmed_base}{extension}"
    if _collision_key(candidate) not in used_keys:
        used_keys.add(_collision_key(candidate))
        return candidate
    attempt = 2
    while True:
        suffix = f" ({attempt})"
        max_base_len = _MAX_FILENAME_LENGTH - len(suffix) - len(extension)
        trimmed_base = base[:max_base_len].rstrip(". ") if len(base) > max_base_len else base
        candidate = f"{trimmed_base}{suffix}{extension}"
        if _collision_key(candidate) not in used_keys:
            used_keys.add(_collision_key(candidate))
            return candidate
        attempt += 1


def _assign_folder_segments(
    folder_by_id: dict[str, LibraryBackupFolder],
    notes_by_lifecycle: dict[str, list[LibraryBackupNote]],
) -> dict[tuple[str, str | None], str]:
    # Folder-segment allocation happens once per folder per lifecycle
    # root, scoped independently to each root -- never once globally --
    # so the same real folder can legitimately receive the same
    # readable segment under more than one lifecycle root (e.g. a
    # trashed note referencing a still-active folder).
    segments: dict[tuple[str, str | None], str] = {}
    for lifecycle in (LIFECYCLE_ACTIVE, LIFECYCLE_TRASH, LIFECYCLE_RECOVERY):
        used_keys: set[str] = {_collision_key(_UNFILED_SEGMENT)}
        segments[(lifecycle, None)] = _UNFILED_SEGMENT

        seen_folder_ids: set[str] = set()
        ordered_folder_ids: list[str] = []
        for note in notes_by_lifecycle.get(lifecycle, []):
            if note.folder_id is not None and note.folder_id not in seen_folder_ids:
                seen_folder_ids.add(note.folder_id)
                ordered_folder_ids.append(note.folder_id)

        for folder_id in ordered_folder_ids:
            folder = folder_by_id.get(folder_id)
            raw_name = folder.name if folder is not None else _UNNAMED_FOLDER_FALLBACK
            base = sanitize_backup_path_segment(raw_name, fallback=_UNNAMED_FOLDER_FALLBACK)
            segment = _allocate_unique_directory_name(base, used_keys)
            used_keys.add(_collision_key(segment))
            segments[(lifecycle, folder_id)] = segment
    return segments


def _enforce_path_length(path: str, *, lifecycle: str, folder_segment: str, filename: str) -> str:
    if len(path) <= _MAX_ARCHIVE_PATH_LENGTH:
        return path
    root = f"notes/{lifecycle}/{folder_segment}/"
    available = max(_MAX_ARCHIVE_PATH_LENGTH - len(root) - len(_MARKDOWN_EXTENSION), 1)
    if filename.endswith(_MARKDOWN_EXTENSION):
        stem = filename[: -len(_MARKDOWN_EXTENSION)]
    else:
        stem = filename
    shortened_stem = stem[:available].rstrip(". ") or "N"
    return f"{root}{shortened_stem}{_MARKDOWN_EXTENSION}"


def build_backup_markdown_paths(manifest: LibraryBackupManifest) -> dict[str, str]:
    """Pure function: assigns every note in `manifest` a safe, unique,
    relative Markdown archive path
    (`notes/<lifecycle>/<folder-or-Unfiled>/<filename>.md`), using only
    the already-normalized manifest data -- no database queries, no
    file I/O. Placement follows each note's own lifecycle, not its
    referenced folder's lifecycle, so a trashed or recovery note that
    still references an active folder is placed under its own
    lifecycle root using that folder's name. Deterministic across
    repeated calls over the same manifest; returns note export ID ->
    relative path."""
    folder_by_id = {folder.id: folder for folder in manifest.folders}

    notes_by_lifecycle: dict[str, list[LibraryBackupNote]] = {
        LIFECYCLE_ACTIVE: [],
        LIFECYCLE_TRASH: [],
        LIFECYCLE_RECOVERY: [],
    }
    for note in manifest.notes:
        notes_by_lifecycle.setdefault(note.lifecycle, []).append(note)

    folder_segments = _assign_folder_segments(folder_by_id, notes_by_lifecycle)

    paths: dict[str, str] = {}
    for lifecycle, notes in notes_by_lifecycle.items():
        used_note_keys: dict[tuple[str, str | None], set[str]] = {}
        for note in notes:
            directory_key = (lifecycle, note.folder_id)
            folder_segment = folder_segments[directory_key]
            used_keys = used_note_keys.setdefault(directory_key, set())
            title_base = _sanitize_note_filename_base(note.title, fallback=_UNTITLED_NOTE_FALLBACK)
            filename = _allocate_unique_filename(
                title_base, used_keys, extension=_MARKDOWN_EXTENSION
            )
            path = f"notes/{lifecycle}/{folder_segment}/{filename}"
            path = _enforce_path_length(
                path, lifecycle=lifecycle, folder_segment=folder_segment, filename=filename
            )
            paths[note.id] = path
    return paths


# ---------------------------------------------------------------------------
# Parser / validator
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, *, path: str, errors: _ErrorCollector) -> bool:
    if not isinstance(value, dict):
        errors.add("invalid_type", "Expected an object.", path=path)
        return False
    return True


def _require_string(
    value: Any, *, path: str, errors: _ErrorCollector, allow_empty: bool = False
) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        errors.add("invalid_type", "Expected a string.", path=path)
        return False
    if not allow_empty and not value.strip():
        errors.add("invalid_value", "Value must not be blank.", path=path)
        return False
    return True


def _require_bool(value: Any, *, path: str, errors: _ErrorCollector) -> bool:
    if not isinstance(value, bool):
        errors.add("invalid_type", "Expected a boolean.", path=path)
        return False
    return True


def _require_list(value: Any, *, path: str, errors: _ErrorCollector) -> bool:
    if not isinstance(value, list):
        errors.add("invalid_type", "Expected an array.", path=path)
        return False
    return True


def _reject_unknown_keys(
    value: dict,
    allowed: frozenset[str],
    *,
    path: str,
    errors: _ErrorCollector,
    optional: frozenset[str] = frozenset(),
) -> None:
    extra = set(value) - allowed - optional
    if extra:
        errors.add(
            "unknown_fields",
            f"Unknown fields: {sorted(extra)!r}.",
            path=path,
        )
    missing = allowed - set(value)
    if missing:
        errors.add(
            "missing_fields",
            f"Missing required fields: {sorted(missing)!r}.",
            path=path,
        )


def _parse_timestamp(
    value: Any, *, path: str, errors: _ErrorCollector, required: bool
) -> datetime | None:
    if value is None:
        if required:
            errors.add("missing_value", "Timestamp is required.", path=path)
        return None
    if not isinstance(value, str):
        errors.add("invalid_type", "Timestamp must be a string.", path=path)
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.add("malformed_timestamp", "Timestamp could not be parsed.", path=path)
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        errors.add("naive_timestamp", "Timestamp must be timezone-aware.", path=path)
        return None
    return parsed.astimezone(UTC)


def _validate_lifecycle_timestamps(
    *,
    lifecycle: str | None,
    trashed_at: datetime | None,
    trashed_at_present: bool,
    emptied_at: datetime | None,
    emptied_at_present: bool,
    path: str,
    errors: _ErrorCollector,
) -> None:
    if lifecycle not in LIFECYCLE_STATES:
        errors.add(
            "invalid_lifecycle", f"Unsupported lifecycle: {lifecycle!r}.", path=f"{path}.lifecycle"
        )
        return

    if lifecycle == LIFECYCLE_ACTIVE:
        if trashed_at_present and trashed_at is not None:
            errors.add(
                "lifecycle_mismatch",
                "Active entries must not have trashed_at.",
                path=f"{path}.trashed_at",
            )
        if emptied_at_present and emptied_at is not None:
            errors.add(
                "lifecycle_mismatch",
                "Active entries must not have emptied_at.",
                path=f"{path}.emptied_at",
            )
    elif lifecycle == LIFECYCLE_TRASH:
        if trashed_at is None:
            errors.add(
                "lifecycle_mismatch",
                "Trash entries require trashed_at.",
                path=f"{path}.trashed_at",
            )
        if emptied_at_present and emptied_at is not None:
            errors.add(
                "lifecycle_mismatch",
                "Trash entries must not have emptied_at.",
                path=f"{path}.emptied_at",
            )
    elif lifecycle == LIFECYCLE_RECOVERY:
        if trashed_at is None:
            errors.add(
                "lifecycle_mismatch",
                "Recovery entries require trashed_at.",
                path=f"{path}.trashed_at",
            )
        if emptied_at is None:
            errors.add(
                "lifecycle_mismatch",
                "Recovery entries require emptied_at.",
                path=f"{path}.emptied_at",
            )


def _parse_folders(
    raw_folders: list, *, errors: _ErrorCollector
) -> tuple[dict[str, LibraryBackupFolder], set[str]]:
    folders: dict[str, LibraryBackupFolder] = {}
    seen_ids: set[str] = set()
    for index, raw_folder in enumerate(raw_folders):
        path = f"folders[{index}]"
        if not _require_mapping(raw_folder, path=path, errors=errors):
            continue
        _reject_unknown_keys(raw_folder, _FOLDER_KEYS, path=path, errors=errors)

        folder_id = raw_folder.get("id")
        if not _require_string(folder_id, path=f"{path}.id", errors=errors):
            continue
        if not folder_id.startswith(f"{_FOLDER_ID_PREFIX}-"):
            errors.add("invalid_id", "Folder id has the wrong prefix.", path=f"{path}.id")
            continue
        if folder_id in seen_ids:
            errors.add("duplicate_id", "Duplicate folder id.", path=f"{path}.id")
            continue
        seen_ids.add(folder_id)

        name = raw_folder.get("name")
        _require_string(name, path=f"{path}.name", errors=errors)

        is_recovery_folder = raw_folder.get("is_recovery_folder")
        _require_bool(is_recovery_folder, path=f"{path}.is_recovery_folder", errors=errors)

        trashed_at_present = "trashed_at" in raw_folder
        emptied_at_present = "emptied_at" in raw_folder
        trashed_at = _parse_timestamp(
            raw_folder.get("trashed_at"), path=f"{path}.trashed_at", errors=errors, required=False
        )
        emptied_at = _parse_timestamp(
            raw_folder.get("emptied_at"), path=f"{path}.emptied_at", errors=errors, required=False
        )
        created_at = _parse_timestamp(
            raw_folder.get("created_at"), path=f"{path}.created_at", errors=errors, required=True
        )

        lifecycle = raw_folder.get("lifecycle")
        _validate_lifecycle_timestamps(
            lifecycle=lifecycle,
            trashed_at=trashed_at,
            trashed_at_present=trashed_at_present,
            emptied_at=emptied_at,
            emptied_at_present=emptied_at_present,
            path=path,
            errors=errors,
        )

        is_recovery = isinstance(is_recovery_folder, bool) and is_recovery_folder
        if is_recovery and lifecycle != LIFECYCLE_ACTIVE:
            errors.add(
                "invalid_recovery_marker",
                "Only an active folder may carry the recovery marker.",
                path=f"{path}.is_recovery_folder",
            )

        if created_at is None or not isinstance(name, str):
            continue

        folders[folder_id] = LibraryBackupFolder(
            id=folder_id,
            name=name,
            lifecycle=lifecycle if lifecycle in LIFECYCLE_STATES else LIFECYCLE_ACTIVE,
            trashed_at=trashed_at,
            emptied_at=emptied_at,
            is_recovery_folder=bool(is_recovery_folder),
            created_at=created_at,
        )
    return folders, seen_ids


def _parse_tags(
    raw_tags: list, *, errors: _ErrorCollector
) -> tuple[dict[str, LibraryBackupTag], set[str]]:
    tags: dict[str, LibraryBackupTag] = {}
    seen_ids: set[str] = set()
    for index, raw_tag in enumerate(raw_tags):
        path = f"tags[{index}]"
        if not _require_mapping(raw_tag, path=path, errors=errors):
            continue
        _reject_unknown_keys(
            raw_tag, _TAG_KEYS, path=path, errors=errors, optional=_TAG_OPTIONAL_KEYS
        )

        tag_id = raw_tag.get("id")
        if not _require_string(tag_id, path=f"{path}.id", errors=errors):
            continue
        if not tag_id.startswith(f"{_TAG_ID_PREFIX}-"):
            errors.add("invalid_id", "Tag id has the wrong prefix.", path=f"{path}.id")
            continue
        if tag_id in seen_ids:
            errors.add("duplicate_id", "Duplicate tag id.", path=f"{path}.id")
            continue
        seen_ids.add(tag_id)

        name = raw_tag.get("name")
        if not _require_string(name, path=f"{path}.name", errors=errors):
            continue

        # Additive, backward-compatible field: absent on any
        # pre-correction version-1
        # archive, which must remain fully parseable -- default to
        # DEFAULT_TAG_COLOR rather than reject. A *present* color must
        # be one of the model's own supported values; no separate
        # palette list is maintained here.
        if "color" in raw_tag:
            color = raw_tag.get("color")
            if not _require_string(color, path=f"{path}.color", errors=errors):
                continue
            if color not in TAG_COLOR_VALUES:
                errors.add(
                    "invalid_tag_color",
                    "Tag color is not a supported value.",
                    path=f"{path}.color",
                )
                continue
        else:
            color = DEFAULT_TAG_COLOR

        tags[tag_id] = LibraryBackupTag(id=tag_id, name=name, color=color)
    return tags, seen_ids


def check_archive_path_structural_safety(value: str) -> tuple[str, str] | None:
    """Structural archive-path safety checks shared between manifest
    `markdown_path` validation (below) and raw
    uploaded-archive member-name validation in
    `notes/library_backup_upload.py`: NFC normalization, relative (no
    leading `/`), no Windows drive prefix, no backslashes, bounded
    length, no empty/`.`/`..` segments, and no hidden-dot or
    Windows-reserved segments in any position. Does not check a file
    extension or a specific lifecycle root -- callers apply those
    separately, since only manifest `markdown_path` values have either
    concept. Returns `(code, message)` if `value` fails a check, or
    `None` if it passes every one."""
    if value != unicodedata.normalize("NFC", value):
        return ("non_canonical_path", "Path must be NFC-normalized.")
    if "\\" in value:
        return ("invalid_path", "Path must not contain backslashes.")
    if value.startswith("/"):
        return ("invalid_path", "Path must be relative.")
    if _WINDOWS_DRIVE_RE.match(value):
        return ("invalid_path", "Path must not include a drive prefix.")
    if len(value) > MAX_ARCHIVE_PATH_LENGTH:
        return ("path_too_long", "Path exceeds the maximum length.")

    segments = value.split("/")
    if any(segment == "" for segment in segments):
        return ("invalid_path", "Path must not contain empty segments.")
    if any(segment in (".", "..") for segment in segments):
        return ("invalid_path", "Path must not contain . or .. segments.")

    for segment in segments:
        if segment.startswith("."):
            return ("hidden_segment", "Path segments must not be hidden.")
        stem = segment.split(".", 1)[0]
        if stem.upper() in WINDOWS_RESERVED_NAMES:
            return ("reserved_name", "Path segment uses a reserved name.")

    return None


def _validate_markdown_path(
    value: Any,
    *,
    lifecycle: str | None,
    path: str,
    errors: _ErrorCollector,
    seen_path_keys: set[str],
) -> str | None:
    """Validates safety and structural consistency -- not literal
    reproduction of `build_backup_markdown_paths()`'s own output. A
    safe, unique, correctly-rooted `.md` path is accepted even if it
    differs from what the canonical sanitizer would have produced,
    keeping the contract forward-compatible with future externally
    produced manifests."""
    if not _require_string(value, path=path, errors=errors):
        return None

    if not value.endswith(_MARKDOWN_EXTENSION):
        errors.add("invalid_path", "markdown_path must end with .md.", path=path)
        return None

    safety_error = check_archive_path_structural_safety(value)
    if safety_error is not None:
        code, message = safety_error
        errors.add(code, f"markdown_path: {message}", path=path)
        return None

    segments = value.split("/")
    if lifecycle is not None and tuple(segments[:2]) != ("notes", lifecycle):
        errors.add(
            "invalid_path",
            f"markdown_path must start with 'notes/{lifecycle}/'.",
            path=path,
        )
        return None

    key = _collision_key(value)
    if key in seen_path_keys:
        errors.add("duplicate_path", "Duplicate markdown_path.", path=path)
        return None
    seen_path_keys.add(key)

    return value


def _parse_notes(
    raw_notes: list,
    *,
    known_folder_ids: set[str],
    known_tag_ids: set[str],
    errors: _ErrorCollector,
) -> tuple[LibraryBackupNote, ...]:
    notes: list[LibraryBackupNote] = []
    seen_ids: set[str] = set()
    seen_path_keys: set[str] = set()
    for index, raw_note in enumerate(raw_notes):
        path = f"notes[{index}]"
        if not _require_mapping(raw_note, path=path, errors=errors):
            continue
        _reject_unknown_keys(raw_note, _NOTE_KEYS, path=path, errors=errors)

        note_id = raw_note.get("id")
        if not _require_string(note_id, path=f"{path}.id", errors=errors):
            continue
        if not note_id.startswith(f"{_NOTE_ID_PREFIX}-"):
            errors.add("invalid_id", "Note id has the wrong prefix.", path=f"{path}.id")
            continue
        if note_id in seen_ids:
            errors.add("duplicate_id", "Duplicate note id.", path=f"{path}.id")
            continue
        seen_ids.add(note_id)

        title = raw_note.get("title")
        _require_string(title, path=f"{path}.title", errors=errors, allow_empty=True)

        pinned = raw_note.get("pinned")
        _require_bool(pinned, path=f"{path}.pinned", errors=errors)

        folder_id = raw_note.get("folder_id")
        if folder_id is not None:
            if not _require_string(folder_id, path=f"{path}.folder_id", errors=errors):
                folder_id = None
            elif folder_id not in known_folder_ids:
                errors.add(
                    "broken_reference",
                    "folder_id does not match any folder in this manifest.",
                    path=f"{path}.folder_id",
                )
                folder_id = None

        tag_ids_raw = raw_note.get("tag_ids")
        tag_ids: list[str] = []
        if _require_list(tag_ids_raw, path=f"{path}.tag_ids", errors=errors):
            seen_tag_refs: set[str] = set()
            for tag_index, tag_ref in enumerate(tag_ids_raw):
                tag_path = f"{path}.tag_ids[{tag_index}]"
                if not _require_string(tag_ref, path=tag_path, errors=errors):
                    continue
                if tag_ref not in known_tag_ids:
                    errors.add(
                        "broken_reference",
                        "tag_ids entry does not match any tag in this manifest.",
                        path=tag_path,
                    )
                    continue
                if tag_ref in seen_tag_refs:
                    errors.add("duplicate_reference", "Duplicate tag reference.", path=tag_path)
                    continue
                seen_tag_refs.add(tag_ref)
                tag_ids.append(tag_ref)

        trashed_at_present = "trashed_at" in raw_note
        emptied_at_present = "emptied_at" in raw_note
        trashed_at = _parse_timestamp(
            raw_note.get("trashed_at"), path=f"{path}.trashed_at", errors=errors, required=False
        )
        emptied_at = _parse_timestamp(
            raw_note.get("emptied_at"), path=f"{path}.emptied_at", errors=errors, required=False
        )
        created_at = _parse_timestamp(
            raw_note.get("created_at"), path=f"{path}.created_at", errors=errors, required=True
        )
        modified_at = _parse_timestamp(
            raw_note.get("modified_at"), path=f"{path}.modified_at", errors=errors, required=True
        )

        lifecycle = raw_note.get("lifecycle")
        _validate_lifecycle_timestamps(
            lifecycle=lifecycle,
            trashed_at=trashed_at,
            trashed_at_present=trashed_at_present,
            emptied_at=emptied_at,
            emptied_at_present=emptied_at_present,
            path=path,
            errors=errors,
        )

        body_json = raw_note.get("body_json")
        try:
            body_json = documents.validate_canonical_document(body_json)
        except DjangoValidationError:
            errors.add(
                "invalid_body", "body_json failed document validation.", path=f"{path}.body_json"
            )
            body_json = None

        markdown_path = _validate_markdown_path(
            raw_note.get("markdown_path"),
            lifecycle=lifecycle if lifecycle in LIFECYCLE_STATES else None,
            path=f"{path}.markdown_path",
            errors=errors,
            seen_path_keys=seen_path_keys,
        )

        title_is_valid = isinstance(title, str)
        if (
            created_at is None
            or modified_at is None
            or body_json is None
            or not title_is_valid
            or markdown_path is None
        ):
            continue

        notes.append(
            LibraryBackupNote(
                id=note_id,
                title=title,
                body_json=body_json,
                folder_id=folder_id,
                tag_ids=tuple(tag_ids),
                pinned=bool(pinned) if isinstance(pinned, bool) else False,
                lifecycle=lifecycle if lifecycle in LIFECYCLE_STATES else LIFECYCLE_ACTIVE,
                trashed_at=trashed_at,
                emptied_at=emptied_at,
                created_at=created_at,
                modified_at=modified_at,
                markdown_path=markdown_path,
            )
        )
    return tuple(notes)


def parse_library_backup_manifest(data: dict[str, Any]) -> LibraryBackupManifest:
    """Parses and validates an already-decoded mapping (never a JSON
    string, bytes, uploaded file, or `HttpRequest`) as a version-1
    library-backup manifest, returning a frozen, normalized
    `LibraryBackupManifest`. Raises `LibraryBackupValidationError`
    (never `LibraryBackupValidationError`-wrapped note content) on any
    structural or semantic problem, accumulating up to
    `LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT` errors rather than failing
    on the first one found."""
    errors = _ErrorCollector()

    if not _require_mapping(data, path="$", errors=errors):
        errors.raise_if_any()

    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, path="$", errors=errors)

    format_identifier = data.get("format_identifier")
    if format_identifier != LIBRARY_BACKUP_FORMAT_IDENTIFIER:
        errors.add(
            "unsupported_format",
            f"Unsupported format identifier: {format_identifier!r}.",
            path="format_identifier",
        )

    format_version = data.get("format_version")
    if format_version != LIBRARY_BACKUP_FORMAT_VERSION:
        errors.add(
            "unsupported_version",
            f"Unsupported format version: {format_version!r}.",
            path="format_version",
        )

    exported_at = _parse_timestamp(
        data.get("exported_at"), path="exported_at", errors=errors, required=True
    )

    scope_raw = data.get("scope")
    scope: tuple[str, ...] = ()
    if _require_mapping(scope_raw, path="scope", errors=errors):
        _reject_unknown_keys(scope_raw, _SCOPE_KEYS, path="scope", errors=errors)
        included = []
        for key in ("active", "trash", "recovery"):
            value = scope_raw.get(key)
            if _require_bool(value, path=f"scope.{key}", errors=errors) and value:
                included.append(key)
        scope = tuple(included)

    raw_folders = data.get("folders")
    folders: dict[str, LibraryBackupFolder] = {}
    known_folder_ids: set[str] = set()
    if _require_list(raw_folders, path="folders", errors=errors):
        folders, known_folder_ids = _parse_folders(raw_folders, errors=errors)

    raw_tags = data.get("tags")
    known_tag_ids: set[str] = set()
    tags: dict[str, LibraryBackupTag] = {}
    if _require_list(raw_tags, path="tags", errors=errors):
        tags, known_tag_ids = _parse_tags(raw_tags, errors=errors)

    raw_notes = data.get("notes")
    notes: tuple[LibraryBackupNote, ...] = ()
    if _require_list(raw_notes, path="notes", errors=errors):
        notes = _parse_notes(
            raw_notes,
            known_folder_ids=known_folder_ids,
            known_tag_ids=known_tag_ids,
            errors=errors,
        )

    errors.raise_if_any()

    return LibraryBackupManifest(
        format_identifier=format_identifier,
        format_version=format_version,
        exported_at=exported_at,
        scope=scope,
        folders=tuple(folders.values()),
        tags=tuple(tags.values()),
        notes=notes,
    )
