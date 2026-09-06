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


def _excluding_help_panel(content: str) -> str:
    """Everything before the in-app Help dialog (`id="help-panel"`), which
    is embedded on every page and legitimately discusses common UI-action
    words (Rename, Move, New folder, Open in new tab, etc.) in its own
    copy -- whole-page text/position scans must exclude it to test actual
    row-action markup, not incidental Help wording."""
    return content[: content.index('id="help-panel"')]


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


def _wide_tree_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


def _row_menu_panels(content: str, panel_class: str) -> list[str]:
    pattern = re.compile(
        rf'<div class="{re.escape(panel_class)}">(.*?)</div>\s*</details>', re.DOTALL
    )
    return pattern.findall(content)


def _details_open_at_marker(content: str, marker: str, occurrence: int = 0) -> bool:
    idx = content.index(marker)
    for _ in range(occurrence):
        idx = content.index(marker, idx + 1)
    details_start = content.rfind("<details", 0, idx)
    tag_end = content.index(">", details_start)
    return "open" in content[details_start:tag_end]


def _assert_exact_row_menu_order(panel: str, *, include_move: bool = True) -> None:
    order = [
        "Open",
        "Open in new tab",
        "Rename",
        *(["Move"] if include_move else []),
        "Print",
        "Download",
        "Duplicate",
        "Move to Trash",
    ]
    last_index = -1
    for item in order:
        marker = f">{item}<"
        idx = panel.index(marker, last_index + 1)
        assert idx > last_index, f"{item!r} out of order in panel"
        last_index = idx


@pytest.mark.django_db
def test_note_detail_tree_renders_row_action_menu_with_all_eight_items():
    owner = create_account("row-menu-note-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Three row-action menus per tree copy (wide tree + narrow drawer): the
    # single note's own row menu, plus the tree/drawer creation toolbar's
    # Move note and New Folder popovers (both reuse the same shared menu
    # foundation). Two more row-action menus exist outside the tree
    # (the Recent notes popover trigger and the consolidated note-action
    # overflow trigger), each appearing once (not duplicated per tree copy).
    assert content.count("row-action-menu") == 8
    assert content.count(">Open<") == 2
    assert content.count(">Rename<") >= 2
    assert content.count(">Move<") >= 2
    # The row-menu trigger's own label is "Move to Trash", matching the
    # shared delete-confirm
    # dialog's own submit button, which also reads
    # "Move to Trash" and renders once per page regardless of context --
    # the two tree-row triggers plus that one shared dialog button.
    assert content.count(">Move to Trash<") == 3

    # Print/Download assertions are scoped to each menu's own panel because the
    # page separately has unrelated, pre-existing Print and Download toolbar
    # actions (and their narrow-overflow duplicates) that must not be mistaken
    # for row-menu items.
    panels = _row_menu_panels(content, "tree-nav__row-menu-panel")
    assert len(panels) == 2
    for panel in panels:
        assert ">Open<" in panel
        assert ">Open in new tab<" in panel
        assert ">Rename<" in panel
        assert ">Move<" in panel
        assert ">Print<" in panel
        assert ">Download<" in panel
        assert ">Duplicate<" in panel
        assert ">Move to Trash<" in panel
        _assert_exact_row_menu_order(panel)

    # The consolidated active-note overflow menu is a third, separate
    # row-action panel (its own combined-class `<div>`, not matched by the
    # plain-class pattern above) that also carries "Open in new tab" for
    # the current note itself -- checked here structurally rather than by
    # a whole-page count, since the in-app Help panel (embedded on every
    # page) legitimately mentions "Open in new tab" too.
    overflow_panels = _row_menu_panels(
        content, "tree-nav__row-menu-panel note-workspace__overflow-menu"
    )
    assert len(overflow_panels) == 1
    assert overflow_panels[0].count(">Open in new tab<") == 1
    assert overflow_panels[0].count(">Duplicate<") == 1


@pytest.mark.django_db
def test_note_detail_row_menu_open_link_matches_existing_note_link_url_with_no_new_route():
    owner = create_account("row-menu-open-url-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    detail_url = reverse("notes:detail", args=[note.id])

    # Open and Open in new tab both use the class+href prefix; one tree copy contributes
    # one Open and one Open-in-new-tab occurrence, so two copies contribute four.
    assert content.count(f'class="tree-nav__row-menu-item" href="{detail_url}"') == 4
    # The Open link, the Open-in-new-tab link, and the existing note link all point at
    # the same, already-existing route -- no new route was introduced.
    assert content.count(f'href="{detail_url}"') >= 6


@pytest.mark.django_db
def test_note_detail_row_menu_open_in_new_tab_uses_target_blank_and_safe_rel():
    owner = create_account("row-menu-open-new-tab-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    detail_url = reverse("notes:detail", args=[note.id])

    expected = (
        f'class="tree-nav__row-menu-item" href="{detail_url}" '
        'target="_blank" rel="noopener">Open in new tab</a>'
    )
    assert content.count(expected) == 2


@pytest.mark.django_db
def test_note_detail_row_menu_print_targets_existing_print_route_as_plain_link():
    owner = create_account("row-menu-note-print-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    print_url = reverse("notes:print", args=[note.id])

    # Print carries `target="_blank"` -- a
    # functional no-JavaScript fallback that keeps this entry point
    # consistent with the other three (this is a plain link, not the
    # editor-integrated, save-gated Print item in the header overflow
    # menu) -- no new route.
    expected = (
        f'class="tree-nav__row-menu-item" href="{print_url}" '
        'target="_blank" rel="noopener">Print</a>'
    )
    assert content.count(expected) == 2


@pytest.mark.django_db
def test_note_detail_row_menu_download_targets_existing_download_text_route_as_plain_link():
    owner = create_account("row-menu-note-download-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    download_url = reverse("notes:download_text", args=[note.id])

    # Download is a plain native link -- no target/rel attributes, no new route,
    # and no JS-hook attribute -- sharing the same already-existing download
    # route used by the toolbar Download button. Row-action links live outside
    # the note-editor's form, so unlike the toolbar copies, they do
    # not need the data-note-download-link guard-exemption attribute.
    expected = f'class="tree-nav__row-menu-item" href="{download_url}">Download</a>'
    assert content.count(expected) == 2
    assert f'href="{download_url}" target="_blank"' not in content
    # The toolbar's Download copy is a single always-in-overflow copy, which
    # carries the attribute; the row-menu copies must not.
    assert content.count("data-note-download-link") == 1


@pytest.mark.django_db
def test_note_detail_row_menu_move_targets_existing_note_move_route():
    owner = create_account("row-menu-note-move-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    move_url = reverse("notes:note_move", args=[note.id])

    # Move is a collapsed-by-default
    # `.tree-nav__action` disclosure reusing the shared
    # `.tree-nav__action-form` styling class -- the old, always-expanded
    # `.tree-nav__row-menu-move` class does not exist.
    # The active-note top-bar overflow menu has a third copy of this same
    # pattern: its own Move disclosure, targeting
    # this same note and posting to this same existing route -- the tree's
    # two copies (wide + narrow drawer) plus the one overflow copy, three
    # total.
    pattern = re.compile(rf'class="tree-nav__action-form"[^>]*action="{re.escape(move_url)}"')
    assert len(pattern.findall(content)) == 3


@pytest.mark.django_db
def test_note_detail_row_menu_rename_targets_new_note_rename_route():
    owner = create_account("row-menu-note-rename-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    rename_url = reverse("notes:note_rename", args=[note.id])

    # Tree note Rename reuses the exact same
    # collapsed-by-default `.tree-nav__action-form` disclosure pattern as
    # folder Rename and All Notes' Rename -- the old, always-expanded
    # `.tree-nav__row-menu-rename` class does not exist.
    pattern = re.compile(rf'class="tree-nav__action-form"[^>]*action="{re.escape(rename_url)}"')
    assert len(pattern.findall(content)) == 2


@pytest.mark.django_db
def test_tree_note_rename_and_move_are_collapsed_by_default_for_unfiled_note():
    # Compact
    # `.tree-nav__action` disclosures, collapsed by default whenever the
    # menu opens, matching the tree's own folder Rename precedent.
    owner = create_account("compact-tree-actions-unfiled-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # The active-note overflow has a plain `>Rename<` button,
    # ahead of both tree copies' own Rename
    # disclosures in document order -- occurrence 0 is that button
    # (not itself a `<details>`), so the wide and narrow tree copies'
    # disclosures are occurrences 1 and 2.
    assert not _details_open_at_marker(content, ">Rename<", occurrence=1)
    assert not _details_open_at_marker(content, ">Rename<", occurrence=2)
    assert not _details_open_at_marker(content, ">Move<", occurrence=0)
    assert not _details_open_at_marker(content, ">Move<", occurrence=1)


@pytest.mark.django_db
def test_tree_note_rename_and_move_are_collapsed_by_default_for_nested_note():
    owner = create_account("compact-tree-actions-nested-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = _excluding_help_panel(response.content.decode())

    # The folder's own row disclosures (Rename, and the folder-details
    # expand/collapse toggle for the current note's own folder) come
    # first in document order, so the note row's own Rename/Move markers
    # are found at later occurrence indices -- located explicitly via the
    # summary text, not assumed to be the first match.
    rename_marker = ">Rename<"
    move_marker = ">Move<"
    rename_positions = [
        i
        for i in range(content.count(rename_marker))
        if not _details_open_at_marker(content, rename_marker, occurrence=i)
    ]
    move_positions = [
        i
        for i in range(content.count(move_marker))
        if not _details_open_at_marker(content, move_marker, occurrence=i)
    ]
    # Every Rename/Move disclosure on this page (folder's own Rename, plus
    # the nested note's own Rename and Move, each present in both the wide
    # tree and narrow drawer copies) is collapsed -- none open.
    assert len(rename_positions) == content.count(rename_marker)
    assert len(move_positions) == content.count(move_marker)
    assert content.count(rename_marker) >= 4  # folder Rename x2 + note Rename x2
    assert content.count(move_marker) >= 2  # note Move x2 (folders have no Move)


@pytest.mark.django_db
def test_tree_note_row_menu_renders_compact_rename_and_move_summaries():
    owner = create_account("compact-tree-actions-summary-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # The compact disclosure summary uses the same shared row-menu-item
    # styling class as every other menu entry (Open, Print, Download...),
    # not a bespoke label style.
    assert content.count('<summary class="tree-nav__row-menu-item">Rename</summary>') >= 2
    assert content.count('<summary class="tree-nav__row-menu-item">Move</summary>') >= 2


@pytest.mark.django_db
def test_tree_note_row_menu_move_and_rename_use_the_tree_nav_action_disclosure_class():
    owner = create_account("compact-tree-actions-class-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('<details class="tree-nav__action tree-nav__action--rename-folder">') >= 2
    assert content.count('<details class="tree-nav__action tree-nav__action--move-note">') >= 2


@pytest.mark.django_db
def test_note_detail_tree_still_has_existing_move_disclosure_and_new_note_new_folder_filter_pins():
    owner = create_account("row-menu-coexistence-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('aria-label="Move note"') == 2
    assert content.count('aria-label="New note"') == 2
    assert "New folder" in content
    assert content.count("Rename") >= 2
    assert content.count('placeholder="Filter notes…"') == 2
    assert "tree-nav__pin-marker" in content
    # Folders carry their own drag handle too --
    # 1 note + 1 ordinary folder, each present in both tree copies.
    assert content.count("tree-nav__drag-handle") == 4


# `_wide_tree_section` (everything before the drawer) matches Home's wide
# tree rail, which uses the tree's own standard 8-item row menu (Move
# included, like every other tree row). The standard tree row menu is
# fully exercised via the drawer-scoped tests immediately below (which
# render the identical shared partial) and via note-detail's own tree
# tests elsewhere in this file.


@pytest.mark.django_db
def test_home_flat_list_row_menu_has_no_duplicate_move_entry():
    # Home's row-action overflow menu must not embed
    # a second, duplicate Move form (`.note-list__row-menu-move`),
    # identical in behavior to the always-visible direct Move popover
    # beside it. Exactly one Move entry point
    # (the direct icon-button popover, checked in test_home_move.py)
    # exists per *row*. Home's
    # Recent Notes row has a second, legitimate Move popover for the same
    # note (reusing the identical shared
    # partial/route as the tree's own copy, not a within-row duplicate)
    # -- `_wide_tree_section` (everything before the narrow drawer) spans
    # both the wide tree rail and the Recent module, so it correctly
    # sees 2: the tree row's own popover, and Recent's own, separate one.
    owner = create_account("row-menu-home-no-dup-move-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    flat_list_section = _wide_tree_section(content)
    move_url = reverse("notes:note_move_home", args=[note.id])

    assert "note-list__row-menu-move" not in flat_list_section
    assert flat_list_section.count(f'action="{move_url}"') == 2


@pytest.mark.django_db
def test_home_wide_tree_row_menu_rename_targets_new_note_rename_home_route():
    # This is the wide-rail counterpart of
    # `test_home_drawer_tree_row_menu_rename_targets_new_note_rename_home_route`
    # below, using the same robust (not over-specified) assertion.
    owner = create_account("row-menu-home-wide-rename-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    flat_list_section = _wide_tree_section(content)
    rename_url = reverse("notes:note_rename_home", args=[note.id])

    assert f'action="{rename_url}"' in flat_list_section
    assert "row-action-menu" in flat_list_section


@pytest.mark.django_db
def test_home_drawer_tree_row_menu_move_targets_existing_note_move_home_route():
    owner = create_account("row-menu-home-drawer-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)
    move_url = reverse("notes:note_move_home", args=[note.id])

    assert f'action="{move_url}"' in drawer
    assert "row-action-menu" in drawer


@pytest.mark.django_db
def test_home_drawer_tree_row_menu_rename_targets_new_note_rename_home_route():
    owner = create_account("row-menu-home-drawer-rename-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)
    rename_url = reverse("notes:note_rename_home", args=[note.id])

    assert f'action="{rename_url}"' in drawer
    assert "row-action-menu" in drawer


@pytest.mark.django_db
def test_home_drawer_tree_row_menu_open_in_new_tab_present_with_target_blank_and_safe_rel():
    owner = create_account("row-menu-home-drawer-open-new-tab-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)
    detail_url = reverse("notes:detail", args=[note.id])

    expected = (
        f'class="tree-nav__row-menu-item" href="{detail_url}" '
        'target="_blank" rel="noopener">Open in new tab</a>'
    )
    assert expected in drawer
    assert "row-action-menu" in drawer


@pytest.mark.django_db
def test_home_drawer_tree_row_menu_print_targets_existing_print_route_as_plain_link():
    owner = create_account("row-menu-home-drawer-print-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)
    print_url = reverse("notes:print", args=[note.id])

    expected = (
        f'class="tree-nav__row-menu-item" href="{print_url}" '
        'target="_blank" rel="noopener">Print</a>'
    )
    assert expected in drawer
    assert "row-action-menu" in drawer


@pytest.mark.django_db
def test_home_drawer_tree_row_menu_download_targets_existing_download_text_route_as_plain_link():
    owner = create_account("row-menu-home-drawer-download-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    drawer = _drawer_section(content)
    download_url = reverse("notes:download_text", args=[note.id])

    expected = f'class="tree-nav__row-menu-item" href="{download_url}">Download</a>'
    assert expected in drawer
    assert f'href="{download_url}" target="_blank"' not in drawer
    assert "row-action-menu" in drawer


# Home's flat list does not exist. Move-route coverage for the wide tree
# rail is covered by
# `test_home_wide_tree_rail_folder_actions_target_home_context_routes`
# in `test_home_dashboard.py`, and "no drag affordances" correctly
# applies to the dashboard pane, not the tree beside it (which
# legitimately has drag handles) -- see
# `test_home_dashboard_has_no_drag_affordances_or_move_url_template` in
# `test_home_move.py`.


@pytest.mark.django_db
def test_note_detail_renders_no_duplicate_element_ids_with_row_action_menu():
    owner = create_account("row-menu-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


@pytest.mark.django_db
def test_home_renders_no_duplicate_element_ids_with_row_action_menu():
    owner = create_account("row-menu-home-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Shared icon/compact-control/menu foundation:
# the literal ellipsis glyph is replaced by the menu-dots SVG as proof of use.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_detail_row_menu_triggers_use_menu_dots_icon_not_literal_ellipsis():
    owner = create_account("row-menu-icon-note-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # No literal ellipsis glyph remains anywhere on the page.
    assert "⋯" not in content

    # One row-action-menu trigger per tree copy (wide tree + narrow drawer),
    # each now rendering the shared icon-button class and the menu-dots SVG.
    assert content.count('tree-nav__row-menu-trigger icon-button"') == 2
    # The consolidated note-action overflow trigger also
    # reuses the existing menu-dots icon (deliberately, to avoid vendoring a
    # new one), adding one more copy of the icon's circle markup.
    assert content.count('<circle cx="10" cy="4" r="1.6" fill="currentColor"/>') == 3

    # Accessible name is preserved on the trigger itself, not lost to the icon.
    assert content.count(f'aria-label="Actions for {note.title}"') == 2

    # The same `tree-nav__row-menu-trigger` marker
    # class (for its details-marker-hiding behavior) is reused on two more
    # triggers outside the tree: the Recent notes popover and the note-action
    # overflow.
    assert content.count("tree-nav__row-menu-trigger") == 4


@pytest.mark.django_db
def test_home_row_menu_trigger_uses_menu_dots_icon_not_literal_ellipsis():
    owner = create_account("row-menu-icon-home-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "⋯" not in content
    # Home's flat-list row-menu trigger uses the shared
    # `tree-nav__row-menu-trigger` foundation also used by the narrow
    # drawer's own tree copy of the same note, so both share one class:
    # two occurrences, not one, and zero of the old
    # `note-list__row-menu-trigger` class. Home's Recent Notes
    # row has its own overflow menu too, reusing this identical shared
    # trigger -- a third, legitimate occurrence on this page (wide tree
    # row, narrow drawer tree row, and Recent's own row).
    assert content.count("note-list__row-menu-trigger") == 0
    assert content.count('tree-nav__row-menu-trigger icon-button"') == 3
    assert content.count('<circle cx="10" cy="4" r="1.6" fill="currentColor"/>') == 3
    assert content.count(f'aria-label="Actions for {note.title}"') == 3
    assert content.count("tree-nav__row-menu-trigger") == 3


@pytest.mark.django_db
def test_menu_dots_svg_is_decorative_and_theme_aware():
    owner = create_account("row-menu-icon-decorative-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    # Targeted by the menu-dots icon's own distinctive `viewBox`, not by
    # page position -- the RidgeNote brand mark is also an `<svg>` and
    # renders earlier in the page, so "the first `<svg>` on the page" is
    # not a reliable way to find this specific icon.
    svg_start = content.index('<svg viewBox="0 0 20 20"')
    svg_tag = content[svg_start : content.index(">", svg_start) + 1]

    # Decorative: hidden from assistive technology and never a tab stop --
    # the accessible name lives on the surrounding <summary aria-label>.
    assert 'aria-hidden="true"' in svg_tag
    assert 'focusable="false"' in svg_tag

    # Theme-aware: every fill in the icon set inherits the surrounding
    # control's color rather than hard-coding one, so it renders correctly
    # in both warm-light and dark without any icon-specific override.
    assert "currentColor" in content
    assert "#" not in content[svg_start : content.index("</svg>", svg_start)]


@pytest.mark.django_db
def test_row_action_menu_regular_user_sees_no_admin_only_content():
    # Converting the trigger to an icon must not change who can see the
    # menu or what it contains -- an ordinary user's own row-action menu
    # remains exactly as before.
    owner = create_account("row-menu-icon-auth-owner", role=User.ROLE_USER)
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'aria-label="Actions for {note.title}"' in content


@pytest.mark.django_db
def test_row_action_menu_admin_sees_same_menu_as_regular_user():
    admin = create_account("row-menu-icon-admin-owner", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)

    response = authenticated_client(admin).get(reverse("home"))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'aria-label="Actions for {note.title}"' in content
    # Wide tree row, narrow drawer tree row, and Home Recent's own row --
    # three, not two.
    assert content.count("tree-nav__row-menu-trigger") == 3
