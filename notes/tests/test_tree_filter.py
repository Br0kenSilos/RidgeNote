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
def test_home_renders_filter_input():
    # Home has a wide tree rail alongside its
    # narrow drawer copy, matching note-detail's own
    # two-copies pattern (see the sibling test below).
    owner = create_account("filter-home-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert content.count("data-tree-filter-input") == 2  # wide tree + narrow drawer
    assert content.count('placeholder="Filter notes…"') == 2
    assert content.count("data-tree-filter-empty") == 2


@pytest.mark.django_db
def test_note_detail_renders_filter_input_in_both_tree_copies():
    owner = create_account("filter-note-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("data-tree-filter-input") == 2  # wide tree + narrow drawer
    assert content.count('placeholder="Filter notes…"') == 2
    assert content.count("data-tree-filter-empty") == 2


@pytest.mark.django_db
def test_note_detail_tree_content_still_renders_correctly_with_filter_present():
    owner = create_account("filter-content-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    current_note = services.create_note(owner=owner)
    services.assign_note_folder(note=current_note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    assert f'data-folder-id="{folder.id}"' in content
    assert f'data-note-id="{current_note.id}"' in content
    assert 'aria-label="Move note"' in content


@pytest.mark.django_db
def test_home_dashboard_still_has_no_drag_affordances():
    # Home's narrow-drawer tree copy and wide tree rail both carry drag
    # handles legitimately. Only Home's own
    # dashboard content pane (not the tree beside it) is expected to
    # stay drag-free.
    owner = create_account("filter-home-no-drag-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    dashboard_start = content.index('class="home-dashboard home-dashboard--populated"')
    dashboard_section = content[dashboard_start : content.index('id="workspace-drawer"')]

    assert "tree-nav__drag-handle" not in dashboard_section
    assert "data-move-url-template" not in dashboard_section


@pytest.mark.django_db
def test_note_detail_tree_still_has_drag_affordances():
    owner = create_account("filter-note-detail-drag-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("tree-nav__drag-handle") == 2
    assert content.count("data-move-url-template") == 2


@pytest.mark.django_db
def test_tree_ordering_is_alphabetical_by_title():
    # The tree orders notes case-insensitively
    # alphabetical by title. "Zulu Note" is created *first* and "Apple Note"
    # *second* -- deliberately reversed from alphabetical order, so this
    # test only passes if the tree is genuinely sorting by title.
    owner = create_account("filter-ordering-owner")
    first_created = services.create_note(owner=owner)
    Note.objects.filter(pk=first_created.pk).update(title="Zulu Note")
    second_created = services.create_note(owner=owner)
    Note.objects.filter(pk=second_created.pk).update(title="Apple Note")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[second_created.id]))
    content = response.content.decode()

    drawer = _drawer_section(content)
    apple_pos = drawer.index(f'data-note-id="{second_created.id}"')
    zulu_pos = drawer.index(f'data-note-id="{first_created.id}"')
    assert apple_pos < zulu_pos


@pytest.mark.django_db
def test_no_duplicate_element_ids_with_filter_input():
    owner = create_account("filter-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
