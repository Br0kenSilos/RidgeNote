"""Downloadable library-backup archive.

Covers `notes.library_backup_archive`'s destination-writing
`write_library_backup_archive()` directly, against a plain `io.BytesIO()`
destination -- no Django test client, no view, no tempfile machinery
needed at this layer.
"""

import io
import json
import zipfile
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import library_backup_archive, services
from notes.library_backup import parse_library_backup_manifest
from notes.markdown import note_to_markdown

PASSWORD = "LongUniquePassword123!"
FIXED_TIME = datetime(2026, 8, 6, 18, 0, 0, tzinfo=UTC)


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def _trash(obj, *, when=None):
    obj.trashed_at = when or timezone.now()
    obj.save(update_fields=["trashed_at"])
    return obj


def _empty(obj, *, when=None):
    obj.emptied_at = when or timezone.now()
    obj.save(update_fields=["emptied_at"])
    return obj


def _write(owner, destination=None, **kwargs):
    destination = destination if destination is not None else io.BytesIO()
    metadata = library_backup_archive.write_library_backup_archive(
        owner, destination, exported_at=kwargs.pop("exported_at", FIXED_TIME), **kwargs
    )
    return destination, metadata


# ---------------------------------------------------------------------------
# Basic archive shape
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_empty_library_produces_exactly_two_members():
    owner = create_account("empty-archive-owner")

    destination, metadata = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["manifest.json", "README.txt"]
    assert metadata.entry_count == 2
    assert metadata.note_count == 0
    assert metadata.folder_count == 0
    assert metadata.tag_count == 0


@pytest.mark.django_db(transaction=True)
def test_service_never_closes_the_destination():
    owner = create_account("open-destination-owner")
    destination = io.BytesIO()

    _write(owner, destination)

    assert destination.closed is False
    destination.seek(0)
    assert destination.read()  # still fully readable


@pytest.mark.django_db(transaction=True)
def test_populated_library_produces_valid_zip_with_deterministic_member_order():
    owner = create_account("populated-archive-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Plan")

    destination, metadata = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
    assert names[0] == "manifest.json"
    assert names[1] == "README.txt"
    assert names[2] == "notes/active/Projects/Plan.md"
    assert metadata.entry_count == 3
    assert metadata.note_count == 1
    assert metadata.folder_count == 1


@pytest.mark.django_db(transaction=True)
def test_repeated_calls_produce_identical_member_order():
    owner = create_account("deterministic-archive-owner")
    for _ in range(3):
        services.create_note(owner=owner)

    first, _ = _write(owner)
    second, _ = _write(owner)

    with zipfile.ZipFile(first) as archive:
        first_names = archive.namelist()
    with zipfile.ZipFile(second) as archive:
        second_names = archive.namelist()
    assert first_names == second_names


# ---------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_manifest_json_is_valid_utf8_with_trailing_newline():
    owner = create_account("manifest-json-owner")
    services.create_folder(owner=owner, name="Café Notes")

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        raw = archive.read("manifest.json")
    text = raw.decode("utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "Café Notes" in text  # ensure_ascii=False keeps Unicode readable
    json.loads(text)  # must parse


@pytest.mark.django_db(transaction=True)
def test_generated_manifest_parses_through_parse_library_backup_manifest():
    owner = create_account("manifest-parses-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    services.create_note(owner=owner, folder=folder)

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        raw = archive.read("manifest.json")
    parsed = parse_library_backup_manifest(json.loads(raw))
    assert len(parsed.notes) == 1
    assert len(parsed.folders) == 1


# ---------------------------------------------------------------------------
# README.txt
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_readme_exists_with_trailing_newline_and_required_statements():
    owner = create_account("readme-owner")

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        text = archive.read("README.txt").decode("utf-8")
    assert text.endswith("\n")
    assert "RidgeNote Library Backup" in text
    assert "manifest.json" in text
    assert "authoritative" in text
    assert "not change manifest.json" in text
    assert "credentials" in text
    assert "sensitive" in text
    assert "PostgreSQL" in text


@pytest.mark.django_db(transaction=True)
def test_readme_reports_accurate_counts():
    owner = create_account("readme-counts-owner")
    services.create_folder(owner=owner, name="Folder")
    services.get_or_create_tag(owner=owner, name="Tag", color="blue")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Active")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed")
    _trash(trashed_note)
    recovery_note = services.create_note(owner=owner)
    services.rename_note(note=recovery_note, title="Recovered")
    _trash(recovery_note)
    _empty(recovery_note)

    destination, metadata = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        text = archive.read("README.txt").decode("utf-8")
    assert "Folders: 1" in text
    assert "Tags: 1" in text
    assert "Notes: 3 total (1 active, 1 trash, 1 recovery)" in text
    assert metadata.note_count == 3
    assert metadata.folder_count == 1
    assert metadata.tag_count == 1


# ---------------------------------------------------------------------------
# Markdown members
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_every_note_has_exactly_one_markdown_member_matching_note_to_markdown():
    owner = create_account("markdown-members-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="My Note")

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        raw = archive.read("manifest.json")
        manifest = parse_library_backup_manifest(json.loads(raw))
        markdown_names = [name for name in archive.namelist() if name.endswith(".md")]
        assert markdown_names == [manifest.notes[0].markdown_path]
        content = archive.read(manifest.notes[0].markdown_path).decode("utf-8")

    expected = note_to_markdown(manifest.notes[0].title, manifest.notes[0].body_json)
    assert content == expected


@pytest.mark.django_db(transaction=True)
def test_no_unexpected_markdown_members():
    owner = create_account("no-orphan-markdown-owner")
    for _ in range(3):
        services.create_note(owner=owner)

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        raw = archive.read("manifest.json")
        manifest = parse_library_backup_manifest(json.loads(raw))
        markdown_names = {name for name in archive.namelist() if name.endswith(".md")}

    assert markdown_names == {note.markdown_path for note in manifest.notes}


@pytest.mark.django_db(transaction=True)
def test_active_trash_recovery_and_unfiled_paths_present():
    owner = create_account("lifecycle-paths-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    active_note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=active_note, title="Active")

    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed")
    _trash(trashed_note)

    recovery_note = services.create_note(owner=owner)
    services.rename_note(note=recovery_note, title="Recovered")
    _trash(recovery_note)
    _empty(recovery_note)

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())

    assert "notes/active/Projects/Active.md" in names
    assert "notes/trash/Unfiled/Trashed.md" in names
    assert "notes/recovery/Unfiled/Recovered.md" in names


@pytest.mark.django_db(transaction=True)
def test_sanitized_and_colliding_names_appear_correctly():
    owner = create_account("sanitized-names-owner")
    first = services.create_note(owner=owner)
    services.rename_note(note=first, title="Same Title")
    second = services.create_note(owner=owner)
    services.rename_note(note=second, title="Same Title")

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        names = {name for name in archive.namelist() if name.endswith(".md")}

    assert names == {
        "notes/active/Unfiled/Same Title.md",
        "notes/active/Unfiled/Same Title (2).md",
    }


@pytest.mark.django_db(transaction=True)
def test_unicode_filenames_are_preserved_in_the_zip():
    owner = create_account("unicode-names-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Café Ideas 日本語")

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()

    assert "notes/active/Unfiled/Café Ideas 日本語.md" in names


# ---------------------------------------------------------------------------
# Privacy / isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_other_owner_content_excluded():
    owner = create_account("archive-owner-a")
    other = create_account("archive-owner-b")
    other_folder = services.create_folder(owner=other, name="Not Mine")
    services.create_note(owner=other, folder=other_folder)

    destination, metadata = _write(owner)

    assert metadata.note_count == 0
    assert metadata.folder_count == 0
    with zipfile.ZipFile(destination) as archive:
        assert "Not Mine" not in "".join(archive.namelist())
        assert b"Not Mine" not in archive.read("manifest.json")


@pytest.mark.django_db(transaction=True)
def test_no_raw_database_ids_or_account_session_audit_data():
    owner = create_account("no-leak-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    services.create_note(owner=owner, folder=folder)

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        raw = archive.read("manifest.json")
        readme_text = archive.read("README.txt").decode("utf-8")

    manifest = json.loads(raw)
    assert manifest["folders"][0]["id"] != str(folder.id)
    assert manifest["notes"][0]["id"] != str(folder.id)
    manifest_text = raw.decode("utf-8")
    assert "password" not in manifest_text.lower()
    assert "session" not in manifest_text.lower()
    assert owner.username not in manifest_text
    # README legitimately *mentions* "password material" as an excluded
    # category -- it must never contain an actual password value.
    assert PASSWORD not in readme_text


# ---------------------------------------------------------------------------
# Timestamps and member metadata
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_shared_export_timestamp_used_for_filename_and_every_member():
    # `display_timezone` is pinned to UTC here so
    # this test's own concern (one shared timestamp drives the filename
    # and every ZIP member identically) stays independent of the
    # filename's own timezone-localization behavior, covered separately
    # in test_library_backup_filename_timezone.py.
    owner = create_account("shared-timestamp-owner")
    services.create_note(owner=owner)

    destination, metadata = _write(owner, display_timezone=ZoneInfo("UTC"))

    assert metadata.filename == "ridgenote-library-backup-2026-08-06_180000.zip"
    assert metadata.exported_at == FIXED_TIME

    with zipfile.ZipFile(destination) as archive:
        date_times = {info.date_time for info in archive.infolist()}
    assert date_times == {(2026, 8, 6, 18, 0, 0)}


@pytest.mark.django_db(transaction=True)
def test_members_are_ordinary_files_with_no_comment():
    owner = create_account("ordinary-files-owner")
    services.create_note(owner=owner)

    destination, _ = _write(owner)

    with zipfile.ZipFile(destination) as archive:
        assert archive.comment == b""
        for info in archive.infolist():
            unix_mode = info.external_attr >> 16
            # Regular file bit set, no symlink bit (0o120000) present.
            assert unix_mode & 0o170000 != 0o120000
            assert unix_mode & 0o170000 != 0o020000  # not a character device
            assert unix_mode & 0o170000 != 0o060000  # not a block device


# ---------------------------------------------------------------------------
# Generation limits
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_note_limit_exceeded(monkeypatch):
    owner = create_account("note-limit-owner")
    services.create_note(owner=owner)
    monkeypatch.setattr(library_backup_archive, "MAX_NOTE_COUNT", 0)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "note_limit_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_folder_limit_exceeded(monkeypatch):
    owner = create_account("folder-limit-owner")
    services.create_folder(owner=owner, name="Folder")
    monkeypatch.setattr(library_backup_archive, "MAX_FOLDER_COUNT", 0)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "folder_limit_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_entry_limit_exceeded(monkeypatch):
    owner = create_account("entry-limit-owner")
    services.create_note(owner=owner)
    monkeypatch.setattr(library_backup_archive, "MAX_ARCHIVE_ENTRIES", 1)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "entry_limit_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_manifest_size_exceeded(monkeypatch):
    owner = create_account("manifest-size-owner")
    monkeypatch.setattr(library_backup_archive, "MAX_MANIFEST_BYTES", 1)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "manifest_size_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_markdown_size_exceeded(monkeypatch):
    owner = create_account("markdown-size-owner")
    services.create_note(owner=owner)
    monkeypatch.setattr(library_backup_archive, "MAX_MARKDOWN_FILE_BYTES", 1)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "markdown_size_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_uncompressed_size_exceeded(monkeypatch):
    owner = create_account("uncompressed-size-owner")
    monkeypatch.setattr(library_backup_archive, "MAX_UNCOMPRESSED_TOTAL_BYTES", 1)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "uncompressed_size_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_compressed_size_exceeded(monkeypatch):
    owner = create_account("compressed-size-owner")
    services.create_note(owner=owner)
    monkeypatch.setattr(library_backup_archive, "MAX_COMPRESSED_ARCHIVE_BYTES", 1)

    destination = io.BytesIO()
    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner, destination)

    assert excinfo.value.code == "compressed_size_exceeded"
    assert destination.closed is False


@pytest.mark.django_db(transaction=True)
def test_generation_error_messages_never_contain_note_content(monkeypatch):
    owner = create_account("safe-error-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Super Secret Title")
    monkeypatch.setattr(library_backup_archive, "MAX_MARKDOWN_FILE_BYTES", 1)

    with pytest.raises(library_backup_archive.LibraryBackupGenerationError) as excinfo:
        _write(owner)

    assert "Super Secret Title" not in str(excinfo.value)
    assert "Super Secret Title" not in excinfo.value.code
