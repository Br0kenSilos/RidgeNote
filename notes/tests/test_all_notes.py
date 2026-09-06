import pathlib

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Note
from notes.views import ALL_NOTES_PAGE_SIZE, ALL_NOTES_SORT_SESSION_KEY

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


def _panel_section(content: str) -> str:
    start = content.index('class="all-notes-panel"')
    return content[start : content.index('id="workspace-drawer"')]


# -- Route, authentication, eligibility --------------------------------------


def test_all_notes_requires_login():
    response = Client().get(reverse("notes:all_notes"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_all_notes_renders_with_correct_template():
    owner = create_account("route-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))

    assert response.status_code == 200
    assert "notes/all_notes.html" in [t.name for t in response.templates]
    assert ">All notes<" in response.content.decode()


@pytest.mark.django_db
def test_all_notes_is_owner_scoped():
    owner = create_account("scope-owner")
    other = create_account("scope-other-owner")
    services.rename_note(note=services.create_note(owner=owner), title="Mine")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Not mine")

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "Mine" in content
    assert "Not mine" not in content


@pytest.mark.django_db
def test_all_notes_excludes_trashed_notes():
    owner = create_account("trash-exclude-owner")
    active = services.create_note(owner=owner)
    services.rename_note(note=active, title="Active note")
    trashed = services.create_note(owner=owner)
    services.rename_note(note=trashed, title="Trashed note")
    services.move_note_to_trash(note=trashed)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "Active note" in content
    assert "Trashed note" not in content


@pytest.mark.django_db
def test_all_notes_excludes_emptied_notes():
    owner = create_account("empty-exclude-owner")
    active = services.create_note(owner=owner)
    services.rename_note(note=active, title="Still active")
    emptied = services.create_note(owner=owner)
    services.rename_note(note=emptied, title="Emptied note")
    services.move_note_to_trash(note=emptied)
    services.empty_trash_for_owner(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "Still active" in content
    assert "Emptied note" not in content


# -- Pagination ---------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_page_one_contains_at_most_fifty():
    owner = create_account("page-one-owner")
    for i in range(60):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i:03d}")

    response = authenticated_client(owner).get(reverse("notes:all_notes"))

    assert response.context["page_obj"].paginator.count == 60
    assert len(response.context["page_obj"].object_list) == ALL_NOTES_PAGE_SIZE == 50


@pytest.mark.django_db
def test_all_notes_fifty_first_note_appears_on_page_two():
    owner = create_account("page-two-owner")
    for i in range(51):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i:03d}")

    response = authenticated_client(owner).get(
        reverse("notes:all_notes"), {"page": 2, "sort": "title_asc"}
    )

    assert response.context["page_obj"].number == 2
    titles = [n.title for n in response.context["page_obj"].object_list]
    assert titles == ["Note 050"]


@pytest.mark.django_db
def test_all_notes_malformed_page_falls_back_to_page_one():
    owner = create_account("malformed-page-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"page": "not-a-number"})

    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_zero_page_falls_back_to_page_one():
    owner = create_account("zero-page-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"page": "0"})

    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_negative_page_falls_back_to_page_one():
    owner = create_account("negative-page-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"page": "-3"})

    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_out_of_range_page_clamps_to_last_page():
    owner = create_account("out-of-range-owner")
    for _i in range(10):
        services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"page": "999"})

    assert response.status_code == 200
    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_out_of_range_page_never_returns_404():
    owner = create_account("no-404-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"page": "999"})

    assert response.status_code == 200


@pytest.mark.django_db
def test_all_notes_one_page_result_renders_no_pagination_controls():
    owner = create_account("one-page-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "all-notes-pagination" not in content


@pytest.mark.django_db
def test_all_notes_multi_page_renders_pagination_controls():
    owner = create_account("multi-page-owner")
    for _i in range(51):
        services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "all-notes-pagination" in content
    assert "Page 1 of 2" in content


@pytest.mark.django_db
def test_all_notes_pagination_links_preserve_sort():
    owner = create_account("pagination-sort-owner")
    for _i in range(51):
        services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"sort": "title_asc"})
    content = response.content.decode()

    assert "sort=title_asc" in content


# -- Sorting --------------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_default_sort_promotes_pinned_notes():
    owner = create_account("default-sort-owner")
    older_pinned = services.create_note(owner=owner)
    newer_unpinned = services.create_note(owner=owner)
    services.set_note_pinned(note=older_pinned, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    order = [n.id for n in response.context["page_obj"].object_list]

    assert order.index(older_pinned.id) < order.index(newer_unpinned.id)


@pytest.mark.django_db
def test_all_notes_explicit_non_default_sort_does_not_promote_pinned_notes():
    owner = create_account("no-promote-owner")
    pinned_zeta = services.create_note(owner=owner)
    services.rename_note(note=pinned_zeta, title="Zeta")
    services.set_note_pinned(note=pinned_zeta, pinned=True)
    unpinned_alpha = services.create_note(owner=owner)
    services.rename_note(note=unpinned_alpha, title="Alpha")

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"sort": "title_asc"})
    order = [n.id for n in response.context["page_obj"].object_list]

    assert order.index(unpinned_alpha.id) < order.index(pinned_zeta.id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "sort_value",
    [
        "pinned_new",
        "pinned_old",
        "title_asc",
        "title_desc",
        "created_new",
        "created_old",
        "modified_new",
        "modified_old",
    ],
)
def test_all_notes_accepts_every_sort_mode(sort_value):
    owner = create_account(f"sort-mode-owner-{sort_value}")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"), {"sort": sort_value})

    assert response.status_code == 200
    assert response.context["sort"] == sort_value


@pytest.mark.django_db
def test_all_notes_sort_change_resets_to_page_one():
    owner = create_account("sort-reset-owner")
    for _i in range(51):
        services.create_note(owner=owner)

    client = authenticated_client(owner)
    client.get(reverse("notes:all_notes"), {"page": 2})
    response = client.get(reverse("notes:all_notes"), {"sort": "title_asc"})

    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_uses_dedicated_session_key():
    owner = create_account("session-key-owner")
    services.create_note(owner=owner)

    client = authenticated_client(owner)
    client.get(reverse("notes:all_notes"), {"sort": "title_asc"})

    assert client.session[ALL_NOTES_SORT_SESSION_KEY] == "title_asc"


@pytest.mark.django_db
def test_home_does_not_read_or_mutate_all_notes_session_key():
    owner = create_account("home-no-mutate-owner")
    services.create_note(owner=owner)

    client = authenticated_client(owner)
    client.get(reverse("notes:all_notes"), {"sort": "title_asc"})
    client.get(reverse("home"), {"sort": "modified_old"})

    # Home's own GET must not have overwritten the All Notes sort state.
    assert client.session[ALL_NOTES_SORT_SESSION_KEY] == "title_asc"


# -- Row content ------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_row_shows_folder_location():
    owner = create_account("folder-row-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "in Projects" in content


@pytest.mark.django_db
def test_all_notes_row_shows_unfiled():
    owner = create_account("unfiled-row-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "Unfiled" in content


@pytest.mark.django_db
def test_all_notes_row_shows_read_only_tags():
    owner = create_account("tag-row-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "note-list__tag-chip" in content
    assert "Work" in content
    assert "note_tag_remove" not in content


@pytest.mark.django_db
def test_all_notes_row_has_direct_pin_and_move_controls():
    owner = create_account("direct-actions-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "note-list__pin-toggle" in content
    assert "note-list__move-action" in content


@pytest.mark.django_db
def test_all_notes_row_move_appears_exactly_once():
    owner = create_account("one-move-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()
    row_start = content.index(f'<li class="note-list__item" data-note-id="{note.id}"')
    row_end = content.index("</li>", row_start)
    row = content[row_start:row_end]

    assert row.count("note-list__move-action") == 1
    move_url = reverse("notes:note_move_all_notes", args=[note.id])
    assert row.count(move_url) == 1


@pytest.mark.django_db
def test_all_notes_row_has_all_seven_overflow_actions():
    owner = create_account("overflow-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()
    row_start = content.index(f'<li class="note-list__item" data-note-id="{note.id}"')
    row_end = content.index("</li>", row_start)
    row = content[row_start:row_end]

    assert ">Open<" in row
    assert ">Open in new tab<" in row
    assert ">Rename<" in row
    assert ">Print<" in row
    assert ">Download<" in row
    assert ">Duplicate<" in row
    assert ">Move to Trash<" in row


@pytest.mark.django_db
def test_all_notes_row_print_uses_the_new_tab_fallback():
    # One of the four Print entry points -- this
    # row menu is a plain link (not editor-integrated/save-gated), so
    # `target="_blank"` is its whole new-tab contract, consistent with
    # the other three.
    owner = create_account("all-notes-print-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()
    print_url = reverse("notes:print", args=[note.id])

    expected = f'href="{print_url}" target="_blank" rel="noopener">Print</a>'
    assert expected in content


@pytest.mark.django_db
def test_all_notes_pin_accessibility_contract():
    owner = create_account("pin-a11y-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()
    row_start = content.index(f'<li class="note-list__item" data-note-id="{note.id}"')
    row_end = content.index("</li>", row_start)
    row = content[row_start:row_end]

    assert 'aria-pressed="true"' in row
    assert 'aria-label="Unpin note"' in row
    assert "title=" not in row.split("note-list__pin-toggle")[1].split(">")[0]


# -- Return-state actions: Pin, Move, Rename -----------------------------------


@pytest.mark.django_db
def test_all_notes_pin_returns_to_same_page_and_sort():
    owner = create_account("pin-return-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set_all_notes", args=[note.id]),
        {"pinned": "true", "page": "1", "sort": "title_asc"},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('notes:all_notes')}?page=1&sort=title_asc"
    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_all_notes_pin_malformed_state_normalizes_safely():
    owner = create_account("pin-malformed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set_all_notes", args=[note.id]),
        {"pinned": "true", "page": "bogus", "sort": "not-a-real-sort"},
    )

    assert response.url == f"{reverse('notes:all_notes')}?page=1&sort=pinned_new"


@pytest.mark.django_db
def test_all_notes_pin_recoverable_failure_returns_to_all_notes():
    owner = create_account("pin-fail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set_all_notes", args=[note.id]),
        {"pinned": "invalid-value", "page": "1", "sort": "pinned_new"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("notes:all_notes"))


@pytest.mark.django_db
def test_all_notes_pin_cross_owner_is_404():
    owner = create_account("pin-owner-a")
    other = create_account("pin-owner-b")
    note = services.create_note(owner=other)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set_all_notes", args=[note.id]),
        {"pinned": "true", "page": "1", "sort": "pinned_new"},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_all_notes_move_returns_to_same_page_and_sort():
    owner = create_account("move-return-owner")
    folder = services.create_folder(owner=owner, name="Target")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move_all_notes", args=[note.id]),
        {"folder": folder.id, "page": "2", "sort": "modified_old"},
    )

    assert response.url == f"{reverse('notes:all_notes')}?page=2&sort=modified_old"
    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_all_notes_move_recoverable_failure_returns_to_all_notes():
    owner = create_account("move-fail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move_all_notes", args=[note.id]),
        {"folder": "not-a-valid-id", "page": "1", "sort": "pinned_new"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("notes:all_notes"))


@pytest.mark.django_db
def test_all_notes_rename_returns_to_same_page_and_sort():
    owner = create_account("rename-return-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_all_notes", args=[note.id]),
        {"title": "Renamed via All Notes", "page": "1", "sort": "title_desc"},
    )

    assert response.url == f"{reverse('notes:all_notes')}?page=1&sort=title_desc"
    note.refresh_from_db()
    assert note.title == "Renamed via All Notes"


@pytest.mark.django_db
def test_all_notes_rename_recoverable_failure_returns_to_all_notes():
    owner = create_account("rename-fail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_all_notes", args=[note.id]),
        {"title": "   ", "page": "1", "sort": "pinned_new"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("notes:all_notes"))


# -- Delete ---------------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_delete_get_is_rejected():
    # Confirmation happens via the in-context dialog
    # (`delete-confirm.ts`), so this route is POST-only -- there is no
    # GET-rendered confirmation page to check hidden fields on.
    owner = create_account("delete-confirm-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_delete_all_notes", args=[note.id]), {"page": "2", "sort": "title_asc"}
    )
    note.refresh_from_db()

    assert response.status_code == 405
    assert note.trashed_at is None


@pytest.mark.django_db
def test_all_notes_delete_success_returns_to_all_notes_with_validated_state():
    owner = create_account("delete-success-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="All Notes Delete Success")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete_all_notes", args=[note.id]),
        {"page": "1", "sort": "modified_new"},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('notes:all_notes')}?page=1&sort=modified_new"
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_all_notes_deleting_final_row_on_later_page_clamps_to_valid_page():
    owner = create_account("last-row-owner")
    notes = []
    for i in range(51):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i:03d}")
        notes.append(note)

    client = authenticated_client(owner)
    # Page 2 has exactly one note under title_asc ordering (the 51st).
    response = client.post(
        reverse("notes:note_delete_all_notes", args=[notes[-1].id]),
        {"page": "2", "sort": "title_asc"},
    )
    assert response.url == f"{reverse('notes:all_notes')}?page=2&sort=title_asc"

    follow_up = client.get(reverse("notes:all_notes"), {"page": "2", "sort": "title_asc"})
    assert follow_up.status_code == 200
    assert follow_up.context["page_obj"].number == 1


@pytest.mark.django_db
def test_all_notes_delete_ownership_failure_remains_404():
    owner = create_account("delete-owner-a")
    other = create_account("delete-owner-b")
    note = services.create_note(owner=other)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete_all_notes", args=[note.id])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_note_delete_from_tree_is_unaffected_by_all_notes_variant():
    # Existing `notes:note_delete` (used by the tree's row-menu on Home
    # and note detail) remains a distinct route from
    # `note_delete_all_notes`, with its own
    # return-state handling -- confirmed here as "no origin supplied"
    # falling back to the fixed Home destination, never Trash.
    owner = create_account("existing-delete-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_delete", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("home")


# -- Duplicate/Open/Print/Download (no return-state change) -------------------


@pytest.mark.django_db
def test_all_notes_duplicate_redirects_to_new_duplicate_detail():
    owner = create_account("duplicate-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 302
    assert response.url != reverse("notes:detail", args=[note.id])
    assert response.url.startswith("/notes/")


# -- Home integration ---------------------------------------------------------


@pytest.mark.django_db
def test_home_shows_view_all_notes_link_when_active_notes_exist():
    owner = create_account("home-link-visible-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert reverse("notes:all_notes") in content
    assert ">View all notes<" in content


@pytest.mark.django_db
def test_home_hides_view_all_notes_link_for_brand_new_user():
    owner = create_account("home-link-brand-new-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">View all notes<" not in content


@pytest.mark.django_db
def test_home_hides_view_all_notes_link_for_only_trashed_user():
    owner = create_account("home-link-only-trashed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">View all notes<" not in content


# -- Empty states ---------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_zero_notes_shows_no_notes_yet():
    owner = create_account("empty-brand-new-owner")

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "No notes yet" in content
    assert ">New Note<" in content or "New Note" in content


@pytest.mark.django_db
def test_all_notes_only_trashed_shows_no_active_notes_with_trash_link():
    owner = create_account("empty-only-trashed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "No active notes" in content
    assert reverse("notes:trash") in content
    assert "trash-module" not in content


# -- Populated-header New Note access ---------------


@pytest.mark.django_db
def test_all_notes_populated_header_renders_exactly_one_new_note_control():
    owner = create_account("populated-new-note-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    header_start = content.index('class="all-notes-panel__header"')
    header_end = content.index("</div>", header_start)
    header = content[header_start:header_end]

    assert header.count(">New Note<") == 1
    assert header.count("<form") == 2  # New Note form + sort form
    action_pos = header.index('action="' + reverse("notes:create") + '"')
    new_note_form_start = header.rindex("<form", 0, action_pos)
    new_note_form_end = header.index("</form>", new_note_form_start)
    new_note_form = header[new_note_form_start:new_note_form_end]

    assert 'method="post"' in new_note_form
    assert "csrfmiddlewaretoken" in new_note_form
    assert 'class="home-dashboard__new-note"' in new_note_form
    # No destination selector and no New Folder action.
    assert "<select" not in new_note_form
    assert "New Folder" not in header


@pytest.mark.django_db
def test_all_notes_populated_new_note_control_creates_an_unfiled_note_via_the_existing_route():
    # The header control is a plain POST form to the same `notes:create`
    # route already used by Home and the tree toolbar -- this proves that
    # exact route still creates an owner-scoped, Unfiled, `?new=1`-redirect
    # note when reached from this new entry point, with no new view.
    owner = create_account("populated-new-note-submit-owner")
    seeded = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:create"))

    assert response.status_code == 302
    assert response.url.endswith("?new=1")
    created = Note.objects.filter(owner=owner).exclude(pk=seeded.pk).get()
    assert created.folder_id is None


@pytest.mark.django_db
def test_all_notes_empty_state_still_has_exactly_one_new_note_control_no_duplicate():
    owner = create_account("empty-new-note-no-dup-owner")

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    # The header's New Note control is scoped to the populated branch
    # only, so an empty All Notes page must show exactly the one existing
    # empty-state form -- never both.
    assert content.count(">New Note<") == 1
    assert 'class="all-notes-panel__header"' in content
    header_start = content.index('class="all-notes-panel__header"')
    header_end = content.index("</div>", header_start)
    header = content[header_start:header_end]
    assert ">New Note<" not in header


@pytest.mark.django_db
def test_all_notes_one_note_renders_no_pagination():
    owner = create_account("one-note-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "all-notes-pagination" not in content


@pytest.mark.django_db
def test_all_notes_all_unfiled_renders_normally():
    owner = create_account("all-unfiled-owner")
    for _ in range(3):
        services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = _panel_section(response.content.decode())

    # Each row shows "Unfiled" both as its location text and as the
    # Move popover's own "Unfiled" folder option -- 2 occurrences per row.
    assert content.count('note-list__location">Unfiled<') == 3


# -- Persistent shell / Global Search ------------------------------------------


@pytest.mark.django_db
def test_all_notes_renders_persistent_tree_and_drawer():
    owner = create_account("shell-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert 'class="workspace-shell workspace-shell--all-notes"' in content
    assert 'class="workspace-shell__layout"' in content
    assert 'id="workspace-drawer"' in content


@pytest.mark.django_db
def test_all_notes_renders_global_search_trigger():
    owner = create_account("search-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "data-global-search-toggle" in content
    assert "global-search-panel" in content


@pytest.mark.django_db
def test_all_notes_has_no_local_search_field():
    owner = create_account("no-local-search-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert "all-notes-search" not in content


# -- Query count ----------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_query_count_bounded_with_folder_and_tag_prefetch():
    owner = create_account("query-count-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    for i in range(50):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i:03d}")
        if i % 2 == 0:
            services.assign_note_folder(note=note, folder=folder)
            services.assign_tag_to_note(note=note, tag=tag)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as fifty_notes_queries:
        client.get(reverse("notes:all_notes"))
    fifty_count = len(fifty_notes_queries.captured_queries)

    for i in range(50, 100):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i:03d}")

    with CaptureQueriesContext(connection) as hundred_notes_queries:
        client.get(reverse("notes:all_notes"))
    hundred_count = len(hundred_notes_queries.captured_queries)

    # Query count must not scale with total note count -- only with the
    # bounded 50-row page (folder via `select_related`, tags via one
    # `prefetch_related`, never one query per row).
    assert hundred_count <= fifty_count + 2


# -- Narrow header layout --------------------------


def _app_css() -> str:
    return pathlib.Path("core/static/core/css/app.css").read_text()


def test_all_notes_narrow_header_grid_rule_exists_and_is_scoped():
    # The below-640px override lives inside `@media`, so it is indented --
    # distinguishing it textually from the unindented base (wide/medium)
    # flex rule declared earlier in the file, which must keep its exact
    # prior behavior untouched.
    css = _app_css()

    assert "\n.all-notes-panel__header {\n  display: flex;" in css
    assert (
        "  .all-notes-panel__header {\n    display: grid;\n"
        "    grid-template-columns: minmax(0, 1fr) auto;\n"
        '    grid-template-areas:\n      "heading new-note"\n      "sort sort";'
    ) in css
    assert '  .all-notes-panel__heading {\n    grid-area: heading;\n  }' in css
    assert (
        "  .all-notes-panel__header .inline-form {\n    grid-area: new-note;\n  }"
    ) in css
    assert "  .all-notes-sort-form {\n    grid-area: sort;\n  }" in css

    # No Home, Trash, or Administrator Recovery selector was introduced or
    # changed by this rule -- it names only All Notes' own header classes.
    # `.home-dashboard__new-note` is the one deliberate exception: it
    # appears only as a descendant of `.all-notes-panel__header
    # .inline-form` (the narrow New Note sizing correction below), never
    # as its own bare selector, so Home's identical class keeps its
    # normal size untouched.
    narrow_rule_start = css.index("  .all-notes-panel__header {\n    display: grid;")
    narrow_rule_end = css.index("\n}\n\n", narrow_rule_start) + len("\n}\n")
    narrow_rule_block = css[narrow_rule_start:narrow_rule_end]
    for unrelated_selector in ("home-recent", "trash-item", "admin-recovery"):
        assert unrelated_selector not in narrow_rule_block
    assert "\n  .home-dashboard__new-note {" not in narrow_rule_block
    assert (
        "  .all-notes-panel__header .inline-form .home-dashboard__new-note {"
        in narrow_rule_block
    )


def test_all_notes_narrow_new_note_button_shrinks_to_apply_sizing_only_below_640px():
    # The narrow New Note button would otherwise visually
    # overwhelm the heading/sort row -- matched to Apply's own existing
    # padding/font-size tokens (not a new value) so this reuses
    # established sizing rather than inventing one, and stays scoped to
    # this one narrow-header copy so Home's own New Note button, and All
    # Notes' own medium/wide sizing, are both untouched.
    css = _app_css()

    narrow_button_rule_start = css.index(
        "  .all-notes-panel__header .inline-form .home-dashboard__new-note {"
    )
    narrow_button_rule_end = css.index("\n  }", narrow_button_rule_start)
    narrow_button_rule = css[narrow_button_rule_start:narrow_button_rule_end]

    assert "padding: var(--space-4) var(--space-8);" in narrow_button_rule
    assert "font-size: var(--font-size-control-sm);" in narrow_button_rule

    # The rule lives inside the same below-640px `@media` block as the
    # grid rules above it, not as a standalone unindented (wide/medium)
    # rule.
    assert (
        "\n  .all-notes-panel__header .inline-form .home-dashboard__new-note {"
        in css
    )
    assert (
        "\n.all-notes-panel__header .inline-form .home-dashboard__new-note {"
        not in css
    )

    # The base (unscoped) `.home-dashboard__new-note` rule -- Home's own
    # button, and this same button at medium/wide All Notes -- keeps its
    # original, larger sizing untouched.
    base_rule_start = css.index("\n.home-dashboard__new-note {") + 1
    base_rule_end = css.index("}", base_rule_start)
    base_rule = css[base_rule_start:base_rule_end]
    assert "padding: var(--space-7) var(--space-14);" in base_rule


@pytest.mark.django_db
def test_all_notes_populated_header_dom_order_is_heading_then_new_note_then_sort():
    # The CSS above places New Note visually beside the heading and the
    # sort form on its own row below purely via `grid-template-areas` --
    # it never reorders the underlying elements, so DOM/keyboard order
    # must remain exactly heading, then New Note, then sort form.
    owner = create_account("narrow-header-order-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    header_start = content.index('class="all-notes-panel__header"')
    header_end = content.index("</div>", header_start)
    header = content[header_start:header_end]

    heading_pos = header.index('id="all-notes-heading"')
    new_note_pos = header.index(">New Note<")
    sort_pos = header.index('id="all-notes-sort-select"')
    apply_pos = header.index('class="all-notes-sort-form__apply"')

    assert heading_pos < new_note_pos < sort_pos < apply_pos
