import re
from datetime import timedelta

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


def _seed_note(owner, *, title, modified_hours_ago, pinned=False, folder=None):
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(
        title=title,
        modified_at=now - timedelta(hours=modified_hours_ago),
        pinned=pinned,
    )
    if folder is not None:
        services.assign_note_folder(note=note, folder=folder)
    note.refresh_from_db()
    return note


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


def _wide_tree_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


def _note_order_in_section(section: str, titles: list[str]) -> list[str]:
    positions = {title: section.index(f">{title}<") for title in titles}
    return sorted(titles, key=lambda title: positions[title])


# -- notes_grouped_for_tree ordering (service) -------------------------------


@pytest.mark.django_db
def test_pinned_note_promoted_within_folder():
    owner = create_account("folder-promote-owner")
    folder = services.create_folder(owner=owner, name="Work")
    older_pinned = _seed_note(
        owner, title="Older Pinned", modified_hours_ago=5, pinned=True, folder=folder
    )
    newer_unpinned = _seed_note(owner, title="Newer Unpinned", modified_hours_ago=1, folder=folder)

    folders, _ = services.notes_grouped_for_tree(owner=owner)
    notes_in_folder = list(folders[0].notes.all())

    assert [n.id for n in notes_in_folder] == [older_pinned.id, newer_unpinned.id]


@pytest.mark.django_db
def test_pinned_note_promoted_within_unfiled():
    owner = create_account("unfiled-promote-owner")
    older_pinned = _seed_note(owner, title="Older Pinned", modified_hours_ago=5, pinned=True)
    newer_unpinned = _seed_note(owner, title="Newer Unpinned", modified_hours_ago=1)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [older_pinned.id, newer_unpinned.id]


@pytest.mark.django_db
def test_folder_and_unfiled_promotion_are_independent():
    owner = create_account("independent-promote-owner")
    folder = services.create_folder(owner=owner, name="Work")
    folder_pinned = _seed_note(
        owner, title="Folder Pinned", modified_hours_ago=4, pinned=True, folder=folder
    )
    folder_unpinned = _seed_note(
        owner, title="Folder Unpinned", modified_hours_ago=1, folder=folder
    )
    unfiled_pinned = _seed_note(owner, title="Unfiled Pinned", modified_hours_ago=3, pinned=True)
    unfiled_unpinned = _seed_note(owner, title="Unfiled Unpinned", modified_hours_ago=2)

    folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)
    notes_in_folder = list(folders[0].notes.all())

    assert [n.id for n in notes_in_folder] == [folder_pinned.id, folder_unpinned.id]
    assert [n.id for n in unfiled_notes] == [unfiled_pinned.id, unfiled_unpinned.id]


@pytest.mark.django_db
def test_unpinned_notes_sort_alphabetically_by_title():
    # The tree orders unfiled notes case-insensitively
    # alphabetical by title, not by `-modified_at, -id`. The
    # alphabetically-first note ("Apple") is deliberately given the
    # *oldest* `modified_at` here, so this test only passes if the tree is
    # genuinely sorting by title.
    owner = create_account("unpinned-order-owner")
    cherry = _seed_note(owner, title="Cherry", modified_hours_ago=1)  # most recent
    banana = _seed_note(owner, title="Banana", modified_hours_ago=2)
    apple = _seed_note(owner, title="Apple", modified_hours_ago=3)  # least recent

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [apple.id, banana.id, cherry.id]


@pytest.mark.django_db
def test_deterministic_id_tie_break_within_unfiled():
    # The tree's tie-break applies to notes with
    # the same *normalized (case-insensitive) title*, not the same
    # `modified_at` -- title is the primary sort key, so two notes
    # only tie when their titles collide.
    owner = create_account("tie-break-owner")
    first = services.create_note(owner=owner)
    second = services.create_note(owner=owner)
    Note.objects.filter(pk=first.pk).update(title="Draft")
    Note.objects.filter(pk=second.pk).update(title="draft")
    assert second.pk > first.pk

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [second.pk, first.pk]


@pytest.mark.django_db
def test_folder_ordering_unchanged_by_pin_state():
    owner = create_account("folder-order-owner")
    services.create_folder(owner=owner, name="cherry")
    services.create_folder(owner=owner, name="Apple")
    banana = services.create_folder(owner=owner, name="banana")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=banana)
    services.set_note_pinned(note=note, pinned=True)

    folders, _ = services.notes_grouped_for_tree(owner=owner)

    assert [f.name for f in folders] == ["Apple", "banana", "cherry"]


# -- Tree/drawer rendering ----------------------------------------------------


@pytest.mark.django_db
def test_pin_marker_rendered_for_pinned_tree_row():
    owner = create_account("marker-present-owner")
    current_note = services.create_note(owner=owner)
    pinned_note = _seed_note(owner, title="Pinned Note", modified_hours_ago=1, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    assert content.count(f'data-note-id="{pinned_note.id}"') == 2
    # One marker per tree copy (wide tree + narrow drawer)
    assert content.count('class="tree-nav__pin-marker" role="img" aria-label="Pinned"') == 2


@pytest.mark.django_db
def test_pin_marker_absent_for_unpinned_tree_row():
    owner = create_account("marker-absent-owner")
    current_note = services.create_note(owner=owner)
    services.create_note(owner=owner)  # unpinned

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    assert "tree-nav__pin-marker" not in content


@pytest.mark.django_db
def test_pin_marker_has_accessible_name():
    owner = create_account("marker-accessible-owner")
    current_note = services.create_note(owner=owner)
    services.set_note_pinned(note=current_note, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    assert 'role="img" aria-label="Pinned"' in content


@pytest.mark.django_db
def test_pin_marker_present_in_both_wide_tree_and_narrow_drawer():
    owner = create_account("marker-both-copies-owner")
    current_note = services.create_note(owner=owner)
    pinned_note = _seed_note(owner, title="Pinned Note", modified_hours_ago=1, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)

    assert "tree-nav__pin-marker" in wide
    assert "tree-nav__pin-marker" in drawer
    assert f'data-note-id="{pinned_note.id}"' in wide
    assert f'data-note-id="{pinned_note.id}"' in drawer


@pytest.mark.django_db
def test_pin_marker_renders_on_home_drawer_tree_too():
    owner = create_account("marker-home-owner")
    pinned_note = _seed_note(owner, title="Pinned Note", modified_hours_ago=1, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    drawer = _drawer_section(content)
    assert "tree-nav__pin-marker" in drawer
    assert f'data-note-id="{pinned_note.id}"' in drawer


@pytest.mark.django_db
def test_no_duplicate_element_ids_with_tree_pin_markers():
    owner = create_account("no-dup-id-owner")
    current_note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")
    services.set_note_pinned(note=current_note, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


# -- Independence from Home's session-backed sort ----------------------------


@pytest.mark.django_db
def test_home_session_sort_has_no_effect_on_tree_ordering():
    owner = create_account("sort-independence-owner")
    _seed_note(owner, title="Older Pinned", modified_hours_ago=5, pinned=True)
    _seed_note(owner, title="Newer Unpinned", modified_hours_ago=1)
    client = authenticated_client(owner)

    # Select an explicit, non-default Home sort mode (title A-Z).
    response = client.get(reverse("home"), {"sort": "title_asc"})
    content = response.content.decode()
    drawer = _drawer_section(content)

    order = _note_order_in_section(drawer, ["Older Pinned", "Newer Unpinned"])
    # Tree ordering must remain pinned-first regardless of Home's active sort.
    assert order == ["Older Pinned", "Newer Unpinned"]
