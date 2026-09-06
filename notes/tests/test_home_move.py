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


# -- note_move_home view -----------------------------------------------------


@pytest.mark.django_db
def test_note_move_home_view_is_post_only():
    owner = create_account("home-move-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_move_home", args=[note.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_note_move_home_view_moves_note_to_owned_folder_and_redirects_to_home():
    owner = create_account("home-move-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    client = authenticated_client(owner)

    response = client.post(reverse("notes:note_move_home", args=[note.id]), {"folder": folder.id})

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_note_move_home_view_moves_note_to_unfiled_and_redirects_to_home():
    owner = create_account("home-move-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    client = authenticated_client(owner)

    response = client.post(reverse("notes:note_move_home", args=[note.id]), {"folder": ""})

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_home_view_cross_owner_note_returns_404():
    owner = create_account("home-move-cross-owner")
    other = create_account("home-move-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": ""}
    )

    assert response.status_code == 404
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_home_view_cross_owner_folder_value_leaves_note_unchanged_and_shows_message():
    owner = create_account("home-move-cross-folder-owner")
    other = create_account("home-move-cross-folder-other")
    note = services.create_note(owner=owner)
    other_folder = services.create_folder(owner=other, name="Other's Folder")

    response = authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]),
        {"folder": other_folder.id},
        follow=True,
    )

    note.refresh_from_db()
    assert note.folder_id is None
    assert b"Could not move the note" in response.content


@pytest.mark.django_db
def test_note_move_home_view_malformed_folder_value_leaves_note_unchanged():
    owner = create_account("home-move-malformed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": "not-a-number"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_home_view_unauthenticated_post_is_blocked():
    owner = create_account("home-move-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:note_move_home", args=[note.id]), {"folder": ""})

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_home_view_does_not_change_title_body_version_or_pin_state():
    owner = create_account("home-move-untouched-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)
    folder = services.create_folder(owner=owner, name="Projects")
    original_title = note.title
    original_body_json = note.body_json
    original_version = note.version

    authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": folder.id}
    )

    note.refresh_from_db()
    assert note.title == original_title
    assert note.body_json == original_body_json
    assert note.version == original_version
    assert note.pinned is True


@pytest.mark.django_db
def test_note_move_view_behavior_remains_unchanged_alongside_new_home_route():
    # Regression check that adding note_move_home did not alter note_move's
    # existing note-detail redirect target or owner-scoping behavior.
    owner = create_account("home-move-regression-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": folder.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    note.refresh_from_db()
    assert note.folder_id == folder.id


# -- Home template rendering --------------------------------------------------
#
# Home's dashboard has no per-row actions at all, by design -- see
# `notes/tests/test_home_dashboard.py`. There is no per-row Move popover
# markup here to cover.
# Home's current New Note action is covered by
# `test_home_dashboard_has_prominent_new_note_action` in
# `test_home_dashboard.py`; the `note_move_home` route itself remains
# covered by the request-contract tests above and by
# `test_home_move_control_still_targets_existing_route_with_tags_present`
# in `test_home_tags.py`.


@pytest.mark.django_db
def test_home_narrow_drawer_tree_does_not_render_move_controls():
    owner = create_account("home-move-drawer-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer_section = content[content.index('id="workspace-drawer"') :]

    assert "note-list__move" not in drawer_section
    assert "note_move_home" not in drawer_section


@pytest.mark.django_db
def test_home_dashboard_has_no_drag_affordances_or_move_url_template():
    # Renamed and re-scoped from the retired flat-list version: Home's
    # dashboard pane specifically (not the wide tree rail beside it,
    # which legitimately has drag handles and its own move-url-template
    # hook, same as note detail's tree) has no drag handles and no move
    # affordance of its own.
    owner = create_account("home-move-no-drag-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    dashboard_start = content.index('class="home-dashboard home-dashboard--populated"')
    dashboard_section = content[dashboard_start : content.index('id="workspace-drawer"')]

    assert "tree-nav__drag-handle" not in dashboard_section
    assert "data-move-url-template" not in dashboard_section


@pytest.mark.django_db
def test_note_detail_tree_move_disclosure_and_drag_handles_remain_unaffected():
    owner = create_account("home-move-note-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "Move note" in content
    assert f'action="{reverse("notes:note_move", args=[note.id])}"' in content
    assert content.count("tree-nav__drag-handle") == 2


# -- Home dashboard Recent-notes row current-folder metadata ----------------
#
# Home's per-row folder context uses the same "in Folder Name" /
# "Unfiled" wording throughout. Home's row markup is unified with All
# Notes' own shared `.note-list__*` anatomy (via `_all_notes_row.html`),
# so this element is `.note-list__location` -- same wording, same
# underlying `.note-list__title`/content/actions row shape as every
# other surface in the shared row system.


def _recent_item(content: str, note_id: int) -> str:
    recent_section = content[
        content.index('class="home-recent"') : content.index('id="workspace-drawer"')
    ]
    marker = f'href="/notes/{note_id}/"'
    start = recent_section.rindex(
        f'<li class="note-list__item" data-note-id="{note_id}">',
        0,
        recent_section.index(marker),
    )
    end = recent_section.index("</li>", start)
    return recent_section[start:end]


@pytest.mark.django_db
def test_home_recent_row_shows_named_current_folder():
    owner = create_account("home-location-named-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("home"))
    row = _recent_item(response.content.decode(), note.id)

    location_start = row.index('<span class="note-list__location">')
    location_end = row.index("</span>", location_start)
    location_markup = row[location_start:location_end]

    assert location_markup.strip().endswith("in Projects")


@pytest.mark.django_db
def test_home_recent_row_shows_unfiled_for_notes_without_a_folder():
    owner = create_account("home-location-unfiled-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    row = _recent_item(response.content.decode(), note.id)

    assert '<span class="note-list__location">Unfiled</span>' in row


@pytest.mark.django_db
def test_home_recent_row_folder_location_handles_long_folder_names_safely():
    owner = create_account("home-location-long-owner")
    long_name = "A" * 80
    folder = services.create_folder(owner=owner, name=long_name)
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("home"))
    row = _recent_item(response.content.decode(), note.id)

    assert f"in {long_name}" in row


@pytest.mark.django_db
def test_home_recent_row_folder_location_is_not_a_link_or_filter_control():
    owner = create_account("home-location-no-link-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("home"))
    row = _recent_item(response.content.decode(), note.id)

    location_start = row.index('<span class="note-list__location">')
    location_end = row.index("</span>", location_start)
    location_markup = row[location_start:location_end]

    assert "<a " not in location_markup
    assert "href=" not in location_markup


@pytest.mark.django_db
def test_home_recent_module_folder_location_adds_no_query_per_note():
    # The dashboard caps at 5 recent results, so
    # query count must not scale with total note/folder count at all.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner = create_account("home-location-query-owner")
    for index in range(5):
        folder = services.create_folder(owner=owner, name=f"Folder {index}")
        note = services.create_note(owner=owner)
        services.assign_note_folder(note=note, folder=folder)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as baseline_queries:
        client.get(reverse("home"))
    baseline_count = len(baseline_queries.captured_queries)

    for index in range(10):
        folder = services.create_folder(owner=owner, name=f"More Folder {index}")
        note = services.create_note(owner=owner)
        services.assign_note_folder(note=note, folder=folder)

    with CaptureQueriesContext(connection) as scaled_queries:
        client.get(reverse("home"))
    scaled_count = len(scaled_queries.captured_queries)

    assert scaled_count <= baseline_count + 2
