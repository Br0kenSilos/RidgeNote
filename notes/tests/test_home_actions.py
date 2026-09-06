"""Home Recent action controls.

Home's Recent Notes rows are not read-only: they carry the exact
same Pin/Unpin, Move, and overflow-menu (Open/Open in new tab/Rename/
Print/Download/Duplicate/Delete) controls All Notes rows do, rendered by
the same shared `_all_notes_row.html` partial with `context="home"`.

No new route, view, or Home-specific action logic was added -- every
action posts to a route that already existed and already redirected to
`home` on its own, exactly as the tree's own per-row menu has always
done in Home context: `note_pin_set` (bare), `note_move_home`,
`note_rename_home`, and the bare `note_delete` with
`data-delete-origin="home"`. All Notes' own equivalent tests (`test_all_notes.py`) are
unaffected and remain the source of truth that All Notes behavior is
unchanged.
"""

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services

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


def _recent_section(content: str) -> str:
    recent_start = content.index('class="home-recent"')
    return content[recent_start : content.index('id="workspace-drawer"')]


def _all_notes_panel_section(content: str) -> str:
    start = content.index('class="all-notes-panel"')
    return content[start : content.index('id="workspace-drawer"')]


def _note_item_section(content: str, note_id: int) -> str:
    marker = f'data-note-id="{note_id}"'
    marker_index = content.index(marker)
    start = content.rindex("<li", 0, marker_index)
    end = content.index("</li>", start)
    return content[start:end]


# -- Pin ------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_pin_uses_the_bare_route_and_returns_to_home():
    owner = create_account("home-pin-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_home_unpin_toggles_state_and_returns_to_home():
    owner = create_account("home-unpin-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "false"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_home_row_pin_form_targets_the_bare_pin_route():
    owner = create_account("home-pin-form-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pin Form Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    expected_action = reverse("notes:note_pin_set", args=[note.id])
    assert f'action="{expected_action}"' in row
    assert "csrfmiddlewaretoken" in row
    # No page/sort hidden fields -- those only apply to the All Notes
    # variant, which this row deliberately does not post to.
    assert 'name="page"' not in row
    assert 'name="sort"' not in row


@pytest.mark.django_db
def test_pinning_from_home_promotes_a_note_into_the_pinned_group():
    # Ordering is pinned-first, then most-recently-modified, not
    # strictly `-modified_at, -id` regardless of pin state. Pinning an older
    # note via Home's own pin control must promote it above a newer
    # unpinned note.
    owner = create_account("home-pin-promotion-owner")
    older = services.create_note(owner=owner)
    services.rename_note(note=older, title="Older Unpinned")
    newer = services.create_note(owner=owner)
    services.rename_note(note=newer, title="Newer To Pin")
    from notes.models import Note as NoteModel

    NoteModel.objects.filter(pk=older.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    NoteModel.objects.filter(pk=newer.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    client = authenticated_client(owner)
    before = client.get(reverse("home")).content.decode()
    before_section = _recent_section(before)
    assert before_section.index(">Newer To Pin<") < before_section.index(">Older Unpinned<")

    client.post(reverse("notes:note_pin_set", args=[older.id]), {"pinned": "true"})

    after = client.get(reverse("home")).content.decode()
    after_section = _recent_section(after)
    # The newly-pinned-but-older note now sorts first, ahead of the
    # newer unpinned note.
    assert after_section.index(">Older Unpinned<") < after_section.index(">Newer To Pin<")


# -- Move -----------------------------------------------------------------------


@pytest.mark.django_db
def test_home_move_uses_the_home_route_and_returns_to_home():
    owner = create_account("home-move-owner")
    folder = services.create_folder(owner=owner, name="Home Move Target")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": folder.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_home_row_move_form_targets_the_home_move_route():
    owner = create_account("home-move-form-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Move Form Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    expected_action = reverse("notes:note_move_home", args=[note.id])
    assert f'action="{expected_action}"' in row
    assert 'name="page"' not in row
    assert 'name="sort"' not in row


# -- Overflow menu: Rename, Print, Download, Duplicate, Move to Trash ----------


@pytest.mark.django_db
def test_home_row_has_all_seven_overflow_actions():
    owner = create_account("home-overflow-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Overflow Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    assert ">Open<" in row
    assert ">Open in new tab<" in row
    assert ">Rename<" in row
    assert ">Print<" in row
    assert ">Download<" in row
    assert ">Duplicate<" in row
    assert ">Move to Trash<" in row


@pytest.mark.django_db
def test_home_rename_uses_the_home_route_and_returns_to_home():
    owner = create_account("home-rename-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "Renamed From Home"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.title == "Renamed From Home"


@pytest.mark.django_db
def test_home_row_rename_form_targets_the_home_rename_route():
    owner = create_account("home-rename-form-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Rename Form Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    expected_action = reverse("notes:note_rename_home", args=[note.id])
    assert f'action="{expected_action}"' in row


@pytest.mark.django_db
def test_home_delete_uses_the_bare_route_with_home_origin_and_returns_to_home():
    owner = create_account("home-delete-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Delete From Home Note")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "home"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_home_row_delete_trigger_targets_the_bare_delete_route_with_home_origin():
    owner = create_account("home-delete-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Delete Trigger Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    expected_action = reverse("notes:note_delete", args=[note.id])
    assert f'data-delete-action="{expected_action}"' in row
    assert 'data-delete-origin="home"' in row
    # Page/sort attributes are All-Notes-only.
    assert "data-delete-page" not in row
    assert "data-delete-sort" not in row


@pytest.mark.django_db
def test_home_duplicate_and_print_and_download_links_unaffected_by_context():
    owner = create_account("home-duplicate-print-download-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Context Agnostic Note")

    response = authenticated_client(owner).get(reverse("home"))
    row = _note_item_section(_recent_section(response.content.decode()), note.id)

    assert f'action="{reverse("notes:note_duplicate", args=[note.id])}"' in row
    assert f'href="{reverse("notes:print", args=[note.id])}"' in row
    assert f'href="{reverse("notes:download_text", args=[note.id])}"' in row


# -- Shared markup, not a Home-specific duplicate -------------------------------


@pytest.mark.django_db
def test_home_and_all_notes_actions_use_the_identical_shared_markup_shape():
    owner = create_account("home-shared-actions-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Shared Actions Shape Note")

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    for shared_class in (
        'class="note-list__actions"',
        "note-list__pin-toggle",
        "note-list__move-action",
        "note-list__row-menu",
        'class="tree-nav__row-menu-panel"',
    ):
        assert shared_class in home_row, shared_class
        assert shared_class in all_notes_row, shared_class

    # Actions region is the last top-level child of `.note-list__row` on
    # both surfaces, right after content -- title length cannot displace
    # it on either.
    for row in (home_row, all_notes_row):
        content_index = row.index('class="note-list__content"')
        actions_index = row.index('class="note-list__actions"')
        assert content_index < actions_index


@pytest.mark.django_db
def test_all_notes_pin_move_rename_delete_routes_are_unchanged_by_this_pass():
    # Direct regression guard: All Notes' own rows must keep posting to
    # its own dedicated `_all_notes` routes, never the Home-context ones.
    owner = create_account("all-notes-unaffected-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="All Notes Unaffected Note")

    response = authenticated_client(owner).get(reverse("notes:all_notes"), follow=True)
    content = response.content.decode()
    row_start = content.index(f'<li class="note-list__item" data-note-id="{note.id}"')
    row_end = content.index("</li>", row_start)
    row = content[row_start:row_end]

    assert f'action="{reverse("notes:note_pin_set_all_notes", args=[note.id])}"' in row
    assert f'action="{reverse("notes:note_move_all_notes", args=[note.id])}"' in row
    assert f'action="{reverse("notes:note_rename_all_notes", args=[note.id])}"' in row
    assert f'data-delete-action="{reverse("notes:note_delete_all_notes", args=[note.id])}"' in row
    assert 'name="page"' in row
    assert 'name="sort"' in row
