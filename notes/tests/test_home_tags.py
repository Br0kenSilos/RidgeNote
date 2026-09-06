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


def _note_item_section(content: str, note_id: int) -> str:
    marker = f'data-note-id="{note_id}"'
    start = content.index(marker)
    end = content.index("</li>", start)
    return content[start:end]


def _recent_section(content: str) -> str:
    # The shared tree rail (which also has `data-note-id`-marked rows)
    # renders before the Recent module in document order, so a plain
    # `_note_item_section` lookup would find the tree's row for the same
    # note instead. Scope to the Recent module first.
    recent_start = content.index('class="home-recent"')
    return content[recent_start : content.index('id="workspace-drawer"')]


def _recent_note_item_section(content: str, note_id: int) -> str:
    return _note_item_section(_recent_section(content), note_id)


# Cross-
# owner tag scoping is independently covered at the route level in
# `test_tags.py` (`test_note_tag_assign_view_cross_owner_note_returns_404`
# and `test_note_tag_remove_view_cross_owner_tag_returns_404`).
#
# The dashboard's Recent notes module has read-only tag chips (reusing All
# Notes' own `.note-list__tags`/`.note-list__tag-chip` markup, via the
# shared `_note_tag_chips.html` partial) and a read-only pin marker
# (reusing the tree rail's own `★` glyph) on this module's rows --
# with no assign/remove/pin *controls* of any kind (ordering tests, including
# pin promotion, live in
# `test_home_dashboard.py`). The tests below cover this
# contract; the tag-absence tests further down remain accurate for
# genuinely untagged/unpinned notes and rows outside the Recent module.


@pytest.mark.django_db
def test_home_untagged_note_shows_no_tag_chip_ui():
    owner = create_account("home-untagged-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    note_section = _note_item_section(content, note.id)

    assert "note-tags__chip" not in note_section
    assert "note-list__tags" not in note_section


@pytest.mark.django_db
def test_home_recent_row_shows_read_only_tag_chip():
    owner = create_account("home-tagged-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    note_section = _recent_note_item_section(content, note.id)

    assert "note-list__tags" in note_section
    assert (
        '<span class="note-list__tag-chip" data-tag-color="blue">'
        '<span class="note-list__tag-chip__label">Work</span></span>' in note_section
    )


@pytest.mark.django_db
def test_home_recent_row_shows_multiple_tag_chips_safely():
    owner = create_account("home-multi-tag-owner")
    note = services.create_note(owner=owner)
    work = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    urgent = services.get_or_create_tag(owner=owner, name="Urgent", color="rose")
    services.assign_tag_to_note(note=note, tag=work)
    services.assign_tag_to_note(note=note, tag=urgent)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    note_section = _recent_note_item_section(content, note.id)

    assert (
        '<span class="note-list__tag-chip" data-tag-color="blue">'
        '<span class="note-list__tag-chip__label">Work</span></span>' in note_section
    )
    assert (
        '<span class="note-list__tag-chip" data-tag-color="rose">'
        '<span class="note-list__tag-chip__label">Urgent</span></span>' in note_section
    )


@pytest.mark.django_db
def test_home_recent_row_shows_pin_indicator_for_pinned_note():
    owner = create_account("home-pinned-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pinned Note")
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    note_section = _recent_note_item_section(content, note.id)

    assert 'class="note-list__pin-badge" role="img" aria-label="Pinned"' in note_section
    assert "★" in note_section


@pytest.mark.django_db
def test_home_recent_row_shows_no_pin_indicator_for_unpinned_note():
    owner = create_account("home-unpinned-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Unpinned Note")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    note_section = _recent_note_item_section(content, note.id)

    assert "note-list__pin-badge" not in note_section
    assert "★" not in note_section


@pytest.mark.django_db
def test_home_recent_row_renders_with_long_title_and_many_tags():
    # No dedicated wrapping assertion here -- overflow-wrap is a CSS
    # concern, reusing the
    # same `.note-list__meta`/`.note-list__tags` overflow-wrap rules
    # already proven on All Notes (Home's rows
    # are the identical shared markup, not a lookalike copy). This
    # just confirms a long title and several tags don't break rendering.
    owner = create_account("home-long-content-owner")
    note = services.create_note(owner=owner)
    services.rename_note(
        note=note,
        title="A deliberately very long note title meant to exercise wrapping "
        "behavior across narrow and wide layouts alike",
    )
    for name, color in [("Work", "blue"), ("Urgent", "rose"), ("Personal", "green")]:
        tag = services.get_or_create_tag(owner=owner, name=name, color=color)
        services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))

    assert response.status_code == 200
    note_section = _recent_note_item_section(response.content.decode(), note.id)
    # Exact outer-class attribute match -- a bare substring count would
    # double-count each chip, since its own nested
    # `note-list__tag-chip__label` (the chip's own tooltip
    # element) contains "note-list__tag-chip" as a substring too.
    assert note_section.count('class="note-list__tag-chip"') == 3


@pytest.mark.django_db
def test_home_has_no_tag_assign_or_remove_controls():
    owner = create_account("home-no-controls-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "note-tags__assign-form" not in content
    assert "note-tags__remove-form" not in content
    assert "Add a tag" not in content
    assert 'aria-label="Remove tag' not in content


@pytest.mark.django_db
def test_home_has_no_tag_routes_exposed():
    owner = create_account("home-no-routes-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assign_url = reverse("notes:note_tag_assign", args=[note.id])
    remove_url = reverse("notes:note_tag_remove", args=[note.id])
    assert f'action="{assign_url}"' not in content
    assert f'action="{remove_url}"' not in content


@pytest.mark.django_db
def test_home_tree_drawer_has_no_tag_ui():
    owner = create_account("home-tree-no-tag-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    tree_section = content[content.index('id="workspace-drawer"') :]
    assert "note-tags__chip" not in tree_section
    assert "note-list__tags" not in tree_section


@pytest.mark.django_db
def test_home_row_action_menu_has_no_tag_controls():
    owner = create_account("home-row-menu-no-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    panel_start = content.index('<div class="tree-nav__row-menu-panel">')
    panel_end = content.index("</div>", panel_start)
    panel = content[panel_start:panel_end]
    assert "tag" not in panel.lower()


@pytest.mark.django_db
def test_home_query_count_does_not_scale_with_number_of_tagged_notes():
    owner = create_account("home-query-count-owner")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    for _ in range(5):
        note = services.create_note(owner=owner)
        services.assign_tag_to_note(note=note, tag=tag)

    client = authenticated_client(owner)

    baseline_note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=baseline_note, tag=tag)
    with CaptureQueriesContext(connection) as baseline_queries:
        authenticated_client(owner).get(reverse("home"))
    baseline_count = len(baseline_queries.captured_queries)

    for _ in range(10):
        note = services.create_note(owner=owner)
        services.assign_tag_to_note(note=note, tag=tag)

    with CaptureQueriesContext(connection) as scaled_queries:
        client.get(reverse("home"))
    scaled_count = len(scaled_queries.captured_queries)

    assert scaled_count <= baseline_count + 2


@pytest.mark.django_db
def test_home_move_control_still_targets_existing_route_with_tags_present():
    owner = create_account("home-move-with-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    expected_action = reverse("notes:note_move_home", args=[note.id])
    assert f'action="{expected_action}"' in content


@pytest.mark.django_db
def test_home_row_action_menu_still_has_expected_items_with_tags_present():
    owner = create_account("home-row-menu-with-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    panel_start = content.index('<div class="tree-nav__row-menu-panel">')
    panel_end = content.index("</div>", panel_start)
    panel = content[panel_start:panel_end]
    # The row-menu has no duplicate Move form
    # (see test_row_action_menu.py); Move lives only in the single,
    # always-visible icon-button popover beside this menu.
    for marker in ["Open", "Rename to", "Print", "Download"]:
        assert marker in panel
