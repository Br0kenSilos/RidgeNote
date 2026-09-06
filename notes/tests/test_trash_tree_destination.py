"""Trash tree destination and quick-trash interactions.

Trash is now a fixed destination at the bottom of the wide tree, the
narrow drawer, and the collapsed tree rail (all rendered from the same
`_tree_nav.html`/`_workspace_tree_rail.html` partials, so one server-
rendered pass covers all three) -- reusing the existing `notes:trash`
route for navigation, with drag-and-drop and the note-detail quick-trash
action both submitting directly to the existing, unchanged
`move_note_to_trash()`/`move_folder_to_trash()`-backed views. The three
old top-bar/drawer duplicate Trash navigation copies were removed
together.
"""

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Folder, Note

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


def create_admin(username="admin", **kwargs):
    return create_account(username, role=User.ROLE_ADMIN, **kwargs)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


def _wide_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


# -- Trash destination presence, across wide tree / narrow drawer / rail -------


@pytest.mark.django_db
def test_trash_destination_appears_in_wide_tree_and_narrow_drawer():
    owner = create_account("dest-wide-narrow-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("data-tree-trash-target") >= 2
    assert content.count('class="tree-nav__trash-link') == 2
    wide = _wide_section(content)
    drawer = _drawer_section(content)
    assert 'href="/trash/"' in wide
    assert 'href="/trash/"' in drawer
    assert ">Trash<" in wide
    assert ">Trash<" in drawer


@pytest.mark.django_db
def test_collapsed_rail_has_no_trash_icon():
    """The collapsed rail's own Trash icon was
    removed entirely -- the tree's own fixed Trash destination
    (`.tree-nav__trash-destination`) is now the only Trash affordance
    this navigation surface offers, reachable only once the tree is
    revealed."""
    owner = create_account("dest-rail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "data-tree-rail-trash" not in content
    assert "workspace-shell__tree-rail-trash" not in content


@pytest.mark.django_db
def test_trash_destination_not_present_on_home_and_all_notes_rail():
    owner = create_account("dest-home-all-notes-owner")
    services.create_note(owner=owner)

    home_content = authenticated_client(owner).get(reverse("home")).content.decode()
    all_notes_content = authenticated_client(owner).get(reverse("notes:all_notes")).content.decode()

    assert "data-tree-rail-trash" not in home_content
    assert "data-tree-rail-trash" not in all_notes_content


# -- Active state ---------------------------------------------------------------


@pytest.mark.django_db
def test_trash_page_marks_the_destination_active():
    owner = create_account("dest-active-owner")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "tree-nav__trash-link--active" in content
    assert "workspace-shell__tree-rail-trash--active" not in content
    assert content.count('aria-current="page"') >= 2


@pytest.mark.django_db
def test_note_detail_does_not_mark_trash_destination_active():
    owner = create_account("dest-inactive-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "tree-nav__trash-link--active" not in content
    assert "workspace-shell__tree-rail-trash--active" not in content


# -- Old duplicate navigation copies removed together ---------------------------


@pytest.mark.django_db
def test_old_top_bar_and_drawer_trash_links_are_all_gone():
    owner = create_account("dest-old-links-gone-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'aria-label="View Trash"' not in content
    assert "workspace-header__trash-link" not in content


@pytest.mark.django_db
def test_home_default_header_has_no_top_bar_trash_link():
    owner = create_account("dest-home-header-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    left_start = content.index('class="account-nav account-nav--left"')
    left_end = content.index('class="account-nav account-nav--center"')
    left_zone = content[left_start:left_end]

    assert ">Home<" in left_zone
    assert ">Trash<" not in left_zone


@pytest.mark.django_db
def test_drawer_nav_has_no_top_level_trash_link():
    owner = create_account("dest-drawer-nav-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    nav_start = content.index('class="workspace-drawer__nav"')
    nav_end = content.index('class="workspace-drawer__tree"')
    drawer_nav = content[nav_start:nav_end]

    assert ">Home<" in drawer_nav
    assert ">Trash<" not in drawer_nav


# -- Quick-trash: presence only with one unambiguous current note --------------


@pytest.mark.django_db
def test_quick_trash_present_on_note_detail():
    owner = create_account("quick-present-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'aria-label="Move current note to Trash"' in content
    action = reverse("notes:note_delete", args=[note.id])
    assert f'action="{action}"' in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_quick_trash_absent_on_home():
    owner = create_account("quick-absent-home-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'aria-label="Move current note to Trash"' not in content


@pytest.mark.django_db
def test_quick_trash_absent_on_all_notes():
    owner = create_account("quick-absent-all-notes-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()

    assert 'aria-label="Move current note to Trash"' not in content


@pytest.mark.django_db
def test_quick_trash_absent_on_trash_page():
    owner = create_account("quick-absent-trash-owner")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert 'aria-label="Move current note to Trash"' not in content


# -- Quick-trash: functional behavior --------------------------------------------


@pytest.mark.django_db
def test_quick_trash_moves_an_ordinary_note_to_trash_with_existing_message():
    owner = create_account("quick-ordinary-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Ordinary Note")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
        follow=True,
    )

    note.refresh_from_db()
    assert note.trashed_at is not None
    messages = [m.message for m in response.context["messages"]]
    assert any("Note moved to Trash" in m for m in messages)


@pytest.mark.django_db
def test_quick_trash_discards_an_untouched_placeholder_note():
    owner = create_account("quick-placeholder-owner")
    note = services.create_note(owner=owner)
    note_id = note.id

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
        follow=True,
    )

    assert not Note.objects.filter(pk=note_id).exists()
    messages = [m.message for m in response.context["messages"]]
    assert any("discarded instead of moved to Trash" in m for m in messages)


@pytest.mark.django_db
def test_quick_trash_is_a_real_post_no_get_shortcut():
    owner = create_account("quick-post-only-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
    )

    # `note_delete` is POST-only now (confirmation
    # moved entirely client-side, to the in-context dialog) -- GET is
    # rejected outright and never trashes the note.
    note.refresh_from_db()
    assert note.trashed_at is None
    assert response.status_code == 405


@pytest.mark.django_db
def test_quick_trash_requires_authentication():
    owner = create_account("quick-auth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
    )

    note.refresh_from_db()
    assert note.trashed_at is None
    assert response.status_code == 302


# -- Note-detail hidden trash/drag markup ----------------------------------------


@pytest.mark.django_db
def test_tree_nav_root_carries_trash_url_templates():
    owner = create_account("templates-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    expected_note_template = reverse("notes:note_delete", args=[0])
    expected_folder_template = reverse("notes:folder_delete_home", args=[0])
    assert f'data-note-trash-url-template="{expected_note_template}"' in content
    assert f'data-folder-trash-url-template="{expected_folder_template}"' in content


@pytest.mark.django_db
def test_hidden_trash_forms_are_present_and_hidden_in_both_tree_copies():
    owner = create_account("hidden-forms-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('class="tree-nav__action--trash-note" hidden') == 2
    assert content.count('class="tree-nav__action--trash-folder" hidden') == 2


# -- Folder drag handles: ordinary folders yes, recovery marker no -------------


@pytest.mark.django_db
def test_ordinary_folder_has_a_drag_handle():
    owner = create_account("folder-drag-ordinary-owner")
    folder = services.create_folder(owner=owner, name="Ordinary Folder")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert f'aria-label="Drag to move {folder.name} to Trash"' in content


@pytest.mark.django_db
def test_recovery_marker_folder_has_no_drag_handle():
    owner = create_account("folder-drag-recovery-owner")
    admin = create_admin("folder-drag-recovery-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Needs Recovery")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    services.restore_note_for_administrator(note_id=note.id, actor=admin)
    note.refresh_from_db()
    recovery_folder = note.folder
    assert recovery_folder.is_recovery_folder is True

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert f'aria-label="Drag to move {recovery_folder.name} to Trash"' not in content
    # The folder itself is still rendered (visible, since it now holds a note).
    assert recovery_folder.name in content


# -- Already-trashed folder guard: repeating the drag/quick-trash-style POST is safe --


@pytest.mark.django_db
def test_folder_delete_home_already_trashed_is_a_safe_no_op_not_a_repeat_trash():
    owner = create_account("folder-already-trashed-owner")
    folder = services.create_folder(owner=owner, name="Once Trashed Folder")
    services.move_folder_to_trash(folder=folder)
    first_trashed_at = Folder.objects.get(pk=folder.pk).trashed_at

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id]),
        {"origin": "home"},
        follow=True,
    )

    folder.refresh_from_db()
    assert folder.trashed_at == first_trashed_at
    messages = [m.message for m in response.context["messages"]]
    assert any("already in Trash" in m for m in messages)
