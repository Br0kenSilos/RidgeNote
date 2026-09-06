"""Uploaded library-backup validation.

Covers `notes.library_backup_upload`'s `LibraryBackupRequestSizeGuard`
(unit-level, no Django request/database needed) and
`validate_library_backup_upload()` (mostly no database needed either --
most fixtures are hand-built minimal manifests, not real generated
archive output, except where a real generated archive is explicitly useful).
"""

import io
import struct
import zipfile

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import library_backup_archive, library_backup_upload, services

PASSWORD = "LongUniquePassword123!"

# ---------------------------------------------------------------------------
# Helpers -- hand-built minimal valid manifest, no database required
# ---------------------------------------------------------------------------

_VALID_MANIFEST = {
    "format_identifier": "ridgenote-library-backup",
    "format_version": 1,
    "exported_at": "2026-08-06T12:00:00+00:00",
    "scope": {"active": True, "trash": True, "recovery": True},
    "folders": [],
    "tags": [],
    "notes": [
        {
            "id": "note-000001",
            "title": "Sample",
            "body_json": {"type": "doc", "content": [{"type": "paragraph"}]},
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
_VALID_README = b"RidgeNote Library Backup\n"
_VALID_MARKDOWN = b"# Sample\n\nHello\n"


def _manifest_bytes(manifest=None):
    import json

    return json.dumps(manifest or _VALID_MANIFEST).encode("utf-8")


def _valid_members():
    return {
        "manifest.json": _manifest_bytes(),
        "README.txt": _VALID_README,
        "notes/active/Unfiled/Sample.md": _VALID_MARKDOWN,
    }


def _build_zip(members: dict[str, bytes], *, overrides: dict[str, dict] | None = None) -> bytes:
    overrides = overrides or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in members.items():
            member_overrides = overrides.get(name, {})
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = member_overrides.get("compress_type", zipfile.ZIP_DEFLATED)
            info.external_attr = member_overrides.get("external_attr", 0o644 << 16)
            if "flag_bits" in member_overrides:
                info.flag_bits = member_overrides["flag_bits"]
            archive.writestr(info, data)
    return buf.getvalue()


def _set_encrypted_flag(raw: bytes, member_name: str) -> bytes:
    """Set the encryption bit (0x1) on a member's local and central
    directory headers directly in the raw ZIP bytes.

    ``zipfile.ZipInfo.flag_bits`` set before ``writestr()`` is silently
    recomputed and discarded by ``zipfile``'s writer, so producing a
    genuinely "encrypted" member for tests requires patching the
    already-written archive bytes instead.
    """
    data = bytearray(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        local_offset = archive.getinfo(member_name).header_offset
    struct.pack_into(
        "<H", data, local_offset + 6, struct.unpack_from("<H", data, local_offset + 6)[0] | 0x1
    )
    name_bytes = member_name.encode("utf-8")
    sig = b"PK\x01\x02"
    idx = 0
    while True:
        idx = data.find(sig, idx)
        if idx == -1:
            raise AssertionError(f"central directory record for {member_name!r} not found")
        name_len = struct.unpack_from("<H", data, idx + 28)[0]
        extra_len = struct.unpack_from("<H", data, idx + 30)[0]
        comment_len = struct.unpack_from("<H", data, idx + 32)[0]
        if bytes(data[idx + 46 : idx + 46 + name_len]) == name_bytes:
            flag_offset = idx + 8
            struct.pack_into(
                "<H", data, flag_offset, struct.unpack_from("<H", data, flag_offset)[0] | 0x1
            )
            break
        idx += 46 + name_len + extra_len + comment_len
    return bytes(data)


def _validate(raw: bytes):
    source = io.BytesIO(raw)
    return library_backup_upload.validate_library_backup_upload(source, compressed_size=len(raw))


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# LibraryBackupRequestSizeGuard -- unit tests, no Django request needed
# ---------------------------------------------------------------------------


def test_guard_default_limit_is_202_mib():
    assert library_backup_upload.MAX_LIBRARY_BACKUP_REQUEST_BYTES == 202 * 1024 * 1024


def test_guard_flags_request_over_injected_limit():
    guard = library_backup_upload.LibraryBackupRequestSizeGuard(None, maximum_request_bytes=100)
    guard.handle_raw_input(None, {}, 101, b"--boundary")
    assert guard.request_too_large is True


def test_guard_does_not_flag_request_at_or_under_injected_limit():
    guard = library_backup_upload.LibraryBackupRequestSizeGuard(None, maximum_request_bytes=100)
    guard.handle_raw_input(None, {}, 100, b"--boundary")
    assert guard.request_too_large is False


def test_guard_passes_normal_chunks_through_unchanged():
    guard = library_backup_upload.LibraryBackupRequestSizeGuard(None, maximum_request_bytes=100)
    guard.handle_raw_input(None, {}, 50, b"--boundary")
    assert guard.receive_data_chunk(b"hello", 0) == b"hello"


def test_guard_raises_stop_upload_only_when_flagged():
    from django.core.files.uploadhandler import StopUpload

    guard = library_backup_upload.LibraryBackupRequestSizeGuard(None, maximum_request_bytes=100)
    guard.handle_raw_input(None, {}, 200, b"--boundary")
    with pytest.raises(StopUpload) as excinfo:
        guard.receive_data_chunk(b"x", 0)
    assert excinfo.value.connection_reset is False


def test_guard_file_complete_returns_none():
    guard = library_backup_upload.LibraryBackupRequestSizeGuard(None)
    assert guard.file_complete(123) is None


# ---------------------------------------------------------------------------
# validate_library_backup_upload -- acceptance
# ---------------------------------------------------------------------------


def test_valid_minimal_archive_produces_correct_preview():
    raw = _build_zip(_valid_members())
    preview = _validate(raw)
    assert preview.format_version == 1
    assert preview.note_count == 1
    assert preview.active_note_count == 1
    assert preview.trash_note_count == 0
    assert preview.recovery_note_count == 0
    assert preview.folder_count == 0
    assert preview.tag_count == 0
    assert preview.pinned_note_count == 0
    assert preview.unfiled_note_count == 1
    assert preview.entry_count == 3
    assert preview.compressed_size == len(raw)


def test_source_remains_open_and_unread_after_success():
    raw = _build_zip(_valid_members())
    source = io.BytesIO(raw)
    library_backup_upload.validate_library_backup_upload(source, compressed_size=len(raw))
    assert source.closed is False
    source.seek(0)
    assert source.read() == raw


# ---------------------------------------------------------------------------
# load_library_backup_for_restore
# ---------------------------------------------------------------------------


def test_richer_loader_returns_parsed_manifest_and_preview():
    raw = _build_zip(_valid_members())
    validated = library_backup_upload.load_library_backup_for_restore(
        io.BytesIO(raw), compressed_size=len(raw)
    )
    assert isinstance(validated, library_backup_upload.ValidatedLibraryBackup)
    assert validated.manifest.notes[0].title == "Sample"
    assert validated.preview.note_count == 1


def test_wrapper_preview_identical_to_richer_loader_preview():
    raw = _build_zip(_valid_members())
    preview_from_wrapper = library_backup_upload.validate_library_backup_upload(
        io.BytesIO(raw), compressed_size=len(raw)
    )
    validated = library_backup_upload.load_library_backup_for_restore(
        io.BytesIO(raw), compressed_size=len(raw)
    )
    assert preview_from_wrapper == validated.preview


def test_richer_loader_source_remains_open_and_unread():
    raw = _build_zip(_valid_members())
    source = io.BytesIO(raw)
    library_backup_upload.load_library_backup_for_restore(source, compressed_size=len(raw))
    assert source.closed is False
    source.seek(0)
    assert source.read() == raw


def test_richer_loader_invalid_zip_raises_same_exception_type():
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        library_backup_upload.load_library_backup_for_restore(
            io.BytesIO(b"not a zip"), compressed_size=9
        )
    assert excinfo.value.code == "invalid_zip"


def test_richer_loader_unsafe_member_rejected_same_as_wrapper():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        archive.writestr("/notes/active/Unfiled/Absolute.md", "target")
    raw = buf.getvalue()
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        library_backup_upload.load_library_backup_for_restore(
            io.BytesIO(raw), compressed_size=len(raw)
        )
    assert excinfo.value.code == "unsafe_member_path"


def test_repeated_validation_is_deterministic():
    raw = _build_zip(_valid_members())
    first = _validate(raw)
    second = _validate(raw)
    assert first == second


@pytest.mark.django_db(transaction=True)
def test_generated_empty_archive_is_accepted():
    owner = create_account("real-empty-archive-owner")
    dest = io.BytesIO()
    library_backup_archive.write_library_backup_archive(owner, dest)
    raw = dest.getvalue()
    preview = _validate(raw)
    assert preview.note_count == 0
    assert preview.entry_count == 2


@pytest.mark.django_db(transaction=True)
def test_generated_populated_archive_is_accepted():
    owner = create_account("real-populated-archive-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Real Note")
    services.set_note_pinned(note=note, pinned=True)
    dest = io.BytesIO()
    library_backup_archive.write_library_backup_archive(owner, dest)
    raw = dest.getvalue()
    preview = _validate(raw)
    assert preview.note_count == 1
    assert preview.pinned_note_count == 1
    assert preview.folder_count == 1
    assert preview.entry_count == 3


# ---------------------------------------------------------------------------
# Compressed-size boundary
# ---------------------------------------------------------------------------


def test_empty_upload_rejected():
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        library_backup_upload.validate_library_backup_upload(io.BytesIO(b""), compressed_size=0)
    assert excinfo.value.code == "empty_upload"


def test_upload_over_200_mib_rejected():
    raw = _build_zip(_valid_members())
    source = io.BytesIO(raw)
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        library_backup_upload.validate_library_backup_upload(
            source, compressed_size=library_backup_upload.MAX_LIBRARY_BACKUP_UPLOAD_BYTES + 1
        )
    assert excinfo.value.code == "upload_too_large"
    assert source.closed is False


# ---------------------------------------------------------------------------
# ZIP container hazards
# ---------------------------------------------------------------------------


def test_invalid_zip_rejected():
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(b"not a zip file at all")
    assert excinfo.value.code == "invalid_zip"


def test_too_many_entries_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_ARCHIVE_ENTRIES", 2)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "too_many_entries"


def test_exact_duplicate_member_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "duplicate_member"


def test_casefold_duplicate_member_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("Manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "duplicate_member"


def test_non_nfc_member_path_rejected():
    # A decomposed Unicode form ("e" + combining acute accent, U+0301)
    # -- the structural path-safety check requires strict NFC
    # compliance, so a non-canonical member name is rejected outright
    # as unsafe. Built via chr() rather than a literal accented
    # character to keep the exact codepoints unambiguous.
    decomposed_name = "notes/active/Unfiled/Caf" + "e" + chr(0x0301) + ".md"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        archive.writestr(decomposed_name, b"a")
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "unsafe_member_path"


def test_encrypted_member_rejected():
    raw = _set_encrypted_flag(_build_zip(_valid_members()), "notes/active/Unfiled/Sample.md")
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "encrypted_member"


def test_unsupported_compression_rejected():
    raw = _build_zip(
        _valid_members(),
        overrides={"notes/active/Unfiled/Sample.md": {"compress_type": zipfile.ZIP_BZIP2}},
    )
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "unsupported_compression"


def test_directory_member_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        archive.writestr("notes/active/Unfiled/", b"")
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "directory_member"


def test_symlink_member_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        info = zipfile.ZipInfo("notes/active/Unfiled/link.md")
        info.create_system = 3
        info.external_attr = (0o120777 << 16) | 0x0400  # S_IFLNK
        archive.writestr(info, "target")
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "special_member"


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "/notes/active/Unfiled/Absolute.md",
        "notes\\active\\Unfiled\\Backslash.md",
        "../notes/active/Unfiled/Traversal.md",
        "notes/active/Unfiled/../Escape.md",
    ],
)
def test_unsafe_member_path_rejected(bad_name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        archive.writestr(bad_name, b"x")
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(buf.getvalue())
    assert excinfo.value.code == "unsafe_member_path"


# ---------------------------------------------------------------------------
# Declared/actual size limits
# ---------------------------------------------------------------------------


def test_declared_manifest_too_large_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_MANIFEST_BYTES", 1)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "manifest_too_large"


def test_declared_readme_too_large_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_README_BYTES", 1)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "readme_too_large"


def test_declared_markdown_too_large_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_MARKDOWN_FILE_BYTES", 1)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "markdown_too_large"


def test_declared_total_uncompressed_exceeded_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_UNCOMPRESSED_TOTAL_BYTES", 1)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "declared_size_limit_exceeded"


def test_actual_cumulative_size_exceeded_via_direct_unit_call(monkeypatch):
    # Directly exercises the *actual* (not declared) cumulative-overflow
    # branch inside _read_member_bounded(): a legitimately small member,
    # read with a large per-member ceiling, but an `actual_total` starting
    # point already near the (monkeypatched, small) cumulative cap --
    # simulating "earlier members already consumed most of the budget."
    monkeypatch.setattr(library_backup_upload, "MAX_UNCOMPRESSED_TOTAL_BYTES", 10)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("small.txt", b"0123456789")
    buf.seek(0)
    with zipfile.ZipFile(buf) as archive:
        with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
            library_backup_upload._read_member_bounded(
                archive,
                "small.txt",
                max_member_bytes=1000,
                error_code="markdown_too_large",
                error_message="too large",
                actual_total=5,
            )
    assert excinfo.value.code == "actual_size_limit_exceeded"


# ---------------------------------------------------------------------------
# Member-set correspondence
# ---------------------------------------------------------------------------


def test_missing_manifest_rejected():
    members = _valid_members()
    del members["manifest.json"]
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "missing_manifest"


def test_missing_readme_rejected():
    members = _valid_members()
    del members["README.txt"]
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "missing_readme"


def test_missing_markdown_member_rejected():
    members = _valid_members()
    del members["notes/active/Unfiled/Sample.md"]
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "missing_markdown_member"


def test_orphan_markdown_member_rejected():
    members = _valid_members()
    members["notes/active/Unfiled/Orphan.md"] = b"# Orphan\n"
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "orphan_markdown_member"


def test_extra_non_markdown_member_rejected():
    members = _valid_members()
    members["extra.txt"] = b"junk"
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "extra_member"


def test_hidden_extra_member_rejected_as_unsafe_path():
    members = _valid_members()
    members["__MACOSX/._Sample"] = b"junk"
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "unsafe_member_path"


# ---------------------------------------------------------------------------
# Manifest decoding
# ---------------------------------------------------------------------------


def test_invalid_utf8_manifest_rejected():
    members = _valid_members()
    members["manifest.json"] = b"\xff\xfe not valid utf-8"
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "invalid_utf8"


def test_manifest_bom_rejected():
    members = _valid_members()
    members["manifest.json"] = b"\xef\xbb\xbf" + _manifest_bytes()
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "manifest_bom"


def test_invalid_json_manifest_rejected():
    members = _valid_members()
    members["manifest.json"] = b"{not valid json"
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "invalid_json"


def test_duplicate_top_level_json_key_rejected():
    members = _valid_members()
    members["manifest.json"] = b'{"format_version": 1, "format_version": 2}'
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "duplicate_json_key"


def test_duplicate_nested_json_key_rejected():
    members = _valid_members()
    members["manifest.json"] = b'{"scope": {"active": true, "active": false}}'
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "duplicate_json_key"


def test_manifest_schema_validation_failure_wrapped():
    members = _valid_members()
    members["manifest.json"] = b'{"not": "a valid manifest shape"}'
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert excinfo.value.code == "manifest_validation_failed"


# ---------------------------------------------------------------------------
# Corruption
# ---------------------------------------------------------------------------


def test_crc_mismatch_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", _manifest_bytes())
        archive.writestr("README.txt", _VALID_README)
        archive.writestr("notes/active/Unfiled/Sample.md", _VALID_MARKDOWN)
    raw = bytearray(buf.getvalue())
    # Flip a byte inside the last member's compressed data region without
    # touching the central directory, to produce a genuine CRC mismatch
    # rather than a corrupt archive structure.
    marker = b"Hello"
    idx = raw.find(marker)
    assert idx != -1
    raw[idx] ^= 0xFF
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(bytes(raw))
    assert excinfo.value.code in ("decompression_failed", "invalid_zip")


# ---------------------------------------------------------------------------
# Note/folder count limits
# ---------------------------------------------------------------------------


def test_note_count_over_limit_rejected(monkeypatch):
    monkeypatch.setattr(library_backup_upload, "MAX_NOTE_COUNT", 0)
    raw = _build_zip(_valid_members())
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(raw)
    assert excinfo.value.code == "manifest_validation_failed"


# ---------------------------------------------------------------------------
# Safe error content
# ---------------------------------------------------------------------------


def test_error_never_contains_note_content():
    members = _valid_members()
    manifest = dict(_VALID_MANIFEST)
    manifest["notes"] = [
        {**_VALID_MANIFEST["notes"][0], "title": "Super Secret Title"},
    ]
    members["manifest.json"] = _manifest_bytes(manifest)
    del members["notes/active/Unfiled/Sample.md"]  # force a correspondence failure
    with pytest.raises(library_backup_upload.LibraryBackupUploadValidationError) as excinfo:
        _validate(_build_zip(members))
    assert "Super Secret Title" not in str(excinfo.value)
    assert "Super Secret Title" not in excinfo.value.code
