import re

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


# -- set_note_pinned (service) -----------------------------------------------


@pytest.mark.django_db
def test_new_note_defaults_to_unpinned():
    owner = create_account("default-unpinned-owner")

    note = services.create_note(owner=owner)

    assert note.pinned is False


@pytest.mark.django_db
def test_set_note_pinned_true_pins_the_note():
    owner = create_account("pin-true-owner")
    note = services.create_note(owner=owner)

    updated = services.set_note_pinned(note=note, pinned=True)

    assert updated.pinned is True
    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_set_note_pinned_false_unpins_the_note():
    owner = create_account("pin-false-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    updated = services.set_note_pinned(note=note, pinned=False)

    assert updated.pinned is False
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_set_note_pinned_true_repeated_is_a_safe_no_op():
    owner = create_account("pin-repeat-true-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    updated = services.set_note_pinned(note=note, pinned=True)

    assert updated.pinned is True
    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_set_note_pinned_false_repeated_is_a_safe_no_op():
    owner = create_account("pin-repeat-false-owner")
    note = services.create_note(owner=owner)

    services.set_note_pinned(note=note, pinned=False)
    updated = services.set_note_pinned(note=note, pinned=False)

    assert updated.pinned is False
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_set_note_pinned_does_not_change_modified_at():
    owner = create_account("pin-modified-owner")
    note = services.create_note(owner=owner)
    original_modified_at = note.modified_at

    services.set_note_pinned(note=note, pinned=True)

    note.refresh_from_db()
    assert note.modified_at == original_modified_at


@pytest.mark.django_db
def test_set_note_pinned_does_not_change_version():
    owner = create_account("pin-version-owner")
    note = services.create_note(owner=owner)
    original_version = note.version

    services.set_note_pinned(note=note, pinned=True)

    note.refresh_from_db()
    assert note.version == original_version


# -- note_pin_set view --------------------------------------------------------


@pytest.mark.django_db
def test_note_pin_set_view_pins_note_and_redirects_to_home():
    owner = create_account("view-pin-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_note_pin_set_view_unpins_note_and_redirects_to_home():
    owner = create_account("view-unpin-owner")
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
def test_note_pin_set_view_malformed_value_leaves_note_unchanged():
    owner = create_account("view-malformed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "not-a-valid-value"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_note_pin_set_view_missing_value_leaves_note_unchanged():
    owner = create_account("view-missing-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_pin_set", args=[note.id]), {})

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_note_pin_set_view_cross_owner_note_returns_404():
    owner = create_account("view-cross-owner")
    other = create_account("view-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )

    assert response.status_code == 404
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_note_pin_set_view_unauthenticated_post_is_blocked():
    owner = create_account("view-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"})

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.pinned is False


@pytest.mark.django_db
def test_note_pin_set_view_get_is_not_allowed():
    owner = create_account("view-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_pin_set", args=[note.id]))

    assert response.status_code == 405


# -- All Notes ordering --------------------------------------------------------


def _flat_note_list_section(content: str) -> str:
    # The `<ul class="note-list">` search must not silently fall back to the whole
    # page on a miss -- that would let these tests keep passing by
    # coincidentally observing the persistent tree's own fixed pinned-first
    # ordering, not the All Notes-rendered pinned sort. All Notes is the
    # real, single surface rendering a pinned-sorted flat list -- the
    # marker is required to exist (no silent fallback) so a future
    # regression here fails loudly instead of quietly re-matching
    # something else.
    flat_list_start = content.index('<ul class="note-list">')
    return content[flat_list_start : content.index('id="workspace-drawer"')]


@pytest.mark.django_db
def test_all_notes_shows_pinned_notes_before_unpinned_notes():
    owner = create_account("order-owner")
    older_pinned = services.create_note(owner=owner)
    newer_unpinned = services.create_note(owner=owner)
    services.set_note_pinned(note=older_pinned, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    flat_list = _flat_note_list_section(response.content.decode())

    pinned_href = reverse("notes:detail", args=[older_pinned.id])
    unpinned_href = reverse("notes:detail", args=[newer_unpinned.id])
    assert flat_list.index(f'href="{pinned_href}"') < flat_list.index(f'href="{unpinned_href}"')


@pytest.mark.django_db
def test_all_notes_pinned_notes_tie_break_by_modified_at_then_id():
    owner = create_account("tie-break-owner")
    first_pinned = services.create_note(owner=owner)
    second_pinned = services.create_note(owner=owner)
    services.set_note_pinned(note=first_pinned, pinned=True)
    services.set_note_pinned(note=second_pinned, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    flat_list = _flat_note_list_section(response.content.decode())

    first_href = reverse("notes:detail", args=[first_pinned.id])
    second_href = reverse("notes:detail", args=[second_pinned.id])
    assert flat_list.index(f'href="{second_href}"') < flat_list.index(f'href="{first_href}"')


@pytest.mark.django_db
def test_all_notes_unpinned_notes_remain_ordered_by_modified_at_then_id():
    owner = create_account("unpinned-order-owner")
    first = services.create_note(owner=owner)
    second = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    flat_list = _flat_note_list_section(response.content.decode())

    first_href = reverse("notes:detail", args=[first.id])
    second_href = reverse("notes:detail", args=[second.id])
    assert flat_list.index(f'href="{second_href}"') < flat_list.index(f'href="{first_href}"')


@pytest.mark.django_db
def test_home_pinning_keeps_note_within_its_shared_tree_folder_group():
    # Home's flat-list pin state must not move a note out of its tree folder
    # group. Pinning *does* promote a note within
    # its own tree group (see notes/tests/test_tree_pinning.py for the full
    # ordering coverage) -- this test only guards the folder-membership
    # boundary, not the specific ordering.
    owner = create_account("tree-unaffected-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    older_pinned = services.create_note(owner=owner)
    newer_unpinned = services.create_note(owner=owner)
    services.assign_note_folder(note=older_pinned, folder=folder)
    services.assign_note_folder(note=newer_unpinned, folder=folder)
    services.set_note_pinned(note=older_pinned, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    tree_section = content[content.index('id="workspace-drawer"') :]
    folder_section_start = tree_section.index(f'data-folder-id="{folder.id}"')
    folder_section_end = tree_section.index("tree-nav__folder--unfiled")
    folder_section = tree_section[folder_section_start:folder_section_end]

    assert f'data-note-id="{older_pinned.id}"' in folder_section
    assert f'data-note-id="{newer_unpinned.id}"' in folder_section


# -- Home rendering ------------------------------------------------------------
#
# Home's dashboard's Recent module deliberately has no pinned-state
# indicator or action buttons of any kind. There is no per-row Pin/Unpin
# icon-button markup here to cover (Home shows no pin state on any row);
# the `note_pin_set` route itself remains directly covered above
# (`test_note_pin_set_view_pins_note_and_redirects_to_home` and
# neighbors).


@pytest.mark.django_db
def test_pin_off_icon_partial_no_longer_exists():
    # The unused pin-off.svg asset does not exist -- no partial
    # ships that no template references (see the two tests above).
    from pathlib import Path

    from django.conf import settings

    matches = list(Path(settings.BASE_DIR).rglob("pin-off.svg"))
    assert matches == []


@pytest.mark.django_db
def test_home_renders_no_duplicate_element_ids_with_pin_controls():
    owner = create_account("no-dup-id-pin-owner")
    services.create_note(owner=owner)
    pinned_note = services.create_note(owner=owner)
    services.set_note_pinned(note=pinned_note, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


@pytest.mark.django_db
def test_home_does_not_introduce_pin_indicator_into_shared_tree():
    owner = create_account("tree-no-indicator-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    tree_section = content[content.index('id="workspace-drawer"') :]
    assert "note-list__pin-badge" not in tree_section
    assert "note-list__pin-toggle" not in tree_section


# The "stable three-region row anatomy" tests (row
# content/action region structure, pin/tag/long-title row parity) have
# no current rendered consumer, for the same reason as the Home
# pin-rendering tests above.
