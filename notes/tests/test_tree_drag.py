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


def _wide_tree_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


@pytest.mark.django_db
def test_note_detail_renders_drag_handles_and_move_url_template_in_both_tree_copies():
    owner = create_account("drag-note-detail-owner")
    current_note = services.create_note(owner=owner)
    Note.objects.filter(pk=current_note.pk).update(title="Current Note")
    current_note.refresh_from_db()
    other_note = services.create_note(owner=owner)
    Note.objects.filter(pk=other_note.pk).update(title="Other Note")
    other_note.refresh_from_db()

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    assert content.count("tree-nav__drag-handle") == 2 * 2  # 2 notes x 2 tree copies
    assert content.count(f'aria-label="Drag to move {current_note.title}"') == 2
    assert content.count(f'aria-label="Drag to move {other_note.title}"') == 2

    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)
    assert 'data-move-url-template="/notes/0/move/"' in wide
    assert 'data-move-url-template="/notes/0/move/"' in drawer


@pytest.mark.django_db
def test_note_detail_note_links_are_not_draggable():
    owner = create_account("drag-not-draggable-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert re.search(r'<a class="tree-nav__note-link"\s+draggable="false"', content)


@pytest.mark.django_db
def test_home_dashboard_has_no_drag_affordances_while_tree_and_drawer_do():
    # Home's narrow-drawer tree copy legitimately has drag-and-drop (a
    # Home-context move-url-template pointing at
    # notes:note_move_home), and its wide tree
    # rail legitimately has the same drag affordance -- only
    # Home's own dashboard pane (the content column, not the tree beside
    # it) is expected to stay drag-free.
    owner = create_account("drag-home-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    dashboard_start = content.index('class="home-dashboard home-dashboard--populated"')
    dashboard_section = content[dashboard_start : content.index('id="workspace-drawer"')]
    drawer_section = content[content.index('id="workspace-drawer"') :]

    assert "tree-nav__drag-handle" not in dashboard_section
    assert "data-move-url-template" not in dashboard_section
    assert 'draggable="false"' not in dashboard_section

    assert "tree-nav__drag-handle" in drawer_section
    assert 'data-move-url-template="/notes/0/move/home/"' in drawer_section
    assert 'draggable="false"' in drawer_section


@pytest.mark.django_db
def test_note_detail_renders_no_duplicate_element_ids_with_drag_handles():
    owner = create_account("drag-no-dup-id-owner")
    current_note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
