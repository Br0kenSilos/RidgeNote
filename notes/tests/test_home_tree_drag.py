import re

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


@pytest.mark.django_db
def test_home_drawer_tree_renders_drag_handles_for_each_note():
    owner = create_account("home-drag-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    filed_note = services.create_note(owner=owner)
    Note.objects.filter(pk=filed_note.pk).update(title="Filed Note")
    filed_note.refresh_from_db()
    services.assign_note_folder(note=filed_note, folder=folder)
    unfiled_note = services.create_note(owner=owner)
    Note.objects.filter(pk=unfiled_note.pk).update(title="Unfiled Note")
    unfiled_note.refresh_from_db()

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)

    # Folders carry their own drag handle (for
    # dragging onto Trash) -- 2 notes + 1 ordinary folder here.
    assert drawer.count("tree-nav__drag-handle") == 3
    assert f'aria-label="Drag to move {filed_note.title}"' in drawer
    assert f'aria-label="Drag to move {unfiled_note.title}"' in drawer
    assert f'aria-label="Drag to move {folder.name} to Trash"' in drawer


@pytest.mark.django_db
def test_home_drawer_tree_move_url_template_targets_note_move_home():
    owner = create_account("home-drag-url-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)

    expected_template = reverse("notes:note_move_home", args=[0])
    assert f'data-move-url-template="{expected_template}"' in drawer


@pytest.mark.django_db
def test_home_drawer_note_links_are_not_draggable():
    owner = create_account("home-drag-not-draggable-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)

    assert re.search(r'<a class="tree-nav__note-link"\s+draggable="false"', drawer)


@pytest.mark.django_db
def test_home_drawer_still_has_no_visible_move_disclosure():
    # Home's drawer tree has drag handles but not a visible
    # Move note disclosure -- that control is reserved for note-detail
    # context and Home's flat list, matching the approved scope.
    owner = create_account("home-drag-no-visible-move-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)

    assert 'aria-label="Move note"' not in drawer


@pytest.mark.django_db
def test_home_drawer_still_renders_new_note_new_folder_filter_pin_marker_and_note_link():
    owner = create_account("home-drag-coexistence-owner")
    services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)

    assert 'aria-label="New note"' in drawer
    assert "New folder" in drawer
    assert 'placeholder="Filter notes…"' in drawer
    assert "tree-nav__pin-marker" in drawer
    assert f'href="{reverse("notes:detail", args=[note.id])}"' in drawer


@pytest.mark.django_db
def test_note_detail_tree_still_targets_note_move_and_retains_move_disclosure():
    owner = create_account("home-drag-note-detail-regression-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    expected_template = reverse("notes:note_move", args=[0])
    assert content.count(f'data-move-url-template="{expected_template}"') == 2
    assert content.count('aria-label="Move note"') == 2
    assert content.count("tree-nav__drag-handle") == 2


@pytest.mark.django_db
def test_home_drawer_renders_no_duplicate_element_ids_with_drag_handles():
    owner = create_account("home-drag-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
