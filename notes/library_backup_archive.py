"""Downloadable library-backup archive.

Owns ZIP archive assembly for a user's library backup: README
generation, manifest JSON serialization, ZIP entry creation and
member ordering, generation size/count limits, and the frozen
`LibraryBackupArchiveMetadata` result type. Writes directly into a
caller-owned, already-open, seekable binary destination -- this module
never creates a destination, never chooses tempfile policy, and never
closes the destination itself; the caller owns its entire lifecycle.

Deliberately excluded: the HTTP view/route, the account-menu
template, the audit event, and all upload/restore/preview logic. This
module performs no audit and is independent of `HttpRequest`.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO
from zoneinfo import ZoneInfo

from accounts.services import resolve_display_timezone
from django.utils import timezone

from notes.library_backup import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RECOVERY,
    LIFECYCLE_TRASH,
    LibraryBackupManifest,
    build_library_backup_manifest,
    parse_library_backup_manifest,
)
from notes.markdown import note_to_markdown

ARCHIVE_FILENAME_PREFIX = "ridgenote-library-backup"
MANIFEST_MEMBER_NAME = "manifest.json"
README_MEMBER_NAME = "README.txt"

_KiB = 1024
_MiB = 1024 * _KiB
_GiB = 1024 * _MiB

MAX_COMPRESSED_ARCHIVE_BYTES = 200 * _MiB
MAX_UNCOMPRESSED_TOTAL_BYTES = 1 * _GiB
MAX_ARCHIVE_ENTRIES = 20_000
MAX_MARKDOWN_FILE_BYTES = 10 * _MiB
MAX_MANIFEST_BYTES = 50 * _MiB
MAX_NOTE_COUNT = 10_000
MAX_FOLDER_COUNT = 2_000

_FIXED_ENTRY_COUNT = 2  # manifest.json + README.txt
_ZIP_MIN_YEAR = 1980  # zipfile.ZipInfo's earliest representable DOS-era year
_ORDINARY_FILE_PERMISSIONS = 0o644 << 16  # regular file, no symlink/special mode


class LibraryBackupGenerationError(Exception):
    """Raised by `write_library_backup_archive()` when an authorized
    generation limit is exceeded. Carries only a safe, machine-readable
    `code` -- never note content, titles, folder names, or paths.
    `destination` is left open, exactly as on success; the caller owns
    all cleanup."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class LibraryBackupArchiveMetadata:
    filename: str
    exported_at: datetime
    compressed_size: int
    uncompressed_size: int
    entry_count: int
    note_count: int
    folder_count: int
    tag_count: int


def _resolve_exported_at(exported_at: datetime | None) -> datetime:
    if exported_at is None:
        return timezone.now()
    if exported_at.tzinfo is None or exported_at.tzinfo.utcoffset(exported_at) is None:
        raise ValueError("exported_at must be timezone-aware.")
    return exported_at.astimezone(UTC)


def _archive_filename(exported_at: datetime, *, display_timezone: ZoneInfo) -> str:
    """`exported_at` (already resolved to UTC by
    `_resolve_exported_at()`) is converted to the owner's effective
    display timezone before formatting -- only this human-facing
    filename changes; the manifest's `exported_at` and the README's
    `Generated ... UTC` line remain UTC, untouched."""
    localized = exported_at.astimezone(display_timezone)
    return f"{ARCHIVE_FILENAME_PREFIX}-{localized:%Y-%m-%d_%H%M%S}.zip"


def _zip_date_time(exported_at: datetime) -> tuple[int, int, int, int, int, int]:
    year = max(exported_at.year, _ZIP_MIN_YEAR)
    return (
        year,
        exported_at.month,
        exported_at.day,
        exported_at.hour,
        exported_at.minute,
        exported_at.second,
    )


def _encode_manifest(serialized_manifest: dict) -> bytes:
    text = json.dumps(serialized_manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return text.encode("utf-8")


def _lifecycle_counts(manifest: LibraryBackupManifest) -> dict[str, int]:
    counts = {LIFECYCLE_ACTIVE: 0, LIFECYCLE_TRASH: 0, LIFECYCLE_RECOVERY: 0}
    for note in manifest.notes:
        counts[note.lifecycle] = counts.get(note.lifecycle, 0) + 1
    return counts


def _build_readme(
    manifest: LibraryBackupManifest,
    exported_at: datetime,
    counts: dict[str, int],
    *,
    display_timezone: ZoneInfo,
) -> str:
    filename = _archive_filename(exported_at, display_timezone=display_timezone)
    generated = exported_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "RidgeNote Library Backup",
        "=========================",
        "",
        f"Generated: {generated}",
        f"Archive: {filename}",
        "",
        "Counts",
        "------",
        f"Folders: {len(manifest.folders)}",
        f"Tags: {len(manifest.tags)}",
        f"Notes: {len(manifest.notes)} total "
        f"({counts[LIFECYCLE_ACTIVE]} active, {counts[LIFECYCLE_TRASH]} trash, "
        f"{counts[LIFECYCLE_RECOVERY]} recovery)",
        "",
        "Contents",
        "--------",
        "- manifest.json -- the authoritative record of this backup. A future",
        "  RidgeNote restore feature will read this file, not the Markdown files",
        "  below. folder_id and other manifest references define true identity;",
        "  Markdown file paths do not.",
        "- One Markdown (.md) file per note, organized under notes/active/,",
        "  notes/trash/, and notes/recovery/, for human reading only.",
        "",
        "Important notes",
        "----------------",
        "- manifest.json is authoritative. The native body_json it contains is",
        "  the source a future restore will use. Editing a Markdown file does",
        "  not change manifest.json, and a future restore must never substitute",
        "  edited Markdown content for what manifest.json contains.",
        "- Markdown filenames and folder names may differ slightly from the",
        "  original note/folder names (unsafe characters removed, duplicate",
        "  names numbered) for filesystem safety. Original names and",
        "  relationships are preserved exactly in manifest.json.",
        '- "Trash" and "Recovery" reflect RidgeNote\'s own retention lifecycle at',
        "  the time of this backup; recovery-lifecycle content may normally be",
        "  visible only to an administrator inside RidgeNote itself.",
        "",
        "Backup scope",
        "------------",
        "This archive contains only this account's own folders, notes, tags,",
        "pin state, and lifecycle state (including lifecycle timestamps and",
        "recovery metadata) as represented in manifest.json.",
        "",
        "This archive does not contain:",
        "- credentials or password material",
        "- session data",
        "- audit history",
        "- application or server configuration",
        "- other users' data",
        "",
        "Because note content may be sensitive, store this archive securely.",
        "",
        "This is an application-level RidgeNote library backup. It is not a",
        "substitute for a PostgreSQL, deployment, or other operator-level backup.",
    ]
    return "\n".join(lines) + "\n"


def _write_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    date_time: tuple[int, int, int, int, int, int],
) -> None:
    info = zipfile.ZipInfo(name, date_time=date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = _ORDINARY_FILE_PERMISSIONS
    archive.writestr(info, data)


def write_library_backup_archive(
    owner,
    destination: BinaryIO,
    *,
    exported_at: datetime | None = None,
    display_timezone: ZoneInfo | None = None,
) -> LibraryBackupArchiveMetadata:
    """Writes a complete version-1 library-backup ZIP archive
    (`manifest.json`, `README.txt`, and one Markdown file per note)
    directly into `destination`, a caller-owned, already-open,
    seekable binary file-like object. Never creates, chooses tempfile
    policy for, or closes `destination` -- the caller owns its entire
    lifecycle in every case. Raises `LibraryBackupGenerationError` if
    an authorized generation limit is exceeded; `destination` is left
    open (possibly containing partial or finalized-but-oversized
    content) exactly as on success -- cleanup remains the caller's
    responsibility either way. Performs no audit and is independent of
    `HttpRequest`.

    `display_timezone` localizes only the
    human-facing archive filename (and the matching `Archive:` line in
    README.txt) -- the manifest's `exported_at` and README's
    `Generated ... UTC` line stay UTC, unaffected. Defaults to
    `owner`'s own effective display timezone via
    `resolve_display_timezone()` when not explicitly supplied, so
    ordinary callers need only pass `owner`; an explicit value remains
    available for tests."""
    resolved_exported_at = _resolve_exported_at(exported_at)
    resolved_display_timezone = display_timezone or resolve_display_timezone(owner)

    serialized_manifest = build_library_backup_manifest(owner, exported_at=resolved_exported_at)
    manifest = parse_library_backup_manifest(serialized_manifest)

    if len(manifest.notes) > MAX_NOTE_COUNT:
        raise LibraryBackupGenerationError("note_limit_exceeded", "Too many notes.")
    if len(manifest.folders) > MAX_FOLDER_COUNT:
        raise LibraryBackupGenerationError("folder_limit_exceeded", "Too many folders.")

    projected_entry_count = _FIXED_ENTRY_COUNT + len(manifest.notes)
    if projected_entry_count > MAX_ARCHIVE_ENTRIES:
        raise LibraryBackupGenerationError("entry_limit_exceeded", "Too many archive entries.")

    manifest_bytes = _encode_manifest(serialized_manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise LibraryBackupGenerationError("manifest_size_exceeded", "Manifest too large.")

    counts = _lifecycle_counts(manifest)
    readme_bytes = _build_readme(
        manifest, resolved_exported_at, counts, display_timezone=resolved_display_timezone
    ).encode("utf-8")

    uncompressed_total = len(manifest_bytes) + len(readme_bytes)
    if uncompressed_total > MAX_UNCOMPRESSED_TOTAL_BYTES:
        raise LibraryBackupGenerationError(
            "uncompressed_size_exceeded", "Backup content too large."
        )

    zip_date_time = _zip_date_time(resolved_exported_at)

    with zipfile.ZipFile(destination, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_member(archive, MANIFEST_MEMBER_NAME, manifest_bytes, zip_date_time)
        _write_member(archive, README_MEMBER_NAME, readme_bytes, zip_date_time)

        for note in manifest.notes:
            markdown_bytes = note_to_markdown(note.title, note.body_json).encode("utf-8")
            if len(markdown_bytes) > MAX_MARKDOWN_FILE_BYTES:
                raise LibraryBackupGenerationError(
                    "markdown_size_exceeded", "A note is too large to include in the backup."
                )
            uncompressed_total += len(markdown_bytes)
            if uncompressed_total > MAX_UNCOMPRESSED_TOTAL_BYTES:
                raise LibraryBackupGenerationError(
                    "uncompressed_size_exceeded", "Backup content too large."
                )
            _write_member(archive, note.markdown_path, markdown_bytes, zip_date_time)

    compressed_size = destination.tell()
    if compressed_size > MAX_COMPRESSED_ARCHIVE_BYTES:
        raise LibraryBackupGenerationError("compressed_size_exceeded", "Backup archive too large.")

    return LibraryBackupArchiveMetadata(
        filename=_archive_filename(
            resolved_exported_at, display_timezone=resolved_display_timezone
        ),
        exported_at=resolved_exported_at,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_total,
        entry_count=projected_entry_count,
        note_count=len(manifest.notes),
        folder_count=len(manifest.folders),
        tag_count=len(manifest.tags),
    )
