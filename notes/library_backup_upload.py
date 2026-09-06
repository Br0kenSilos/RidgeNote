"""Uploaded library-backup validation and restore preview.

Owns everything needed to safely accept an *uploaded* RidgeNote
library-backup ZIP and produce a non-mutating, count-only preview:
the HTTP request-size guard (`LibraryBackupRequestSizeGuard`), ZIP
container preflight, member-type/path/duplicate safety, bounded
CRC-verified reads, duplicate-JSON-key-safe manifest decoding, exact
archive-member correspondence, and the immutable `LibraryBackupPreview`
result. Reuses `notes.library_backup`'s existing manifest parser and
promoted path-safety helpers -- no manifest semantics are duplicated.

Deliberately excluded: the HTTP view/route, the Account-page
template, ORM writes, audit events, uploaded-archive retention, and
all restore logic. `validate_library_backup_upload()` performs no ORM
access, no audit, and is independent of `HttpRequest`; the only
`HttpRequest`-aware piece in this module is
`LibraryBackupRequestSizeGuard`, a `FileUploadHandler` used to protect
the multipart request itself before any file content is read.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from django.core.files.uploadhandler import FileUploadHandler, StopUpload

from notes.library_backup import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RECOVERY,
    LIFECYCLE_TRASH,
    LibraryBackupManifest,
    LibraryBackupValidationError,
    check_archive_path_structural_safety,
    collision_key,
    parse_library_backup_manifest,
)

MANIFEST_MEMBER_NAME = "manifest.json"
README_MEMBER_NAME = "README.txt"

_KiB = 1024
_MiB = 1024 * _KiB
_GiB = 1024 * _MiB

MAX_LIBRARY_BACKUP_UPLOAD_BYTES = 200 * _MiB
MAX_LIBRARY_BACKUP_REQUEST_BYTES = 202 * _MiB
MAX_UNCOMPRESSED_TOTAL_BYTES = 1 * _GiB
MAX_ARCHIVE_ENTRIES = 20_000
MAX_MANIFEST_BYTES = 50 * _MiB
MAX_README_BYTES = 1 * _MiB
MAX_MARKDOWN_FILE_BYTES = 10 * _MiB
MAX_NOTE_COUNT = 10_000
MAX_FOLDER_COUNT = 2_000

_READ_CHUNK_SIZE = 64 * _KiB
_SUPPORTED_COMPRESSION_TYPES = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_UTF8_BOM = b"\xef\xbb\xbf"

# ZIP central-directory Unix mode bits (`external_attr >> 16`), used only
# when `create_system == 3` (the archive was authored on Unix) --
# confirmed the only platform this project ships from. `S_IFMT`/`S_IFREG`
# match Python's own `stat` module constants; duplicated here as plain
# integers to avoid importing `stat` for two values.
_UNIX_CREATE_SYSTEM = 3
_S_IFMT = 0o170000
_S_IFREG = 0o100000


# ---------------------------------------------------------------------------
# HTTP request-size guard
# ---------------------------------------------------------------------------


class LibraryBackupRequestSizeGuard(FileUploadHandler):
    """A `FileUploadHandler` that rejects a multipart request whose
    declared `Content-Length` exceeds `maximum_request_bytes`, without
    ever buffering or spooling the oversized body to memory or disk.

    Must be constructed by the caller and inserted first in
    `request.upload_handlers` before any access to
    `request.POST`/`request.FILES`. `handle_raw_input()` only records
    whether the declared size is over the limit; `receive_data_chunk()`
    raises `StopUpload(connection_reset=False)` on the first chunk if
    so, which Django's own `MultiPartParser` catches internally
    (verified from Django 5.2.15 source: `StopUpload` raised here is
    *not* re-raised to the caller) and safely drains the remaining
    body in bounded reads. The view must check the retained instance's
    `request_too_large` attribute after parsing -- `request.FILES` is
    not a reliable signal, since an aborted upload never appears in it
    either way."""

    def __init__(
        self, request=None, *, maximum_request_bytes: int = MAX_LIBRARY_BACKUP_REQUEST_BYTES
    ) -> None:
        super().__init__(request)
        self.maximum_request_bytes = maximum_request_bytes
        self.request_too_large = False

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None) -> None:
        # content_length is always an int here: Django coerces a
        # missing/malformed header to 0 before this is ever called, and
        # never calls any handler at all when content_length == 0.
        self.request_too_large = content_length > self.maximum_request_bytes

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes | None:
        if self.request_too_large:
            raise StopUpload(connection_reset=False)
        # Must return raw_data unchanged: Django chains this return
        # value into the next handler's receive_data_chunk() call, and
        # `if chunk is None: break` would silently truncate every
        # legitimate, correctly-sized upload if this ever returned None
        # on the non-rejecting path.
        return raw_data

    def file_complete(self, file_size: int) -> None:
        # Must be overridden (the base class raises NotImplementedError
        # otherwise) but must return None -- this handler never claims
        # the file; Memory/TemporaryFileUploadHandler, later in the
        # list, are the ones that construct the real UploadedFile.
        return None


# ---------------------------------------------------------------------------
# Validation errors and result type
# ---------------------------------------------------------------------------


class LibraryBackupUploadValidationError(Exception):
    """Raised by `validate_library_backup_upload()` when the uploaded
    archive fails safety or structural validation. Carries only a
    safe, machine-readable `code` -- never arbitrary member paths,
    uploaded filenames, note titles, bodies, or JSON fragments.
    `source` is left open and unmodified; the caller owns all
    cleanup."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class LibraryBackupPreview:
    format_version: int
    exported_at: datetime
    folder_count: int
    active_folder_count: int
    trash_folder_count: int
    recovery_folder_count: int
    tag_count: int
    note_count: int
    active_note_count: int
    trash_note_count: int
    recovery_note_count: int
    pinned_note_count: int
    unfiled_note_count: int
    compressed_size: int
    declared_uncompressed_size: int
    actual_uncompressed_size: int
    entry_count: int


@dataclass(frozen=True)
class ValidatedLibraryBackup:
    """The full result of validating an uploaded backup, for callers
    (the restore service) that need the parsed
    manifest itself, not just the count-only preview. Produced by the
    exact same validation path `validate_library_backup_upload()`
    uses -- never a second, parallel implementation."""

    manifest: LibraryBackupManifest
    preview: LibraryBackupPreview


# ---------------------------------------------------------------------------
# Duplicate-JSON-key-safe decoding
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Used as json.loads()'s object_pairs_hook -- called once per JSON
    # object encountered, at every nesting level, so checking only the
    # pairs passed to *this* call already catches duplicates
    # recursively without any extra bookkeeping.
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise LibraryBackupUploadValidationError(
                "duplicate_json_key", "The backup manifest contains a duplicate key."
            )
        seen[key] = value
    return seen


# ---------------------------------------------------------------------------
# ZIP member safety
# ---------------------------------------------------------------------------


def _is_regular_zip_member(info: zipfile.ZipInfo) -> bool:
    if info.create_system == _UNIX_CREATE_SYSTEM:
        mode = (info.external_attr >> 16) & _S_IFMT
        if mode not in (0, _S_IFREG):
            return False
    return True


def _member_size_limit(name: str) -> tuple[int, str, str]:
    if name == MANIFEST_MEMBER_NAME:
        return MAX_MANIFEST_BYTES, "manifest_too_large", "The backup manifest is too large."
    if name == README_MEMBER_NAME:
        return MAX_README_BYTES, "readme_too_large", "The backup README is too large."
    return MAX_MARKDOWN_FILE_BYTES, "markdown_too_large", "A backup note file is too large."


def _preflight_members(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], set[str], int]:
    infolist = archive.infolist()
    if len(infolist) > MAX_ARCHIVE_ENTRIES:
        raise LibraryBackupUploadValidationError(
            "too_many_entries", "The backup contains too many archive entries."
        )

    seen_keys: set[str] = set()
    declared_total = 0
    for info in infolist:
        name = info.filename
        if info.is_dir():
            raise LibraryBackupUploadValidationError(
                "directory_member", "This file is not a valid RidgeNote library backup."
            )
        if info.flag_bits & 0x1:
            raise LibraryBackupUploadValidationError(
                "encrypted_member", "This file is not a valid RidgeNote library backup."
            )
        if info.compress_type not in _SUPPORTED_COMPRESSION_TYPES:
            raise LibraryBackupUploadValidationError(
                "unsupported_compression", "This file is not a valid RidgeNote library backup."
            )
        if not _is_regular_zip_member(info):
            raise LibraryBackupUploadValidationError(
                "special_member", "This file is not a valid RidgeNote library backup."
            )

        safety_error = check_archive_path_structural_safety(name)
        if safety_error is not None:
            raise LibraryBackupUploadValidationError(
                "unsafe_member_path", "This file is not a valid RidgeNote library backup."
            )

        key = collision_key(name)
        if key in seen_keys:
            raise LibraryBackupUploadValidationError(
                "duplicate_member", "This file is not a valid RidgeNote library backup."
            )
        seen_keys.add(key)

        declared_total += info.file_size
        member_limit, error_code, error_message = _member_size_limit(name)
        if info.file_size > member_limit:
            raise LibraryBackupUploadValidationError(error_code, error_message)

    if declared_total > MAX_UNCOMPRESSED_TOTAL_BYTES:
        raise LibraryBackupUploadValidationError(
            "declared_size_limit_exceeded",
            "The backup exceeds RidgeNote's supported archive limits.",
        )

    names = {info.filename for info in infolist}
    if MANIFEST_MEMBER_NAME not in names:
        raise LibraryBackupUploadValidationError(
            "missing_manifest", "The backup is missing manifest.json."
        )
    if README_MEMBER_NAME not in names:
        raise LibraryBackupUploadValidationError(
            "missing_readme", "The backup is missing README.txt."
        )

    return infolist, names, declared_total


def _read_member_bounded(
    archive: zipfile.ZipFile,
    name: str,
    *,
    max_member_bytes: int,
    error_code: str,
    error_message: str,
    actual_total: int,
) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    member_total = 0
    running_total = actual_total
    try:
        with archive.open(name) as member:
            while True:
                chunk = member.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                member_total += len(chunk)
                running_total += len(chunk)
                if member_total > max_member_bytes:
                    raise LibraryBackupUploadValidationError(error_code, error_message)
                if running_total > MAX_UNCOMPRESSED_TOTAL_BYTES:
                    raise LibraryBackupUploadValidationError(
                        "actual_size_limit_exceeded",
                        "The backup exceeds RidgeNote's supported archive limits.",
                    )
                chunks.append(chunk)
    except (zipfile.BadZipFile, EOFError, OSError) as exc:
        raise LibraryBackupUploadValidationError(
            "decompression_failed", "The backup archive is damaged or incomplete."
        ) from exc
    return b"".join(chunks), running_total


# ---------------------------------------------------------------------------
# Manifest decoding
# ---------------------------------------------------------------------------


def _decode_manifest(manifest_bytes: bytes) -> dict[str, Any]:
    if manifest_bytes.startswith(_UTF8_BOM):
        raise LibraryBackupUploadValidationError("manifest_bom", "The backup manifest is invalid.")
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LibraryBackupUploadValidationError(
            "invalid_utf8", "The backup manifest is invalid."
        ) from exc
    try:
        return json.loads(manifest_text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise LibraryBackupUploadValidationError(
            "invalid_json", "The backup manifest is invalid."
        ) from exc


def _parse_manifest(decoded_manifest: Any) -> LibraryBackupManifest:
    try:
        return parse_library_backup_manifest(decoded_manifest)
    except LibraryBackupValidationError as exc:
        raise LibraryBackupUploadValidationError(
            "manifest_validation_failed", "The backup manifest is invalid."
        ) from exc


# ---------------------------------------------------------------------------
# Exact member correspondence
# ---------------------------------------------------------------------------


def _check_member_correspondence(manifest: LibraryBackupManifest, names: set[str]) -> None:
    markdown_paths = {note.markdown_path for note in manifest.notes}
    required_members = markdown_paths | {MANIFEST_MEMBER_NAME, README_MEMBER_NAME}

    missing = markdown_paths - names
    if missing:
        raise LibraryBackupUploadValidationError(
            "missing_markdown_member", "The backup archive is damaged or incomplete."
        )

    extra = names - required_members
    if extra:
        for name in extra:
            if name.startswith("notes/") and name.endswith(".md"):
                raise LibraryBackupUploadValidationError(
                    "orphan_markdown_member", "The backup archive is damaged or incomplete."
                )
        raise LibraryBackupUploadValidationError(
            "extra_member", "This file is not a valid RidgeNote library backup."
        )


# ---------------------------------------------------------------------------
# Preview counts
# ---------------------------------------------------------------------------


def _build_preview(
    manifest: LibraryBackupManifest,
    *,
    compressed_size: int,
    declared_uncompressed_size: int,
    actual_uncompressed_size: int,
    entry_count: int,
) -> LibraryBackupPreview:
    folder_lifecycle_counts = {LIFECYCLE_ACTIVE: 0, LIFECYCLE_TRASH: 0, LIFECYCLE_RECOVERY: 0}
    for folder in manifest.folders:
        folder_lifecycle_counts[folder.lifecycle] = (
            folder_lifecycle_counts.get(folder.lifecycle, 0) + 1
        )

    note_lifecycle_counts = {LIFECYCLE_ACTIVE: 0, LIFECYCLE_TRASH: 0, LIFECYCLE_RECOVERY: 0}
    pinned_note_count = 0
    unfiled_note_count = 0
    for note in manifest.notes:
        note_lifecycle_counts[note.lifecycle] = note_lifecycle_counts.get(note.lifecycle, 0) + 1
        if note.pinned:
            pinned_note_count += 1
        if note.folder_id is None:
            unfiled_note_count += 1

    return LibraryBackupPreview(
        format_version=manifest.format_version,
        exported_at=manifest.exported_at,
        folder_count=len(manifest.folders),
        active_folder_count=folder_lifecycle_counts[LIFECYCLE_ACTIVE],
        trash_folder_count=folder_lifecycle_counts[LIFECYCLE_TRASH],
        recovery_folder_count=folder_lifecycle_counts[LIFECYCLE_RECOVERY],
        tag_count=len(manifest.tags),
        note_count=len(manifest.notes),
        active_note_count=note_lifecycle_counts[LIFECYCLE_ACTIVE],
        trash_note_count=note_lifecycle_counts[LIFECYCLE_TRASH],
        recovery_note_count=note_lifecycle_counts[LIFECYCLE_RECOVERY],
        pinned_note_count=pinned_note_count,
        unfiled_note_count=unfiled_note_count,
        compressed_size=compressed_size,
        declared_uncompressed_size=declared_uncompressed_size,
        actual_uncompressed_size=actual_uncompressed_size,
        entry_count=entry_count,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_library_backup_upload(
    source: BinaryIO, *, compressed_size: int
) -> LibraryBackupPreview:
    """Validates `source` (a caller-owned, already-open, seekable
    binary file-like object -- never created, closed, or retained by
    this function) as a complete, safe, restorable-shaped RidgeNote
    library backup, and returns an immutable, count-only
    `LibraryBackupPreview`. Raises `LibraryBackupUploadValidationError`
    on any safety or structural problem; `source` is left open and
    unread-from-further in every case. Performs no ORM access, no
    audit, no filesystem extraction, and has no `HttpRequest`
    dependency.

    A thin wrapper over `load_library_backup_for_restore()` -- both
    perform exactly the same validation; this one simply discards the
    parsed manifest and returns only the count-only preview."""
    return load_library_backup_for_restore(source, compressed_size=compressed_size).preview


def load_library_backup_for_restore(
    source: BinaryIO, *, compressed_size: int
) -> ValidatedLibraryBackup:
    """Validates `source` exactly as `validate_library_backup_upload()`
    does (same caller-owns-source, never-closed, never-retained
    contract) and additionally returns the fully parsed
    `LibraryBackupManifest` alongside the preview, for callers
    (the restore service) that need the manifest's
    actual content, not just its counts. Raises
    `LibraryBackupUploadValidationError` on any safety or structural
    problem -- the identical exception type and codes
    `validate_library_backup_upload()` raises, since this function is
    the single, shared validation path both public entry points call."""
    if compressed_size <= 0:
        raise LibraryBackupUploadValidationError("empty_upload", "The upload is empty.")
    if compressed_size > MAX_LIBRARY_BACKUP_UPLOAD_BYTES:
        raise LibraryBackupUploadValidationError(
            "upload_too_large",
            "This upload is too large. RidgeNote accepts library backup ZIP files up to 200 MiB.",
        )

    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise LibraryBackupUploadValidationError(
            "invalid_zip", "This file is not a valid RidgeNote library backup."
        ) from exc

    with archive:
        infolist, names, declared_total = _preflight_members(archive)

        manifest_bytes, actual_total = _read_member_bounded(
            archive,
            MANIFEST_MEMBER_NAME,
            max_member_bytes=MAX_MANIFEST_BYTES,
            error_code="manifest_too_large",
            error_message="The backup manifest is too large.",
            actual_total=0,
        )
        decoded_manifest = _decode_manifest(manifest_bytes)
        manifest = _parse_manifest(decoded_manifest)

        if len(manifest.notes) > MAX_NOTE_COUNT:
            raise LibraryBackupUploadValidationError(
                "manifest_validation_failed", "The backup manifest is invalid."
            )
        if len(manifest.folders) > MAX_FOLDER_COUNT:
            raise LibraryBackupUploadValidationError(
                "manifest_validation_failed", "The backup manifest is invalid."
            )

        _check_member_correspondence(manifest, names)

        _, actual_total = _read_member_bounded(
            archive,
            README_MEMBER_NAME,
            max_member_bytes=MAX_README_BYTES,
            error_code="readme_too_large",
            error_message="The backup README is too large.",
            actual_total=actual_total,
        )

        for note in manifest.notes:
            _, actual_total = _read_member_bounded(
                archive,
                note.markdown_path,
                max_member_bytes=MAX_MARKDOWN_FILE_BYTES,
                error_code="markdown_too_large",
                error_message="A backup note file is too large.",
                actual_total=actual_total,
            )

    preview = _build_preview(
        manifest,
        compressed_size=compressed_size,
        declared_uncompressed_size=declared_total,
        actual_uncompressed_size=actual_total,
        entry_count=len(infolist),
    )
    return ValidatedLibraryBackup(manifest=manifest, preview=preview)
