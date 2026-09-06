"""Library-backup manifest foundation.

Covers `notes.library_backup`'s version-1 manifest serializer
(`build_library_backup_manifest`) and parser/validator
(`parse_library_backup_manifest`). This module is not reachable from
any route or view -- these are the only tests that exercise it.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from notes import documents, services
from notes.library_backup import (
    LIBRARY_BACKUP_FORMAT_IDENTIFIER,
    LIBRARY_BACKUP_FORMAT_VERSION,
    LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT,
    LibraryBackupValidationError,
    build_library_backup_manifest,
    parse_library_backup_manifest,
)
from notes.models import DEFAULT_TAG_COLOR, Folder, Note

PASSWORD = "LongUniquePassword123!"
FIXED_TIME = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


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


def _recovery_folder(owner, name="Recovered Items"):
    return Folder.objects.create(owner=owner, name=name, is_recovery_folder=True)


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_manifest_of_empty_library_has_empty_arrays_not_an_error():
    owner = create_account("empty-library-owner")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["folders"] == []
    assert manifest["tags"] == []
    assert manifest["notes"] == []
    assert manifest["format_identifier"] == LIBRARY_BACKUP_FORMAT_IDENTIFIER
    assert manifest["format_version"] == LIBRARY_BACKUP_FORMAT_VERSION
    assert manifest["exported_at"] == "2026-08-06T12:00:00+00:00"
    assert manifest["scope"] == {"active": True, "trash": True, "recovery": True}


@pytest.mark.django_db(transaction=True)
def test_manifest_excludes_another_owners_content():
    owner = create_account("owner-a")
    other = create_account("owner-b")
    folder = services.create_folder(owner=other, name="Other's Folder")
    services.create_note(owner=other, folder=folder)
    services.get_or_create_tag(owner=other, name="OtherTag", color="blue")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["folders"] == []
    assert manifest["tags"] == []
    assert manifest["notes"] == []


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_active_folder():
    owner = create_account("active-folder-owner")
    services.create_folder(owner=owner, name="Projects")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert len(manifest["folders"]) == 1
    entry = manifest["folders"][0]
    assert entry["id"] == "folder-000001"
    assert entry["name"] == "Projects"
    assert entry["lifecycle"] == "active"
    assert entry["trashed_at"] is None
    assert entry["emptied_at"] is None
    assert entry["is_recovery_folder"] is False
    assert entry["created_at"]


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_trashed_folder():
    owner = create_account("trashed-folder-owner")
    folder = services.create_folder(owner=owner, name="Old Projects")
    _trash(folder)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["folders"][0]
    assert entry["lifecycle"] == "trash"
    assert entry["trashed_at"] is not None
    assert entry["emptied_at"] is None


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_recovery_folder():
    owner = create_account("recovery-folder-owner")
    folder = services.create_folder(owner=owner, name="Emptied Projects")
    _trash(folder)
    _empty(folder)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["folders"][0]
    assert entry["lifecycle"] == "recovery"
    assert entry["trashed_at"] is not None
    assert entry["emptied_at"] is not None


@pytest.mark.django_db(transaction=True)
def test_manifest_marks_active_recovery_folder():
    owner = create_account("recovery-marker-owner")
    _recovery_folder(owner)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["folders"][0]
    assert entry["is_recovery_folder"] is True
    assert entry["lifecycle"] == "active"


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_active_note():
    owner = create_account("active-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="My Note")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["notes"][0]
    assert entry["id"] == "note-000001"
    assert entry["title"] == "My Note"
    assert entry["lifecycle"] == "active"
    assert entry["folder_id"] is None
    assert entry["body_json"] == note.body_json


@pytest.mark.django_db(transaction=True)
def test_manifest_note_includes_markdown_path():
    owner = create_account("markdown-path-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="My Note")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["notes"][0]
    assert entry["markdown_path"] == "notes/active/Unfiled/My Note.md"


@pytest.mark.django_db(transaction=True)
def test_manifest_markdown_path_reflects_note_lifecycle_not_folder_lifecycle():
    owner = create_account("markdown-path-lifecycle-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Filed but trashed")
    _trash(note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["notes"][0]
    assert entry["markdown_path"] == "notes/trash/Projects/Filed but trashed.md"


@pytest.mark.django_db(transaction=True)
def test_manifest_markdown_path_reserves_unfiled_and_suffixes_colliding_titles():
    owner = create_account("markdown-path-collision-owner")
    services.create_note(owner=owner)
    services.create_note(owner=owner)
    notes = list(Note.objects.filter(owner=owner).order_by("id"))
    services.rename_note(note=notes[0], title="Same Title")
    services.rename_note(note=notes[1], title="Same Title")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    paths = sorted(entry["markdown_path"] for entry in manifest["notes"])
    assert paths == ["notes/active/Unfiled/Same Title (2).md", "notes/active/Unfiled/Same Title.md"]


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_trashed_note():
    owner = create_account("trashed-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed")
    _trash(note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["notes"][0]
    assert entry["lifecycle"] == "trash"
    assert entry["trashed_at"] is not None
    assert entry["emptied_at"] is None


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_recovery_note():
    owner = create_account("recovery-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Recovered")
    _trash(note)
    _empty(note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    entry = manifest["notes"][0]
    assert entry["lifecycle"] == "recovery"
    assert entry["trashed_at"] is not None
    assert entry["emptied_at"] is not None


@pytest.mark.django_db(transaction=True)
def test_manifest_allows_trashed_note_referencing_active_folder():
    owner = create_account("trashed-note-active-folder-owner")
    folder = services.create_folder(owner=owner, name="Still Active")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Trashed but filed")
    _trash(note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    note_entry = manifest["notes"][0]
    folder_entry = manifest["folders"][0]
    assert note_entry["lifecycle"] == "trash"
    assert folder_entry["lifecycle"] == "active"
    assert note_entry["folder_id"] == folder_entry["id"]


@pytest.mark.django_db(transaction=True)
def test_manifest_allows_recovery_note_referencing_active_folder():
    owner = create_account("recovery-note-active-folder-owner")
    folder = services.create_folder(owner=owner, name="Still Active Too")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Emptied but filed")
    _trash(note)
    _empty(note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    note_entry = manifest["notes"][0]
    folder_entry = manifest["folders"][0]
    assert note_entry["lifecycle"] == "recovery"
    assert folder_entry["lifecycle"] == "active"
    assert note_entry["folder_id"] == folder_entry["id"]


@pytest.mark.django_db(transaction=True)
def test_manifest_unfiled_note_has_null_folder_id():
    owner = create_account("unfiled-note-owner")
    services.create_note(owner=owner)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["notes"][0]["folder_id"] is None


@pytest.mark.django_db(transaction=True)
def test_manifest_includes_tags_and_relationships():
    owner = create_account("tags-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Tagged")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert len(manifest["tags"]) == 1
    tag_entry = manifest["tags"][0]
    assert tag_entry == {"id": "tag-000001", "name": "Work", "color": "blue"}
    assert manifest["notes"][0]["tag_ids"] == ["tag-000001"]


@pytest.mark.django_db(transaction=True)
def test_manifest_unaffected_by_owner_tag_preferences():
    # Tag Preferences are account/user
    # preferences, excluded from library backup/restore -- the manifest
    # for the same underlying Tags must be identical regardless of the
    # owner's stored tag_uppercase_enabled/tag_semantic_color_enabled/
    # tag_default_color values.
    owner = create_account(
        "tags-prefs-owner",
        tag_uppercase_enabled=False,
        tag_semantic_color_enabled=False,
        tag_default_color="violet",
    )
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Tagged")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert len(manifest["tags"]) == 1
    tag_entry = manifest["tags"][0]
    assert tag_entry == {"id": "tag-000001", "name": "Work", "color": "blue"}
    assert "tag_uppercase_enabled" not in str(manifest)
    assert "tag_semantic_color_enabled" not in str(manifest)
    assert "tag_default_color" not in str(manifest)


@pytest.mark.django_db(transaction=True)
def test_manifest_round_trips_a_normalized_tag_name_verbatim():
    # A name normalized (uppercased, whitespace-
    # collapsed, punctuation-preserving) by `normalize_tag_name()` before
    # `get_or_create_tag()` is called (exactly what the Add Tag view
    # does) must round-trip through the manifest exactly as stored --
    # no separate backup-layer normalization or re-normalization exists
    # or is needed.
    owner = create_account("normalized-tag-backup-owner")
    normalized_name = services.normalize_tag_name("  home   lab #1  ", uppercase=True)
    tag = services.get_or_create_tag(owner=owner, name=normalized_name, color="amber")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["tags"][0]["name"] == "HOME LAB #1"
    parsed = parse_library_backup_manifest(manifest)
    assert parsed.tags[0].name == "HOME LAB #1"


@pytest.mark.django_db(transaction=True)
def test_manifest_round_trips_a_semantically_chosen_stored_color():
    # The resulting stored color of a newly
    # created tag -- whether from an explicit selection or a semantic
    # match -- is just an ordinary `Tag.color` value by the time the
    # backup layer ever sees it; no new manifest field or backup-layer
    # logic is needed or exists.
    owner = create_account("semantic-tag-backup-owner")
    normalized_name = services.normalize_tag_name("red", uppercase=True)
    color = services.resolve_tag_color_for_creation("", normalized_name)
    tag = services.get_or_create_tag(owner=owner, name=normalized_name, color=color)
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["tags"][0]["name"] == "RED"
    assert manifest["tags"][0]["color"] == "red"
    parsed = parse_library_backup_manifest(manifest)
    assert parsed.tags[0].color == "red"


@pytest.mark.django_db(transaction=True)
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
def test_manifest_and_parser_round_trip_every_tag_color(color):
    owner = create_account(f"tag-color-{color}-owner")
    tag = services.get_or_create_tag(owner=owner, name="Colored", color=color)
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    assert manifest["tags"][0]["color"] == color

    parsed = parse_library_backup_manifest(manifest)
    assert parsed.tags[0].color == color


@pytest.mark.django_db(transaction=True)
def test_manifest_preserves_pin_state():
    owner = create_account("pin-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pinned")
    services.set_note_pinned(note=note, pinned=True)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["notes"][0]["pinned"] is True


@pytest.mark.django_db(transaction=True)
def test_manifest_ids_are_deterministic_across_calls():
    owner = create_account("deterministic-owner")
    services.create_folder(owner=owner, name="A")
    services.create_folder(owner=owner, name="B")
    services.get_or_create_tag(owner=owner, name="X", color="blue")

    first = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    second = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert first["folders"] == second["folders"]
    assert first["tags"] == second["tags"]


@pytest.mark.django_db(transaction=True)
def test_manifest_folder_ordering_is_case_insensitive_by_name():
    owner = create_account("ordering-owner")
    services.create_folder(owner=owner, name="banana")
    services.create_folder(owner=owner, name="Apple")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert [entry["name"] for entry in manifest["folders"]] == ["Apple", "banana"]


@pytest.mark.django_db(transaction=True)
def test_manifest_timestamps_are_timezone_aware_iso_8601_utc():
    owner = create_account("timestamp-owner")
    services.create_folder(owner=owner, name="Timestamped")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    created_at = manifest["folders"][0]["created_at"]
    assert created_at.endswith("+00:00")
    parsed = datetime.fromisoformat(created_at)
    assert parsed.tzinfo is not None


@pytest.mark.django_db(transaction=True)
def test_manifest_preserves_native_body_json_exactly():
    owner = create_account("body-owner")
    note = services.create_note(owner=owner)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["notes"][0]["body_json"] == note.body_json


@pytest.mark.django_db(transaction=True)
def test_manifest_contains_no_raw_database_ids():
    owner = create_account("no-raw-id-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    note = services.create_note(owner=owner, folder=folder)
    tag = services.get_or_create_tag(owner=owner, name="Tag", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert manifest["folders"][0]["id"] != str(folder.id)
    assert manifest["notes"][0]["id"] != str(note.id)
    assert manifest["tags"][0]["id"] != str(tag.id)
    assert manifest["notes"][0]["folder_id"] == manifest["folders"][0]["id"]


@pytest.mark.django_db(transaction=True)
def test_manifest_contains_no_account_session_or_audit_data():
    owner = create_account("no-account-data-owner")
    services.create_note(owner=owner)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    assert set(manifest.keys()) == {
        "format_identifier",
        "format_version",
        "exported_at",
        "scope",
        "folders",
        "notes",
        "tags",
    }
    assert "owner" not in manifest["notes"][0]
    assert "password" not in str(manifest).lower()


@pytest.mark.django_db(transaction=True)
def test_manifest_is_plain_json_serializable():
    owner = create_account("json-serializable-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    note = services.create_note(owner=owner, folder=folder)
    tag = services.get_or_create_tag(owner=owner, name="Tag", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)

    json.dumps(manifest)  # must not raise


@pytest.mark.django_db(transaction=True)
def test_manifest_query_count_does_not_grow_with_note_count():
    owner = create_account("query-count-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    tag = services.get_or_create_tag(owner=owner, name="Tag", color="blue")
    for _ in range(10):
        note = services.create_note(owner=owner, folder=folder)
        services.assign_tag_to_note(note=note, tag=tag)

    with CaptureQueriesContext(connection) as ten_notes_queries:
        build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    ten_count = len(ten_notes_queries.captured_queries)

    for _ in range(20):
        note = services.create_note(owner=owner, folder=folder)
        services.assign_tag_to_note(note=note, tag=tag)

    with CaptureQueriesContext(connection) as thirty_notes_queries:
        build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    thirty_count = len(thirty_notes_queries.captured_queries)

    assert thirty_count <= ten_count


# ---------------------------------------------------------------------------
# Parser / validator -- acceptance
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_parser_accepts_empty_manifest():
    owner = create_account("parser-empty-owner")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    parsed = parse_library_backup_manifest(manifest)

    assert parsed.folders == ()
    assert parsed.notes == ()
    assert parsed.tags == ()


@pytest.mark.django_db(transaction=True)
def test_parser_accepts_populated_manifest_round_trip():
    owner = create_account("parser-populated-owner")
    folder = services.create_folder(owner=owner, name="Folder")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Round trip")
    tag = services.get_or_create_tag(owner=owner, name="Tag", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)
    services.set_note_pinned(note=note, pinned=True)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    parsed = parse_library_backup_manifest(manifest)

    assert len(parsed.folders) == 1
    assert len(parsed.notes) == 1
    assert len(parsed.tags) == 1
    parsed_note = parsed.notes[0]
    assert parsed_note.title == "Round trip"
    assert parsed_note.pinned is True
    assert parsed_note.folder_id == parsed.folders[0].id
    assert parsed_note.tag_ids == (parsed.tags[0].id,)
    assert parsed_note.body_json == note.body_json
    assert parsed_note.created_at.tzinfo is not None
    assert parsed_note.markdown_path == manifest["notes"][0]["markdown_path"]
    assert parsed_note.markdown_path == "notes/active/Folder/Round trip.md"


@pytest.mark.django_db(transaction=True)
def test_parser_accepts_all_three_lifecycle_states():
    owner = create_account("parser-lifecycle-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Active")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed")
    _trash(trashed_note)
    recovery_note = services.create_note(owner=owner)
    services.rename_note(note=recovery_note, title="Recovered")
    _trash(recovery_note)
    _empty(recovery_note)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    parsed = parse_library_backup_manifest(manifest)

    lifecycles = {note.lifecycle for note in parsed.notes}
    assert lifecycles == {"active", "trash", "recovery"}


@pytest.mark.django_db(transaction=True)
def test_parser_accepts_recovery_marker_folder():
    owner = create_account("parser-recovery-marker-owner")
    _recovery_folder(owner)

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    parsed = parse_library_backup_manifest(manifest)

    assert parsed.folders[0].is_recovery_folder is True


@pytest.mark.django_db(transaction=True)
def test_parser_accepts_future_timezone_aware_timestamp():
    owner = create_account("parser-future-owner")
    future = timezone.now() + timedelta(days=365)

    manifest = build_library_backup_manifest(owner, exported_at=future)
    parsed = parse_library_backup_manifest(manifest)

    assert parsed.exported_at.year >= future.year


# ---------------------------------------------------------------------------
# Parser / validator -- rejection
# ---------------------------------------------------------------------------


def _valid_manifest():
    return {
        "format_identifier": LIBRARY_BACKUP_FORMAT_IDENTIFIER,
        "format_version": LIBRARY_BACKUP_FORMAT_VERSION,
        "exported_at": "2026-08-06T12:00:00+00:00",
        "scope": {"active": True, "trash": True, "recovery": True},
        "folders": [],
        "tags": [],
        "notes": [],
    }


def _assert_rejected(manifest):
    with pytest.raises(LibraryBackupValidationError) as excinfo:
        parse_library_backup_manifest(manifest)
    return excinfo.value


def test_parser_rejects_non_mapping_root():
    _assert_rejected(["not", "a", "mapping"])


def test_parser_rejects_wrong_format_identifier():
    manifest = _valid_manifest()
    manifest["format_identifier"] = "something-else"
    error = _assert_rejected(manifest)
    assert any(e.code == "unsupported_format" for e in error.errors)


def test_parser_rejects_unsupported_format_version():
    manifest = _valid_manifest()
    manifest["format_version"] = 99
    error = _assert_rejected(manifest)
    assert any(e.code == "unsupported_version" for e in error.errors)


def test_parser_rejects_missing_required_key():
    manifest = _valid_manifest()
    del manifest["notes"]
    error = _assert_rejected(manifest)
    assert any(e.code == "missing_fields" for e in error.errors)


def test_parser_rejects_unknown_top_level_key():
    manifest = _valid_manifest()
    manifest["unexpected_field"] = "surprise"
    error = _assert_rejected(manifest)
    assert any(e.code == "unknown_fields" for e in error.errors)


def test_parser_rejects_owner_account_field():
    manifest = _valid_manifest()
    manifest["owner_username"] = "someone"
    error = _assert_rejected(manifest)
    assert any(e.code == "unknown_fields" for e in error.errors)


def test_parser_rejects_wrong_scalar_type():
    manifest = _valid_manifest()
    manifest["format_version"] = "1"
    error = _assert_rejected(manifest)
    assert any(e.code == "unsupported_version" for e in error.errors)


def test_parser_rejects_folders_not_a_list():
    manifest = _valid_manifest()
    manifest["folders"] = {}
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_type" and e.path == "folders" for e in error.errors)


def test_parser_rejects_boolean_used_as_scope_flag_incorrectly():
    manifest = _valid_manifest()
    manifest["scope"]["active"] = 1
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_type" and e.path == "scope.active" for e in error.errors)


def test_parser_rejects_malformed_scope():
    manifest = _valid_manifest()
    manifest["scope"] = ["active"]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_type" and e.path == "scope" for e in error.errors)


def _folder_record(**overrides):
    record = {
        "id": "folder-000001",
        "name": "Folder",
        "lifecycle": "active",
        "trashed_at": None,
        "emptied_at": None,
        "is_recovery_folder": False,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_parser_rejects_malformed_folder_id():
    manifest = _valid_manifest()
    manifest["folders"] = [_folder_record(id="not-a-real-id")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_id" for e in error.errors)


def test_parser_rejects_duplicate_folder_ids():
    manifest = _valid_manifest()
    manifest["folders"] = [_folder_record(), _folder_record()]
    error = _assert_rejected(manifest)
    assert any(e.code == "duplicate_id" for e in error.errors)


def test_parser_rejects_malformed_timestamp():
    manifest = _valid_manifest()
    manifest["folders"] = [_folder_record(created_at="not-a-timestamp")]
    error = _assert_rejected(manifest)
    assert any(e.code == "malformed_timestamp" for e in error.errors)


def test_parser_rejects_naive_timestamp():
    manifest = _valid_manifest()
    manifest["folders"] = [_folder_record(created_at="2026-01-01T00:00:00")]
    error = _assert_rejected(manifest)
    assert any(e.code == "naive_timestamp" for e in error.errors)


def test_parser_rejects_invalid_lifecycle_value():
    manifest = _valid_manifest()
    manifest["folders"] = [_folder_record(lifecycle="bogus")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_lifecycle" for e in error.errors)


def test_parser_rejects_lifecycle_timestamp_mismatch():
    manifest = _valid_manifest()
    manifest["folders"] = [
        _folder_record(lifecycle="active", trashed_at="2026-01-01T00:00:00+00:00")
    ]
    error = _assert_rejected(manifest)
    assert any(e.code == "lifecycle_mismatch" for e in error.errors)


def test_parser_rejects_recovery_marker_on_trashed_folder():
    manifest = _valid_manifest()
    manifest["folders"] = [
        _folder_record(
            lifecycle="trash",
            trashed_at="2026-01-01T00:00:00+00:00",
            is_recovery_folder=True,
        )
    ]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_recovery_marker" for e in error.errors)


def _note_record(**overrides):
    record = {
        "id": "note-000001",
        "title": "Title",
        "body_json": {"type": "doc", "content": [{"type": "paragraph"}]},
        "folder_id": None,
        "tag_ids": [],
        "pinned": False,
        "lifecycle": "active",
        "trashed_at": None,
        "emptied_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "modified_at": "2026-01-01T00:00:00+00:00",
        "markdown_path": "notes/active/Unfiled/Title.md",
    }
    record.update(overrides)
    return record


def test_parser_rejects_broken_folder_reference():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(folder_id="folder-000099")]
    error = _assert_rejected(manifest)
    assert any(e.code == "broken_reference" and "folder_id" in (e.path or "") for e in error.errors)


def test_parser_rejects_broken_tag_reference():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(tag_ids=["tag-000099"])]
    error = _assert_rejected(manifest)
    assert any(e.code == "broken_reference" and "tag_ids" in (e.path or "") for e in error.errors)


def test_parser_rejects_duplicate_note_tag_references():
    manifest = _valid_manifest()
    manifest["tags"] = [{"id": "tag-000001", "name": "Tag"}]
    manifest["notes"] = [_note_record(tag_ids=["tag-000001", "tag-000001"])]
    error = _assert_rejected(manifest)
    assert any(e.code == "duplicate_reference" for e in error.errors)


def test_parser_defaults_missing_tag_color_for_backward_compatibility():
    # A pre-correction version-1 archive never had a `color` key at all --
    # it must remain fully parseable, defaulting to DEFAULT_TAG_COLOR
    # rather than being rejected.
    manifest = _valid_manifest()
    manifest["tags"] = [{"id": "tag-000001", "name": "Tag"}]
    parsed = parse_library_backup_manifest(manifest)
    assert parsed.tags[0].color == DEFAULT_TAG_COLOR


def test_parser_accepts_present_tag_color():
    manifest = _valid_manifest()
    manifest["tags"] = [{"id": "tag-000001", "name": "Tag", "color": "rose"}]
    parsed = parse_library_backup_manifest(manifest)
    assert parsed.tags[0].color == "rose"


def test_parser_rejects_unsupported_tag_color():
    manifest = _valid_manifest()
    manifest["tags"] = [{"id": "tag-000001", "name": "Tag", "color": "not-a-real-color"}]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_tag_color" for e in error.errors)


def test_parser_rejects_invalid_body_json():
    manifest = _valid_manifest()
    manifest["notes"] = [
        _note_record(body_json={"type": "doc", "content": [{"type": "blockquote"}]})
    ]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_body" for e in error.errors)


def test_parser_rejects_note_missing_markdown_path():
    manifest = _valid_manifest()
    record = _note_record()
    del record["markdown_path"]
    manifest["notes"] = [record]
    error = _assert_rejected(manifest)
    assert any(e.code == "missing_fields" for e in error.errors)


def test_parser_rejects_absolute_markdown_path():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="/notes/active/Unfiled/Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_windows_drive_prefixed_markdown_path():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="C:/notes/active/Unfiled/Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_backslash_in_markdown_path():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="notes\\active\\Unfiled\\Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_traversal_in_markdown_path():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="notes/active/../../etc/Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_markdown_path_with_wrong_lifecycle_root():
    manifest = _valid_manifest()
    manifest["notes"] = [
        _note_record(lifecycle="active", markdown_path="notes/trash/Unfiled/Title.md")
    ]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_markdown_path_with_wrong_extension():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="notes/active/Unfiled/Title.txt")]
    error = _assert_rejected(manifest)
    assert any(e.code == "invalid_path" for e in error.errors)


def test_parser_rejects_markdown_path_with_reserved_windows_segment():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="notes/active/CON/Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "reserved_name" for e in error.errors)


def test_parser_rejects_markdown_path_with_hidden_segment():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path="notes/active/.secret/Title.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "hidden_segment" for e in error.errors)


def test_parser_rejects_overlong_markdown_path():
    manifest = _valid_manifest()
    long_segment = "x" * 250
    manifest["notes"] = [_note_record(markdown_path=f"notes/active/Unfiled/{long_segment}.md")]
    error = _assert_rejected(manifest)
    assert any(e.code == "path_too_long" for e in error.errors)


def test_parser_rejects_duplicate_case_insensitive_markdown_paths():
    manifest = _valid_manifest()
    manifest["notes"] = [
        _note_record(id="note-000001", markdown_path="notes/active/Unfiled/Title.md"),
        _note_record(id="note-000002", markdown_path="notes/active/Unfiled/TITLE.md"),
    ]
    error = _assert_rejected(manifest)
    assert any(e.code == "duplicate_path" for e in error.errors)


def test_parser_rejects_non_nfc_markdown_path():
    combining_form = "notes/active/Unfiled/Cafe\u0301.md"  # "Café" as e + combining acute
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(markdown_path=combining_form)]
    error = _assert_rejected(manifest)
    assert any(e.code == "non_canonical_path" for e in error.errors)


@pytest.mark.django_db(transaction=True)
def test_parser_folder_relationship_is_independent_of_markdown_path_folder_segment():
    owner = create_account("path-folder-independence-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title="Filed")

    manifest = build_library_backup_manifest(owner, exported_at=FIXED_TIME)
    parsed = parse_library_backup_manifest(manifest)

    # The manifest's markdown_path directory segment is a human-readable
    # convenience only -- folder identity comes from folder_id, which
    # would remain correct even if the path's folder segment differed
    # (e.g. after a hand-edited manifest renamed only the directory).
    manifest["notes"][0]["markdown_path"] = "notes/active/Renamed Directory/Filed.md"
    reparsed = parse_library_backup_manifest(manifest)
    assert reparsed.notes[0].folder_id == parsed.notes[0].folder_id == parsed.folders[0].id


def test_parser_rejects_raw_database_id_field():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(pk=12345)]
    error = _assert_rejected(manifest)
    assert any(e.code == "unknown_fields" for e in error.errors)


def test_parser_rejects_audit_field():
    manifest = _valid_manifest()
    manifest["notes"] = [{**_note_record(), "audit_event": "whatever"}]
    error = _assert_rejected(manifest)
    assert any(e.code == "unknown_fields" for e in error.errors)


def test_parser_accumulates_errors_up_to_cap_and_reports_truncation():
    manifest = _valid_manifest()
    manifest["folders"] = [
        _folder_record(id=f"folder-{i:06d}", created_at="not-a-timestamp") for i in range(1, 80)
    ]
    error = _assert_rejected(manifest)

    assert len(error.errors) == LIBRARY_BACKUP_VALIDATION_ERROR_LIMIT
    assert error.truncated is True


def test_parser_error_messages_never_contain_sensitive_input():
    manifest = _valid_manifest()
    manifest["notes"] = [_note_record(title="Super Secret Note Title")]
    manifest["notes"][0]["folder_id"] = "folder-000099"
    error = _assert_rejected(manifest)

    assert "Super Secret Note Title" not in str(error.errors)


# ---------------------------------------------------------------------------
# Regression -- existing note/document/lifecycle behavior unaffected
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_existing_note_export_payload_unaffected():
    owner = create_account("regression-export-owner")
    note = services.create_note(owner=owner)

    payload = services.note_export_payload(note)

    assert payload["format_identifier"] == "ridgenote-note"


@pytest.mark.django_db
def test_existing_document_validator_unaffected():
    doc = documents.canonical_empty_document()

    assert documents.validate_canonical_document(doc) == doc
