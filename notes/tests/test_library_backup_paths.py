"""Archive path-segment sanitization and
deterministic Markdown-path allocation.

Covers `notes.library_backup`'s `sanitize_backup_path_segment()` and
`build_backup_markdown_paths()` directly, against hand-built
`LibraryBackupManifest` fixtures -- no database access is required for
any test in this module.
"""

from datetime import UTC, datetime

from notes.library_backup import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_RECOVERY,
    LIFECYCLE_TRASH,
    LibraryBackupFolder,
    LibraryBackupManifest,
    LibraryBackupNote,
    _enforce_path_length,
    _sanitize_note_filename_base,
    build_backup_markdown_paths,
    sanitize_backup_path_segment,
)

FIXED_TIME = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


def _folder(id, name, *, lifecycle=LIFECYCLE_ACTIVE, is_recovery_folder=False):
    return LibraryBackupFolder(
        id=id,
        name=name,
        lifecycle=lifecycle,
        trashed_at=None if lifecycle == LIFECYCLE_ACTIVE else FIXED_TIME,
        emptied_at=FIXED_TIME if lifecycle == LIFECYCLE_RECOVERY else None,
        is_recovery_folder=is_recovery_folder,
        created_at=FIXED_TIME,
    )


def _note(id, title, *, folder_id=None, lifecycle=LIFECYCLE_ACTIVE):
    return LibraryBackupNote(
        id=id,
        title=title,
        body_json={"type": "doc", "content": [{"type": "paragraph"}]},
        folder_id=folder_id,
        tag_ids=(),
        pinned=False,
        lifecycle=lifecycle,
        trashed_at=None if lifecycle == LIFECYCLE_ACTIVE else FIXED_TIME,
        emptied_at=FIXED_TIME if lifecycle == LIFECYCLE_RECOVERY else None,
        created_at=FIXED_TIME,
        modified_at=FIXED_TIME,
        markdown_path="",
    )


def _manifest(*, folders=(), notes=()):
    return LibraryBackupManifest(
        format_identifier="ridgenote-library-backup",
        format_version=1,
        exported_at=FIXED_TIME,
        scope=(LIFECYCLE_ACTIVE, LIFECYCLE_TRASH, LIFECYCLE_RECOVERY),
        folders=tuple(folders),
        tags=(),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# sanitize_backup_path_segment
# ---------------------------------------------------------------------------


def test_ordinary_name_is_unchanged():
    assert sanitize_backup_path_segment("Project Notes", fallback="X") == "Project Notes"


def test_slash_and_backslash_replaced():
    result = sanitize_backup_path_segment("a/b\\c", fallback="X")
    assert "/" not in result
    assert "\\" not in result


def test_windows_invalid_characters_replaced():
    result = sanitize_backup_path_segment('a<b>c:d"e|f?g*h', fallback="X")
    for char in '<>:"|?*':
        assert char not in result


def test_control_characters_stripped():
    result = sanitize_backup_path_segment("a\x01\x02b", fallback="X")
    assert result == "ab"


def test_nul_stripped():
    result = sanitize_backup_path_segment("a\x00b", fallback="X")
    assert result == "ab"


def test_tab_and_newline_become_space():
    result = sanitize_backup_path_segment("a\tb\nc", fallback="X")
    assert result == "a b c"


def test_windows_reserved_name_prefixed():
    assert sanitize_backup_path_segment("CON", fallback="X") == "_CON"
    assert sanitize_backup_path_segment("con", fallback="X") == "_con"
    assert sanitize_backup_path_segment("LPT1", fallback="X") == "_LPT1"


def test_windows_reserved_name_with_extension_prefixed():
    assert sanitize_backup_path_segment("CON.txt", fallback="X") == "_CON.txt"


def test_non_reserved_name_containing_reserved_substring_unchanged():
    assert sanitize_backup_path_segment("CONcert", fallback="X") == "CONcert"


def test_leading_trailing_whitespace_trimmed():
    assert sanitize_backup_path_segment("  Name  ", fallback="X") == "Name"


def test_trailing_dots_trimmed():
    assert sanitize_backup_path_segment("Name...", fallback="X") == "Name"


def test_dot_and_dotdot_use_fallback():
    assert sanitize_backup_path_segment(".", fallback="Fallback") == "Fallback"
    assert sanitize_backup_path_segment("..", fallback="Fallback") == "Fallback"


def test_whitespace_only_input_uses_fallback():
    assert sanitize_backup_path_segment("   ", fallback="Fallback") == "Fallback"


def test_control_only_input_uses_fallback():
    assert sanitize_backup_path_segment("\x01\x02", fallback="Fallback") == "Fallback"


def test_leading_dot_prefixed_to_avoid_hidden_file():
    assert sanitize_backup_path_segment(".env", fallback="X") == "_env"
    assert sanitize_backup_path_segment(".notes", fallback="X") == "_notes"


def test_nfc_normalization_produces_stable_form():
    decomposed = "Café"  # e + combining acute accent
    precomposed = "Café"
    assert sanitize_backup_path_segment(decomposed, fallback="X") == precomposed


def test_long_segment_is_truncated():
    result = sanitize_backup_path_segment("x" * 500, fallback="X")
    assert len(result) == 80


def test_folder_segment_cap_is_80_not_120():
    # A generic path segment (folder use) is capped at 80 -- the
    # separate, more generous 120 cap is note-filename-base-only,
    # applied through the private `_sanitize_note_filename_base()`,
    # never through the public `sanitize_backup_path_segment()`.
    result = sanitize_backup_path_segment("x" * 100, fallback="X")
    assert len(result) == 80


def test_note_filename_base_between_81_and_120_is_not_truncated_to_80():
    title = "x" * 100
    result = _sanitize_note_filename_base(title, fallback="X")
    assert len(result) == 100
    assert result == title


def test_note_filename_base_longer_than_120_is_truncated_to_120():
    result = _sanitize_note_filename_base("x" * 300, fallback="X")
    assert len(result) == 120


def test_ordinary_punctuation_preserved():
    value = "Q&A - Notes (final)!"
    assert sanitize_backup_path_segment(value, fallback="X") == value


def test_colon_is_replaced_as_unsafe():
    result = sanitize_backup_path_segment("Meeting: Notes", fallback="X")
    assert ":" not in result


# ---------------------------------------------------------------------------
# build_backup_markdown_paths -- basic shape
# ---------------------------------------------------------------------------


def test_active_unfiled_note_path():
    manifest = _manifest(notes=[_note("note-000001", "My Note")])
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Unfiled/My Note.md"


def test_active_note_in_folder_path():
    folder = _folder("folder-000001", "Projects")
    note = _note("note-000001", "Plan", folder_id="folder-000001")
    manifest = _manifest(folders=[folder], notes=[note])
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Projects/Plan.md"


def test_all_paths_relative_with_forward_slashes_only():
    folder = _folder("folder-000001", "Projects")
    note = _note("note-000001", "Plan", folder_id="folder-000001")
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert not path.startswith("/")
    assert "\\" not in path
    assert ".." not in path.split("/")
    assert "" not in path.split("/")


def test_deterministic_across_repeated_calls():
    folder = _folder("folder-000001", "Projects")
    notes = [_note(f"note-{i:06d}", f"Note {i}", folder_id="folder-000001") for i in range(1, 6)]
    manifest = _manifest(folders=[folder], notes=notes)
    first = build_backup_markdown_paths(manifest)
    second = build_backup_markdown_paths(manifest)
    assert first == second


# ---------------------------------------------------------------------------
# Collision allocation
# ---------------------------------------------------------------------------


def test_duplicate_note_titles_in_same_folder_get_suffixed_in_manifest_order():
    notes = [_note("note-000001", "Same"), _note("note-000002", "Same")]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Unfiled/Same.md"
    assert paths["note-000002"] == "notes/active/Unfiled/Same (2).md"


def test_three_way_duplicate_titles_suffix_incrementally():
    notes = [_note(f"note-00000{i}", "Same") for i in (1, 2, 3)]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Unfiled/Same.md"
    assert paths["note-000002"] == "notes/active/Unfiled/Same (2).md"
    assert paths["note-000003"] == "notes/active/Unfiled/Same (3).md"


def test_case_insensitive_title_collision_is_suffixed():
    notes = [_note("note-000001", "Report"), _note("note-000002", "REPORT")]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert {paths["note-000001"], paths["note-000002"]} == {
        "notes/active/Unfiled/Report.md",
        "notes/active/Unfiled/REPORT (2).md",
    }


def test_sanitization_created_collision_is_suffixed():
    # "a/b" and "a\\b" both sanitize to the same segment ("a-b").
    notes = [_note("note-000001", "a/b"), _note("note-000002", "a\\b")]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert len({paths["note-000001"], paths["note-000002"]}) == 2


def test_titles_in_different_folders_do_not_collide():
    folder_a = _folder("folder-000001", "Alpha")
    folder_b = _folder("folder-000002", "Beta")
    notes = [
        _note("note-000001", "Same", folder_id="folder-000001"),
        _note("note-000002", "Same", folder_id="folder-000002"),
    ]
    manifest = _manifest(folders=[folder_a, folder_b], notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Alpha/Same.md"
    assert paths["note-000002"] == "notes/active/Beta/Same.md"


def test_duplicate_folder_names_are_suffixed():
    folder_a = _folder("folder-000001", "Work")
    folder_b = _folder("folder-000002", "Work")
    notes = [
        _note("note-000001", "First", folder_id="folder-000001"),
        _note("note-000002", "Second", folder_id="folder-000002"),
    ]
    manifest = _manifest(folders=[folder_a, folder_b], notes=notes)
    paths = build_backup_markdown_paths(manifest)
    directories = {paths["note-000001"].rsplit("/", 1)[0], paths["note-000002"].rsplit("/", 1)[0]}
    assert directories == {"notes/active/Work", "notes/active/Work (2)"}


def test_long_folder_and_note_names_stay_within_path_length_limit():
    folder = _folder("folder-000001", "x" * 300)
    note = _note("note-000001", "y" * 300, folder_id="folder-000001")
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert len(path) <= 240
    folder_segment, filename = path.split("/")[2], path.split("/")[3]
    assert len(folder_segment) == 80
    assert len(filename) == 123  # 120-char note-filename-base cap + ".md"


def test_note_title_up_to_120_chars_produces_unsuffixed_filename_at_most_130():
    note = _note("note-000001", "z" * 150)
    manifest = _manifest(notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    filename = path.rsplit("/", 1)[1]
    assert len(filename) <= 130
    assert filename == ("z" * 120) + ".md"


def test_suffixed_long_titles_do_not_exceed_130_character_filename():
    notes = [_note("note-000001", "z" * 120), _note("note-000002", "z" * 120)]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    filenames = {p.rsplit("/", 1)[1] for p in paths.values()}
    for filename in filenames:
        assert len(filename) <= 130
    # The second note's suffix must still be present and distinct.
    assert any(" (2).md" in name for name in filenames)


def test_suffix_allocation_after_truncation_is_deterministic_and_unique():
    notes = [_note(f"note-00000{i}", "z" * 120) for i in (1, 2, 3)]
    manifest = _manifest(notes=notes)
    first = build_backup_markdown_paths(manifest)
    second = build_backup_markdown_paths(manifest)
    assert first == second
    assert len(set(first.values())) == 3


def test_full_path_shortening_affects_filename_not_lifecycle_root():
    # Force `_enforce_path_length()`'s defensive shortening path
    # directly with an artificially oversized folder segment -- not
    # reachable through the public API today, since
    # `sanitize_backup_path_segment()`'s own 80-char cap keeps real
    # folder segments well under the 240-char full-path budget.
    huge_folder_segment = "f" * 220
    path = f"notes/active/{huge_folder_segment}/{'n' * 50}.md"
    shortened = _enforce_path_length(
        path,
        lifecycle="active",
        folder_segment=huge_folder_segment,
        filename=f"{'n' * 50}.md",
    )
    assert len(shortened) <= 240
    assert shortened.startswith(f"notes/active/{huge_folder_segment}/")
    filename = shortened.rsplit("/", 1)[1]
    assert filename.endswith(".md")
    assert filename != ".md"


def test_no_raw_database_ids_in_paths():
    folder = _folder("folder-000001", "Projects")
    note = _note("note-000001", "Note", folder_id="folder-000001")
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert "folder-000001" not in path
    assert "note-000001" not in path


# ---------------------------------------------------------------------------
# Unfiled reservation
# ---------------------------------------------------------------------------


def test_unfiled_reserved_for_null_folder_notes():
    manifest = _manifest(notes=[_note("note-000001", "Loose")])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert path.startswith("notes/active/Unfiled/")


def test_real_folder_colliding_with_unfiled_is_suffixed_not_the_reserved_bucket():
    folder = _folder("folder-000001", "Unfiled")
    notes = [
        _note("note-000001", "Loose"),  # folder_id=None -> reserved "Unfiled"
        _note("note-000002", "Filed", folder_id="folder-000001"),
    ]
    manifest = _manifest(folders=[folder], notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Unfiled/Loose.md"
    assert paths["note-000002"] == "notes/active/Unfiled (2)/Filed.md"


# ---------------------------------------------------------------------------
# Lifecycle roots
# ---------------------------------------------------------------------------


def test_each_lifecycle_uses_its_own_root():
    notes = [
        _note("note-000001", "Active", lifecycle=LIFECYCLE_ACTIVE),
        _note("note-000002", "Trashed", lifecycle=LIFECYCLE_TRASH),
        _note("note-000003", "Recovered", lifecycle=LIFECYCLE_RECOVERY),
    ]
    manifest = _manifest(notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"].startswith("notes/active/")
    assert paths["note-000002"].startswith("notes/trash/")
    assert paths["note-000003"].startswith("notes/recovery/")


def test_trashed_note_referencing_active_folder_uses_trash_root():
    folder = _folder("folder-000001", "Projects", lifecycle=LIFECYCLE_ACTIVE)
    note = _note("note-000001", "Filed", folder_id="folder-000001", lifecycle=LIFECYCLE_TRASH)
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert path == "notes/trash/Projects/Filed.md"


def test_recovery_note_referencing_active_folder_uses_recovery_root():
    folder = _folder("folder-000001", "Projects", lifecycle=LIFECYCLE_ACTIVE)
    note = _note("note-000001", "Filed", folder_id="folder-000001", lifecycle=LIFECYCLE_RECOVERY)
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert path == "notes/recovery/Projects/Filed.md"


def test_same_folder_appears_under_multiple_lifecycle_roots_independently():
    folder = _folder("folder-000001", "Projects", lifecycle=LIFECYCLE_ACTIVE)
    notes = [
        _note("note-000001", "Kept", folder_id="folder-000001", lifecycle=LIFECYCLE_ACTIVE),
        _note("note-000002", "Trashed", folder_id="folder-000001", lifecycle=LIFECYCLE_TRASH),
    ]
    manifest = _manifest(folders=[folder], notes=notes)
    paths = build_backup_markdown_paths(manifest)
    assert paths["note-000001"] == "notes/active/Projects/Kept.md"
    assert paths["note-000002"] == "notes/trash/Projects/Trashed.md"


def test_recovery_marker_folder_uses_its_plain_name():
    folder = _folder(
        "folder-000001", "Recovered Items", lifecycle=LIFECYCLE_ACTIVE, is_recovery_folder=True
    )
    note = _note("note-000001", "Salvaged", folder_id="folder-000001")
    manifest = _manifest(folders=[folder], notes=[note])
    path = build_backup_markdown_paths(manifest)["note-000001"]
    assert path == "notes/active/Recovered Items/Salvaged.md"
