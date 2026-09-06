"""Owner-facing Trash lifecycle presentation.

Covers the `TrashedNoteRow`/`TrashedFolderRow` projection and the
three rendered lifecycle labels (`Moved to Trash`, `Leaves your Trash`,
`Final purge`). Boundary/eligibility policy itself is already covered by
`test_trash_lifecycle.py`; this file only reconfirms that the
presentation layer doesn't change which items reach owner-visible
Trash, and adds coverage specific to the new projection and markup.
"""

import re
from datetime import timedelta
from unittest import mock

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import localize
from django.utils.timezone import template_localtime

from notes import services
from notes.models import Note

PASSWORD = "LongUniquePassword123!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _lifecycle_value(content: str, *, label: str) -> str:
    """Extracts the `<dd>` text immediately following a given `<dt>`
    label, tying the assertion to the label/value pairing itself rather
    than to the page's overall string order."""
    match = re.search(rf"<dt>{re.escape(label)}</dt>\s*<dd>([^<]*)</dd>", content)
    assert match, f"Lifecycle label {label!r} not found in rendered Trash page"
    return match.group(1).strip()


def _expected_rendered_datetime(value) -> str:
    """RidgeNote's existing, established convention: plain `{{ value }}`
    interpolation of a timezone-aware datetime, which Django's template
    engine converts to the active timezone (`template_localtime`) and
    then formats using the active locale (`localize`) -- not a raw
    `str()`. Used here only to compute the expected rendered string;
    production code performs no such conversion itself."""
    return localize(template_localtime(value))


# -- projection / arithmetic ---------------------------------------------------


@pytest.mark.django_db
def test_note_lifecycle_row_moved_to_trash_at_matches_trashed_at():
    owner = create_account("lifecycle-note-moved-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Lifecycle Note 1")
    trashed = services.move_note_to_trash(note=note)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows[0].moved_to_trash_at == trashed.trashed_at


@pytest.mark.django_db
def test_note_lifecycle_row_owner_visible_until_is_30_days_after_trashed_at():
    owner = create_account("lifecycle-note-visible-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Lifecycle Note 2")
    trashed = services.move_note_to_trash(note=note)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows[0].owner_visible_until == trashed.trashed_at + services.TRASH_VISIBLE_MAX_AGE
    assert rows[0].owner_visible_until == trashed.trashed_at + timedelta(days=30)


@pytest.mark.django_db
def test_note_lifecycle_row_final_purge_at_is_90_days_after_trashed_at():
    owner = create_account("lifecycle-note-purge-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Lifecycle Note 3")
    trashed = services.move_note_to_trash(note=note)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows[0].final_purge_at == trashed.trashed_at + services.TRASH_RECOVERABLE_MAX_AGE
    assert rows[0].final_purge_at == trashed.trashed_at + timedelta(days=90)


@pytest.mark.django_db
def test_folder_lifecycle_row_uses_identical_formula_to_note():
    owner = create_account("lifecycle-folder-formula-owner")
    folder = services.create_folder(owner=owner, name="Formula Folder")
    trashed = services.move_folder_to_trash(folder=folder)

    rows = services.list_trashed_folders_with_lifecycle_for_owner(owner=owner)

    assert rows[0].moved_to_trash_at == trashed.trashed_at
    assert rows[0].owner_visible_until == trashed.trashed_at + timedelta(days=30)
    assert rows[0].final_purge_at == trashed.trashed_at + timedelta(days=90)


@pytest.mark.django_db
def test_lifecycle_row_values_are_timezone_aware():
    owner = create_account("lifecycle-tz-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)
    row = rows[0]

    assert timezone.is_aware(row.moved_to_trash_at)
    assert timezone.is_aware(row.owner_visible_until)
    assert timezone.is_aware(row.final_purge_at)


@pytest.mark.django_db
def test_lifecycle_row_matches_administrator_recovery_arithmetic_for_same_trashed_at():
    """The owner-facing projection must reuse, not reinvent, the exact
    arithmetic already proven by the administrator recovery projection."""
    owner = create_account("lifecycle-parity-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)
    # Age the note into the administrator-only stage so it's covered by
    # `list_administrator_recoverable_notes` for direct comparison.
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=31))
    note.refresh_from_db()

    admin_rows = services.list_administrator_recoverable_notes()
    admin_row = next(r for r in admin_rows if r["id"] == note.id)

    assert admin_row["owner_visible_expiration_at"] == note.trashed_at + timedelta(days=30)
    assert admin_row["final_purge_at"] == note.trashed_at + timedelta(days=90)
    # Same underlying constants power both projections.
    assert services.TRASH_VISIBLE_MAX_AGE == timedelta(days=30)
    assert services.TRASH_RECOVERABLE_MAX_AGE == timedelta(days=90)


# -- eligibility (reconfirmed, not new policy) ----------------------------------


@pytest.mark.django_db
def test_active_note_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-active-note-owner")
    services.create_note(owner=owner)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows == []


@pytest.mark.django_db
def test_active_folder_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-active-folder-owner")
    services.create_folder(owner=owner, name="Active Folder")

    rows = services.list_trashed_folders_with_lifecycle_for_owner(owner=owner)

    assert rows == []


@pytest.mark.django_db
def test_day_30_note_included_in_lifecycle_rows():
    owner = create_account("lifecycle-day30-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 3")
    services.move_note_to_trash(note=note)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=30))

    # Freezes "now" for the eligibility query itself so this doesn't race
    # against the exact day-30 boundary it's testing (matching the existing
    # `test_trash_lifecycle.py` convention for exact-boundary assertions).
    with mock.patch("notes.services.timezone.now", return_value=now):
        rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert len(rows) == 1
    assert rows[0].note.id == note.id


@pytest.mark.django_db
def test_day_31_note_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-day31-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 4")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - timedelta(days=30, seconds=1)
    )

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows == []


@pytest.mark.django_db
def test_other_owners_note_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-scope-owner")
    other = create_account("lifecycle-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Other Note 5")
    services.move_note_to_trash(note=other_note)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows == []


@pytest.mark.django_db
def test_other_owners_folder_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-scope-folder-owner")
    other = create_account("lifecycle-scope-folder-other")
    other_folder = services.create_folder(owner=other, name="Someone Else's Folder")
    services.move_folder_to_trash(folder=other_folder)

    rows = services.list_trashed_folders_with_lifecycle_for_owner(owner=owner)

    assert rows == []


@pytest.mark.django_db
def test_emptied_note_excluded_from_lifecycle_rows():
    owner = create_account("lifecycle-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 6")
    services.move_note_to_trash(note=note)
    services.empty_trash_for_owner(owner=owner)

    rows = services.list_trashed_notes_with_lifecycle_for_owner(owner=owner)

    assert rows == []


# -- rendering -------------------------------------------------------------------


@pytest.mark.django_db
def test_trash_page_shows_three_lifecycle_labels_for_note():
    owner = create_account("render-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Render Note")
    trashed = services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "<dt>Moved to Trash</dt>" in content
    assert "<dt>Leaves your Trash</dt>" in content
    assert "<dt>Final purge</dt>" in content

    moved = _lifecycle_value(content, label="Moved to Trash")
    leaves = _lifecycle_value(content, label="Leaves your Trash")
    purge = _lifecycle_value(content, label="Final purge")

    assert moved == _expected_rendered_datetime(trashed.trashed_at)
    assert leaves == _expected_rendered_datetime(trashed.trashed_at + timedelta(days=30))
    assert purge == _expected_rendered_datetime(trashed.trashed_at + timedelta(days=90))


@pytest.mark.django_db
def test_trash_page_shows_three_lifecycle_labels_for_folder():
    owner = create_account("render-folder-owner")
    folder = services.create_folder(owner=owner, name="Render Folder")
    trashed = services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "<dt>Moved to Trash</dt>" in content
    assert "<dt>Leaves your Trash</dt>" in content
    assert "<dt>Final purge</dt>" in content

    moved = _lifecycle_value(content, label="Moved to Trash")
    leaves = _lifecycle_value(content, label="Leaves your Trash")
    purge = _lifecycle_value(content, label="Final purge")

    assert moved == _expected_rendered_datetime(trashed.trashed_at)
    assert leaves == _expected_rendered_datetime(trashed.trashed_at + timedelta(days=30))
    assert purge == _expected_rendered_datetime(trashed.trashed_at + timedelta(days=90))


@pytest.mark.django_db
def test_note_and_folder_rows_share_identical_lifecycle_label_set():
    owner = create_account("render-parity-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 7")
    services.move_note_to_trash(note=note)
    folder = services.create_folder(owner=owner, name="Parity Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert content.count("<dt>Moved to Trash</dt>") == 2
    assert content.count("<dt>Leaves your Trash</dt>") == 2
    assert content.count("<dt>Final purge</dt>") == 2


@pytest.mark.django_db
def test_final_purge_uses_the_emphasis_modifier_class():
    """The emphasis treatment is applied via the existing `--emphasis`
    modifier class only -- no separate `danger`/`destructive` class name
    exists for this field's warning/destructive *color*; only the CSS
    custom properties the existing modifier resolves to changed, not the
    class name or the markup structure."""
    owner = create_account("render-emphasis-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 8")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    match = re.search(r'<div class="([^"]*trash-item__lifecycle-entry--emphasis[^"]*)">', content)
    assert match, "Final purge lifecycle entry not found"
    emphasis_class = match.group(1)
    assert "trash-item__lifecycle-entry--emphasis" in emphasis_class
    assert "danger" not in emphasis_class.lower()
    assert "destructive" not in emphasis_class.lower()


@pytest.mark.django_db
def test_empty_trash_shows_no_lifecycle_metadata():
    owner = create_account("render-empty-owner")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Trash is empty" in content
    assert "<dt>Moved to Trash</dt>" not in content
    assert "trash-item__lifecycle" not in content


@pytest.mark.django_db
def test_notes_only_trash_renders_only_note_rows():
    owner = create_account("render-notes-only-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Solo Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Solo Note" in content
    assert "<h2>Folders</h2>" not in content
    assert content.count("trash-item__lifecycle-entry--emphasis") == 1


@pytest.mark.django_db
def test_folders_only_trash_renders_only_folder_rows():
    owner = create_account("render-folders-only-owner")
    folder = services.create_folder(owner=owner, name="Solo Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Solo Folder" in content
    assert "<h2>Notes</h2>" not in content
    assert content.count("trash-item__lifecycle-entry--emphasis") == 1


@pytest.mark.django_db
def test_mixed_notes_and_folders_render_both_lifecycle_groups():
    owner = create_account("render-mixed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 9")
    services.move_note_to_trash(note=note)
    folder = services.create_folder(owner=owner, name="Mixed Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert content.count("trash-item__lifecycle-entry--emphasis") == 2


@pytest.mark.django_db
def test_long_note_title_still_renders_lifecycle_metadata():
    owner = create_account("render-long-title-owner")
    note = services.create_note(owner=owner)
    long_title = "A very long note title " * 10
    services.rename_note(note=note, title=long_title.strip())
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "<dt>Final purge</dt>" in content


@pytest.mark.django_db
def test_long_folder_name_still_renders_lifecycle_metadata():
    owner = create_account("render-long-folder-owner")
    long_name = "A" * 120
    folder = services.create_folder(owner=owner, name=long_name)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "<dt>Final purge</dt>" in content


@pytest.mark.django_db
def test_trash_page_has_per_note_permanent_delete_control_not_other_phrasing():
    # "Delete permanently" is the real, present
    # control for a trashed Note -- only alternate phrasings never used by
    # this project remain absent.
    owner = create_account("render-purge-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 10")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Delete permanently" in content
    assert "Permanently delete" not in content
    assert "Permanent delete" not in content
    assert "Delete now" not in content


def test_trash_template_does_not_reference_unattributed_trash_icons():
    """Static check on the template source itself, not rendered output --
    `trash-empty.svg`/`trash-full.svg` are unused and unattributed;
    this must remain true regardless
    of what's currently trashed for any given render."""
    with open("notes/templates/notes/trash.html", encoding="utf-8") as handle:
        source = handle.read()

    assert "trash-empty.svg" not in source
    assert "trash-full.svg" not in source


# -- existing behavior unchanged --------------------------------------------------


@pytest.mark.django_db
def test_note_restore_still_redirects_to_trash_and_clears_trashed_at():
    owner = create_account("regress-note-restore-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 11")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert note.trashed_at is None


@pytest.mark.django_db
def test_folder_restore_still_redirects_to_trash_and_clears_trashed_at():
    owner = create_account("regress-folder-restore-owner")
    folder = services.create_folder(owner=owner, name="Regress Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_empty_trash_still_redirects_and_stamps_emptied_at_only():
    owner = create_account("regress-empty-trash-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 12")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert note.emptied_at is not None
    assert note.trashed_at is not None


# -- query-count / performance -----------------------------------------------------


@pytest.mark.django_db
def test_trash_page_lifecycle_projection_adds_no_per_row_queries():
    owner = create_account("query-count-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 13")
    services.move_note_to_trash(note=note)
    folder = services.create_folder(owner=owner, name="Query Folder")
    services.move_folder_to_trash(folder=folder)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as baseline:
        response = client.get(reverse("notes:trash"))
    assert response.status_code == 200
    baseline_count = len(baseline.captured_queries)

    for i in range(8):
        extra_note = services.create_note(owner=owner)
        services.rename_note(note=extra_note, title="Extra Note 14")
        services.move_note_to_trash(note=extra_note)
        extra_folder = services.create_folder(owner=owner, name=f"Query Folder {i}")
        services.move_folder_to_trash(folder=extra_folder)

    with CaptureQueriesContext(connection) as scaled:
        response = client.get(reverse("notes:trash"))
    assert response.status_code == 200
    scaled_count = len(scaled.captured_queries)

    assert scaled_count == baseline_count
