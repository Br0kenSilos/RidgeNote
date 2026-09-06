"""Delete confirmation and post-delete navigation.

Covers the shared `#delete-confirm-dialog` markup (`core/templates/base.html`)
and every existing Delete/"Move to Trash" trigger's `data-delete-*`
attributes across note-detail (wide tree + narrow drawer), Home, All Notes
(both its own tree copy and its own row list), and Trash -- confirming each
carries the correct kind/name/action/origin/current-note/page/sort for the
dialog controller (`delete-confirm.ts`, covered independently in Vitest) to
read. Post-delete navigation (including the adjacent-note fallback) is
covered in `test_delete_return_state.py`; this file is presence/attribute
coverage only.
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


# -- Shared dialog markup ---------------------------------------------------


@pytest.mark.django_db
def test_delete_confirm_dialog_markup_present_with_csrf_and_controls():
    owner = create_account("dialog-markup-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert '<dialog id="delete-confirm-dialog"' in content
    assert "data-delete-confirm-form" in content
    assert "data-delete-confirm-title" in content
    assert "data-delete-confirm-consequence" in content
    assert "data-delete-confirm-cancel" in content
    assert 'data-delete-confirm-field="origin"' in content
    assert 'data-delete-confirm-field="current_note"' in content
    assert 'data-delete-confirm-field="page"' in content
    assert 'data-delete-confirm-field="sort"' in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_delete_confirm_dialog_present_on_every_page_that_can_delete():
    owner = create_account("dialog-everywhere-owner")
    note = services.create_note(owner=owner)
    client = authenticated_client(owner)

    for url in (
        reverse("home"),
        reverse("notes:all_notes"),
        reverse("notes:detail", args=[note.id]),
        reverse("notes:trash"),
    ):
        content = client.get(url).content.decode()
        assert 'id="delete-confirm-dialog"' in content, url


# -- Note-detail tree: both wide and narrow-drawer copies --------------------


@pytest.mark.django_db
def test_note_detail_current_note_row_delete_trigger_attributes():
    owner = create_account("nd-current-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Current Note")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    action = reverse("notes:note_delete", args=[note.id])
    # Present in both the wide tree and the narrow drawer copy.
    assert content.count("data-delete-trigger") >= 2
    assert content.count(f'data-delete-action="{action}"') == 2
    assert content.count('data-delete-kind="note"') >= 2
    assert content.count('data-delete-name="Current Note"') == 2
    assert content.count('data-delete-origin="note_detail"') >= 2
    assert content.count(f'data-delete-current-note="{note.id}"') >= 2


@pytest.mark.django_db
def test_note_detail_folder_row_delete_trigger_attributes():
    owner = create_account("nd-folder-trigger-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    action = reverse("notes:folder_delete", args=[note.id, folder.id])
    assert content.count(f'data-delete-action="{action}"') == 2
    assert content.count('data-delete-kind="folder"') == 2
    assert content.count('data-delete-name="Projects"') == 2
    # The note itself lives inside `folder` and is also the page's own
    # current note, so its own row-menu Delete trigger independently
    # contributes `data-delete-origin="note_detail"`/
    # `data-delete-current-note` too -- scope this check to each folder
    # trigger's own attribute list specifically, not a whole-page count.
    for button_html in content.split('data-delete-kind="folder"')[1:3]:
        attrs = button_html.split(">Move to Trash<")[0]
        assert 'data-delete-origin="note_detail"' in attrs
        assert f'data-delete-current-note="{note.id}"' in attrs


@pytest.mark.django_db
def test_note_detail_unfiled_note_delete_trigger_attributes():
    owner = create_account("nd-unfiled-trigger-owner")
    note = services.create_note(owner=owner)
    other = services.create_note(owner=owner)
    services.rename_note(note=other, title="Unfiled Sibling")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    action = reverse("notes:note_delete", args=[other.id])
    assert content.count(f'data-delete-action="{action}"') == 2
    assert content.count('data-delete-name="Unfiled Sibling"') == 2


# -- Home tree ----------------------------------------------------------------


@pytest.mark.django_db
def test_home_tree_note_delete_trigger_has_home_origin_and_no_current_note():
    owner = create_account("home-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Home Note")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    action = reverse("notes:note_delete", args=[note.id])
    assert f'data-delete-action="{action}"' in content
    assert 'data-delete-origin="home"' in content
    # Home has no single current note at all (`note` is always `None` in
    # its own render context) -- no trigger anywhere on the page should
    # carry a `data-delete-current-note` attribute.
    assert "data-delete-current-note" not in content


@pytest.mark.django_db
def test_home_tree_folder_delete_trigger_has_home_origin():
    owner = create_account("home-folder-trigger-owner")
    folder = services.create_folder(owner=owner, name="Ideas")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    action = reverse("notes:folder_delete_home", args=[folder.id])
    assert f'data-delete-action="{action}"' in content
    assert 'data-delete-origin="home"' in content
    assert 'data-delete-kind="folder"' in content
    assert 'data-delete-name="Ideas"' in content


# -- All Notes: both its own tree copy and its own row list -------------------


@pytest.mark.django_db
def test_all_notes_tree_note_delete_trigger_has_all_notes_origin_page_and_sort():
    owner = create_account("an-tree-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="All Notes Tree Note")

    response = authenticated_client(owner).get(
        reverse("notes:all_notes"), {"page": "1", "sort": "title_asc"}
    )
    content = response.content.decode()

    action = reverse("notes:note_delete", args=[note.id])
    assert f'data-delete-action="{action}"' in content
    assert 'data-delete-origin="all_notes"' in content
    assert 'data-delete-page="1"' in content
    assert 'data-delete-sort="title_asc"' in content


@pytest.mark.django_db
def test_all_notes_row_list_delete_trigger_attributes():
    owner = create_account("an-row-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Row List Note")

    response = authenticated_client(owner).get(
        reverse("notes:all_notes"), {"page": "1", "sort": "title_asc"}
    )
    content = response.content.decode()

    action = reverse("notes:note_delete_all_notes", args=[note.id])
    assert f'data-delete-action="{action}"' in content
    assert 'data-delete-kind="note"' in content
    assert 'data-delete-name="Row List Note"' in content
    assert 'data-delete-page="1"' in content
    assert 'data-delete-sort="title_asc"' in content


# -- Trash page's own tree copy ------------------------------------------------


@pytest.mark.django_db
def test_trash_page_tree_note_delete_trigger_present():
    owner = create_account("trash-tree-trigger-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Still Active Note")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    action = reverse("notes:note_delete", args=[note.id])
    assert f'data-delete-action="{action}"' in content
