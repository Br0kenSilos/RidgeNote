"""Workspace shell and navigation foundation.

The collapsed rail is narrowed to exactly one control -- reveal/hide the
note tree -- with no Trash icon, no vertical "Tree" text, and no second
navigation system of its own. The expanded tree keeps its existing fixed
Trash destination at the bottom (click opens Trash, drop moves a
note/folder to Trash) as the sole Trash affordance this navigation
surface offers. The user menu gains "View My Trash" for every
authenticated user and renames the administrator-only recovery link to
"Global Trash Recovery". Note/folder destructive controls
read "Move to Trash", not "Delete".
"""

import pathlib

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


def _app_css() -> str:
    return pathlib.Path("core/static/core/css/app.css").read_text()


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


def _excluding_help_panel(content: str) -> str:
    """Everything before the in-app Help dialog (`id="help-panel"`), which
    is embedded on every page and legitimately discusses common UI-action
    words (View My Trash, Delete, etc.) in its own copy -- whole-page text
    scans must exclude it to test actual navigation/menu markup, not
    incidental Help wording."""
    return content[: content.index('id="help-panel"')]


def _account_menu_dropdown_html(content: str) -> str:
    start = content.index('class="account-menu__dropdown"')
    end = content.index("</div>", start)
    return content[start:end]


def _matching_div_end(content: str, div_open_index: int) -> int:
    """Index of the `</div>` that closes the `<div ...>` whose own opening
    `<` is at `div_open_index`, correctly accounting for `<div` elements
    nested inside it (e.g. the account menu's theme-selector group) rather
    than stopping at the first `</div>` encountered."""
    depth = 0
    pos = div_open_index
    while True:
        next_open = content.find("<div", pos)
        next_close = content.index("</div>", pos)
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + len("<div")
        else:
            depth -= 1
            if depth == 0:
                return next_close
            pos = next_close + len("</div>")


def _drawer_account_html(content: str) -> str:
    div_start = content.rindex("<div", 0, content.index('class="workspace-drawer__account"'))
    end = _matching_div_end(content, div_start)
    return content[div_start:end]


# -- Collapsed rail: reveal control only, no Trash icon -------------------------


@pytest.mark.django_db
def test_collapsed_rail_default_state_is_a_real_keyboard_accessible_button():
    owner = create_account("rail-button-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    rail_start = content.index('class="workspace-shell__tree-rail"')
    rail_end = content.index("</div>", rail_start)
    rail = content[rail_start:rail_end]

    assert '<button type="button"' in rail
    assert "data-tree-toggle" in rail
    # No vertical "Tree" text label.
    assert ">Tree<" not in rail
    # The double-chevron icon is inlined SVG (two <path> elements), not a
    # filename reference.
    assert rail.count("<path") == 2


@pytest.mark.django_db
def test_collapsed_rail_default_label_matches_its_default_expanded_state():
    # The server always renders the tree expanded by default (JS starts
    # `collapsed = false` unless a stored preference says otherwise), so
    # the static markup's label/tooltip must say "Hide", not "Show" --
    # `tree-shell.ts` corrects this client-side if a stored preference
    # says the tree should start collapsed.
    owner = create_account("rail-label-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    rail_start = content.index('class="workspace-shell__tree-rail"')
    rail_end = content.index("</div>", rail_start)
    rail = content[rail_start:rail_end]

    assert 'aria-label="Hide note tree"' in rail
    assert 'data-tooltip="Hide note tree"' in rail
    assert 'aria-expanded="true"' in rail


@pytest.mark.django_db
def test_collapsed_rail_has_no_folder_note_home_or_other_action_icons():
    owner = create_account("rail-no-actions-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    rail_start = content.index('class="workspace-shell__tree-rail"')
    rail_end = content.index("</div>", rail_start)
    rail = content[rail_start:rail_end]

    # Exactly one child element (the toggle button) -- no second control.
    assert rail.count("<button") == 1
    assert rail.count("<a ") == 0
    assert "folder-new" not in rail
    assert "note-new" not in rail


# -- Tree-collapse ---
# -- anti-flash bootstrap ----------------------------------------------------

ANTI_FLASH_SCRIPT = (
    "<script>(function(){try{var shell=document.currentScript.closest("
    '"[data-tree-shell]");if(shell&&window.localStorage.getItem('
    '"ridgenote.tree.collapsed")==="true"){shell.setAttribute('
    '"data-tree-collapsed","true");}}catch(e){}})();</script>'
)


@pytest.mark.django_db
def test_note_detail_renders_the_tree_collapse_anti_flash_script_before_the_tree_pane():
    owner = create_account("anti-flash-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count(ANTI_FLASH_SCRIPT) == 1
    script_pos = content.index(ANTI_FLASH_SCRIPT)
    tree_pane_pos = content.index("data-tree-pane")
    tree_shell_pos = content.index("data-tree-shell")
    assert tree_shell_pos < script_pos < tree_pane_pos


@pytest.mark.django_db
def test_home_and_all_notes_and_trash_all_render_the_tree_collapse_anti_flash_script():
    owner = create_account("anti-flash-pages-owner")
    services.create_note(owner=owner)

    for url_name in ("home", "notes:all_notes", "notes:trash"):
        response = authenticated_client(owner).get(reverse(url_name))
        content = response.content.decode()
        assert content.count(ANTI_FLASH_SCRIPT) == 1, url_name


@pytest.mark.django_db
def test_anti_flash_script_reads_the_same_storage_key_tree_shell_ts_persists_to():
    # tree-shell.ts's own TREE_COLLAPSED_STORAGE_KEY constant; kept in sync
    # manually since this script is inline template markup, not compiled
    # TypeScript that could import the constant directly.
    ts_source = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "core"
        / "static"
        / "core"
        / "src"
        / "tree-shell.ts"
    ).read_text()
    assert 'TREE_COLLAPSED_STORAGE_KEY = "ridgenote.tree.collapsed"' in ts_source
    assert '"ridgenote.tree.collapsed"' in ANTI_FLASH_SCRIPT


# -- Tree scroll --
# -- position restore anti-flash bootstrap -----------------------------------

SCROLL_ANTI_FLASH_SCRIPT = (
    "<script>(function(){try{var viewport=document.currentScript."
    "parentElement;var container=document.currentScript.closest("
    '".tree-nav");if(!container||!viewport){return;}var key='
    '"ridgenote.tree.scrollTop."+(container.getAttribute('
    '"data-tree-scope")==="drawer"?"drawer":"wide");var raw=window.'
    'sessionStorage.getItem(key);var value=(raw===null||raw.trim()==="")'
    "?NaN:Number(raw);if(Number.isFinite(value)&&value>=0){viewport."
    "scrollTop=value;}}catch(e){}})();</script>"
)


@pytest.mark.django_db
def test_note_detail_renders_the_scroll_restore_script_once_per_tree_copy():
    owner = create_account("scroll-anti-flash-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Once for the wide desktop tree, once for the narrow drawer -- both
    # render `_tree_nav.html`, and the script text itself is identical for
    # both copies (it reads `data-tree-scope` at runtime, not at render
    # time).
    assert content.count(SCROLL_ANTI_FLASH_SCRIPT) == 2


@pytest.mark.django_db
def test_home_and_all_notes_and_trash_all_render_the_scroll_restore_script_twice():
    owner = create_account("scroll-anti-flash-pages-owner")
    services.create_note(owner=owner)

    for url_name in ("home", "notes:all_notes", "notes:trash"):
        response = authenticated_client(owner).get(reverse(url_name))
        content = response.content.decode()
        assert content.count(SCROLL_ANTI_FLASH_SCRIPT) == 2, url_name


@pytest.mark.django_db
def test_scroll_restore_script_is_the_last_child_of_the_viewport_not_before_it():
    # A synchronous script placed immediately *before* `.tree-nav__viewport`
    # cannot find it via `nextElementSibling` -- the parser hasn't reached
    # that markup yet at the moment a non-deferred script executes. This
    # script must instead be the LAST element *inside* the viewport, so
    # `document.currentScript.parentElement` (available immediately, since
    # the parent's own opening tag was already parsed) resolves correctly.
    owner = create_account("scroll-anti-flash-sibling-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Immediately followed by the viewport's own closing tag -- confirms
    # placement as the last child, not a preceding sibling.
    combined = SCROLL_ANTI_FLASH_SCRIPT + "\n  </div>"
    assert content.count(combined) == 2

    # And nowhere does the old (buggy) sibling-based placement remain.
    assert content.count(SCROLL_ANTI_FLASH_SCRIPT + '\n  <div class="tree-nav__viewport">') == 0


@pytest.mark.django_db
def test_scroll_restore_script_sits_inside_a_tree_nav_with_a_data_tree_scope_attribute():
    owner = create_account("scroll-anti-flash-scope-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    script_pos = content.index(SCROLL_ANTI_FLASH_SCRIPT)
    scope_pos = content.rindex("data-tree-scope", 0, script_pos)
    tree_nav_pos = content.rindex('class="tree-nav"', 0, script_pos)
    viewport_pos = content.rindex('class="tree-nav__viewport"', 0, script_pos)
    assert tree_nav_pos < scope_pos < viewport_pos < script_pos


@pytest.mark.django_db
def test_scroll_restore_script_reads_the_same_storage_keys_tree_scroll_position_ts_persists_to():
    # tree-scroll-position.ts's own storage-key constants; kept in sync
    # manually since this script is inline template markup, not compiled
    # TypeScript that could import the constants directly.
    ts_source = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "core"
        / "static"
        / "core"
        / "src"
        / "tree-scroll-position.ts"
    ).read_text()
    assert 'TREE_SCROLL_STORAGE_KEY_WIDE = "ridgenote.tree.scrollTop.wide"' in ts_source
    assert 'TREE_SCROLL_STORAGE_KEY_DRAWER = "ridgenote.tree.scrollTop.drawer"' in ts_source
    assert "ridgenote.tree.scrollTop." in SCROLL_ANTI_FLASH_SCRIPT


# -- Expanded tree Trash destination ---------------------------------------------


@pytest.mark.django_db
def test_expanded_tree_trash_destination_opens_trash_with_explicit_label():
    owner = create_account("tree-trash-open-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('aria-label="Open Trash"') == 2
    assert content.count('data-tooltip="Open Trash"') == 2
    assert content.count('href="/trash/"') >= 2
    # The visible link text stays "Trash" (the destination's own name).
    assert content.count('<span class="tree-nav__label">Trash</span>') == 2


@pytest.mark.django_db
def test_expanded_tree_trash_destination_still_carries_the_drop_target_marker():
    owner = create_account("tree-trash-drop-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("data-tree-trash-target") == 2


# -- User menu wording ------------------------------------------------------------


@pytest.mark.django_db
def test_admin_account_menu_shows_global_trash_recovery_then_view_my_trash():
    admin = create_admin("menu-admin-user")
    services.create_note(owner=admin)

    response = authenticated_client(admin).get(reverse("home"))
    dropdown = _account_menu_dropdown_html(response.content.decode())

    assert dropdown.count(">Global Trash Recovery<") == 1
    assert dropdown.count(">Trash Recovery<") == 0
    assert dropdown.count(">View My Trash<") == 1
    assert dropdown.index(">Global Trash Recovery<") < dropdown.index(">View My Trash<")
    trash_url = reverse("notes:trash")
    recovery_url = reverse("notes:admin_recovery")
    assert f'href="{trash_url}"' in dropdown
    assert f'href="{recovery_url}"' in dropdown


@pytest.mark.django_db
def test_regular_account_menu_shows_view_my_trash_only():
    user = create_account("menu-regular-user")
    services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("home"))
    dropdown = _account_menu_dropdown_html(response.content.decode())

    assert ">View My Trash<" in dropdown
    assert "Global Trash Recovery" not in dropdown
    assert ">Admin<" not in dropdown
    trash_url = reverse("notes:trash")
    assert f'href="{trash_url}"' in dropdown


@pytest.mark.django_db
def test_drawer_account_menu_matches_default_header_wording_for_admin():
    admin = create_admin("drawer-menu-admin-user")
    services.create_note(owner=admin)

    response = authenticated_client(admin).get(reverse("home"))
    drawer_account = _drawer_account_html(response.content.decode())

    assert ">Global Trash Recovery<" in drawer_account
    assert ">View My Trash<" in drawer_account
    assert drawer_account.index(">Global Trash Recovery<") < drawer_account.index(">View My Trash<")


@pytest.mark.django_db
def test_drawer_account_menu_matches_default_header_wording_for_regular_user():
    user = create_account("drawer-menu-regular-user")
    services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("home"))
    drawer_account = _drawer_account_html(response.content.decode())

    assert ">View My Trash<" in drawer_account
    assert "Global Trash Recovery" not in drawer_account


@pytest.mark.django_db
def test_note_detail_account_menu_also_shows_view_my_trash_for_admin():
    # note-detail's own header carries a third, independent copy of the
    # account menu (base.html and the default header are two others) --
    # asserted separately so it cannot be silently satisfied by them.
    admin = create_admin("detail-menu-admin-user")
    note = services.create_note(owner=admin)

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))
    dropdown = _account_menu_dropdown_html(response.content.decode())

    assert ">Global Trash Recovery<" in dropdown
    assert ">View My Trash<" in dropdown


# -- No duplicate/ambiguous Trash controls ---------------------------------------


@pytest.mark.django_db
def test_only_one_trash_entry_point_family_exists_per_page():
    # Exactly: the tree's own fixed Trash destination (once per tree
    # copy -- wide + narrow drawer) plus the user menu's "View My Trash"
    # entry (once per account-menu copy -- default header + drawer
    # footer). No collapsed-rail icon, no global-header icon, no
    # top-bar link.
    #
    # Verified directly (not assumed): the in-app Help panel's own Trash
    # & Recovery topic also mentions "View My Trash" in its prose (inside
    # a <strong> tag, not an actual link) -- excluded here via
    # `_excluding_help_panel()` so this test counts only the real
    # account-menu entry points, still exactly two.
    owner = create_account("only-one-trash-family-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = _excluding_help_panel(response.content.decode())

    assert content.count("data-tree-trash-target") == 2
    assert content.count(">View My Trash<") == 2
    assert "data-tree-rail-trash" not in content
    assert "workspace-header__trash-link" not in content


# -- Destructive-control wording ---------------------------------------------------


@pytest.mark.django_db
def test_note_row_menu_delete_trigger_says_move_to_trash_everywhere():
    owner = create_account("note-wording-owner")
    note = services.create_note(owner=owner)

    # The in-app Help panel (embedded on every page) explains *tag*
    # deletion in its own copy, which also contains the literal ">Delete<"
    # substring -- unrelated to note/folder deletion wording, and excluded
    # here so this test checks actual row/menu markup only.
    detail_content = _excluding_help_panel(
        authenticated_client(owner).get(reverse("notes:detail", args=[note.id])).content.decode()
    )
    home_content = _excluding_help_panel(
        authenticated_client(owner).get(reverse("home")).content.decode()
    )
    all_notes_content = _excluding_help_panel(
        authenticated_client(owner).get(reverse("notes:all_notes")).content.decode()
    )

    assert ">Delete<" not in detail_content
    assert ">Delete<" not in home_content
    assert ">Delete<" not in all_notes_content
    assert ">Move to Trash<" in detail_content
    assert ">Move to Trash<" in home_content
    assert ">Move to Trash<" in all_notes_content


@pytest.mark.django_db
def test_folder_row_menu_delete_trigger_already_says_move_to_trash():
    owner = create_account("folder-wording-owner")
    services.create_folder(owner=owner, name="Wording Folder")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">Move to Trash<" in content


@pytest.mark.django_db
def test_quick_trash_and_delete_confirm_dialog_labels_are_unchanged_and_explicit():
    owner = create_account("dialog-wording-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'aria-label="Move current note to Trash"' in content
    assert 'data-tooltip="Move current note to Trash"' in content
    # The shared delete-confirm dialog's title text is filled in
    # client-side (`delete-confirm.ts`); this only confirms the dialog
    # markup itself, and its Move-to-Trash submit button, are present.
    assert 'id="delete-confirm-dialog"' in content
    assert ">Move to Trash</button>" in content


# -- Narrower collapsed rail ----------------------


@pytest.mark.django_db
def test_collapsed_rail_toggle_stays_a_real_keyboard_accessible_button_after_narrowing():
    # The width reduction must not touch the toggle's own accessibility
    # contract -- still a real, enabled, tab-reachable <button>.
    owner = create_account("rail-narrow-keyboard-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    rail_start = content.index('class="workspace-shell__tree-rail"')
    rail_end = content.index("</div>", rail_start)
    rail = content[rail_start:rail_end]

    assert '<button type="button"' in rail
    assert "disabled" not in rail
    assert 'tabindex="-1"' not in rail
    assert 'aria-label="Hide note tree"' in rail


def test_rail_width_uses_one_shared_narrow_token_not_the_old_wider_literal():
    css = _app_css()

    # One source of truth for the rail's width: 1.5rem (24px), the
    # documented lower bound of an already-viable range, not a regression --
    # 1.75rem and 2.7rem are older, wider literals. No CSS declaration
    # uses either (a mention inside an explanatory comment doesn't count).
    assert "--workspace-rail-width: 1.5rem;" in css
    assert "min-width: 1.75rem;" not in css
    assert "flex: 0 0 1.75rem;" not in css
    assert "min-width: 2.7rem;" not in css
    assert "flex: 0 0 2.7rem;" not in css


def test_rail_toggle_fills_the_whole_rail_with_the_icon_visually_pinned_to_the_top():
    # A compact-square toggle would
    # leave the rest of the rail as dead, unclickable space -- the button
    # is the whole rail (one single interactive control, no nested
    # competing elements), with only the *icon's own* alignment (not the
    # button's size) keeping it visually pinned near the top.
    css = _app_css()

    toggle_start = css.index(".workspace-shell__tree-toggle {")
    toggle_end = css.index("}", toggle_start)
    toggle_rule = css[toggle_start:toggle_end]
    assert "flex: 1 1 auto;" in toggle_rule
    assert "align-items: flex-start;" in toggle_rule
    assert "padding-top: var(--space-6);" in toggle_rule
    assert "aspect-ratio" not in toggle_rule


def test_rail_does_not_clip_its_own_tooltip():
    # `overflow: hidden` on the rail would
    # silently clip the toggle's own tooltip pseudo-element, the
    # same structural bug already fixed for tag chips.
    css = _app_css()

    rail_start = css.index(".workspace-shell__tree-rail {")
    rail_end = css.index("}", rail_start)
    assert "overflow: visible;" in css[rail_start:rail_end]
    assert "overflow: hidden;" not in css[rail_start:rail_end]

    toggle_start = css.index(".workspace-shell__tree-toggle {")
    toggle_end = css.index("}", toggle_start)
    # The toggle carries its own matching radius now, since it can no
    # longer rely on the rail's own (removed) clipping to look rounded.
    assert "border-radius: var(--radius-md);" in css[toggle_start:toggle_end]


def test_rail_tooltip_is_positioned_beside_the_visually_pinned_icon():
    # The toggle fills the rail's *entire* height (the whole rail is the
    # click target), so the generic above/below tooltip positioning
    # (relative to the trigger's own top/bottom edges) would misplace a
    # "below" tooltip far down the tree pane. A side-positioned override
    # anchored to the icon's own fixed top offset (not a percentage of
    # the button's full height) sidesteps this, and must win over both
    # the plain and the JS-driven "below" variant of the generic rule.
    css = _app_css()

    override_start = css.index(".workspace-shell__tree-toggle[data-tooltip]::after,")
    override_end = css.index("}", override_start)
    override_rule = css[override_start:override_end]

    assert (
        '.workspace-shell__tree-toggle[data-tooltip][data-tooltip-placement="below"]::after'
        in override_rule
    )
    assert "left: 100%;" in override_rule
    assert "transform: translateY(-50%);" in override_rule
    # A fixed offset (matching the icon's own padding-top plus half its
    # height), not a percentage of the full-height button.
    assert "top: calc(var(--space-6) + 0.5rem);" in override_rule

    rail_flex_index = css.index("flex: 0 0 var(--workspace-rail-width);")
    assert rail_flex_index > 0


# -- Compact Trash Restore footer -----------------


@pytest.mark.django_db
def test_trash_note_row_places_final_purge_and_restore_in_one_footer():
    owner = create_account("footer-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Footer Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    footer_start = content.index('<div class="trash-item__footer">')
    footer = content[footer_start : footer_start + 900]

    assert "<dt>Final purge</dt>" in footer
    assert "trash-item__lifecycle-entry--emphasis" in footer
    assert '<div class="note-list__actions">' in footer
    assert ">Restore</button>" in footer
    # The two quieter dates stay in their own dl, outside this footer.
    assert "<dt>Moved to Trash</dt>" not in footer
    assert "<dt>Leaves your Trash</dt>" not in footer


@pytest.mark.django_db
def test_trash_folder_row_places_final_purge_and_restore_in_one_footer():
    owner = create_account("footer-folder-owner")
    folder = services.create_folder(owner=owner, name="Footer Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    footer_start = content.index('<div class="trash-item__footer">')
    footer = content[footer_start : footer_start + 900]

    assert "<dt>Final purge</dt>" in footer
    assert "data-restore-trigger" in footer
    assert 'data-restore-kind="folder"' in footer


@pytest.mark.django_db
def test_admin_recovery_note_and_folder_rows_use_the_same_footer_structure():
    owner = create_account("admin-footer-owner")
    admin = create_admin("admin-footer-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Admin Footer Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Admin Footer Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert content.count('<div class="trash-item__footer">') == 2
    footer_note_start = content.index('<div class="trash-item__footer">')
    footer_note = content[footer_note_start : footer_note_start + 900]
    assert "<dt>Final purge</dt>" in footer_note
    assert ">Restore</button>" in footer_note


def test_trash_item_footer_css_wraps_cleanly_and_keeps_final_purge_emphasized():
    css = _app_css()

    footer_start = css.index(".trash-item__footer {")
    footer_end = css.index("}", footer_start)
    footer_rule = css[footer_start:footer_end]

    assert "display: flex" in footer_rule
    assert "flex-wrap: wrap" in footer_rule
    assert "justify-content: space-between" in footer_rule
    # No border/padding-top here
    # -- that would read as a second, unwanted separator splitting one Trash/
    # Recovery item into two visual halves. Only `.note-list__item`'s
    # own row-to-row border divides one item from the next.
    assert "border-top" not in footer_rule
    assert "padding-top" not in footer_rule
    # Even a small margin-top on
    # this footer would read as an unwanted blank-line-
    # sized gap -- no top margin at all, relying purely on each
    # entry's own line-height for breathing room.
    assert "margin-top: 0;" in footer_rule

    # The narrow-width `.note-list__actions { width: 100%; }` rule (used
    # by Home/All Notes' own row-level actions region) is cancelled for
    # this nested footer context -- the footer's own flex-wrap governs
    # narrow stacking instead, so Restore never forces a redundant
    # full-width line inside it.
    override_start = css.index(".trash-item__footer .note-list__actions {")
    override_end = css.index("}", override_start)
    assert "width: auto;" in css[override_start:override_end]


# -- Trash panel top-alignment with the tree -----


def test_trash_panel_top_margin_matches_the_tree_panes_own_inset():
    # The tree pane and rail both get `margin-top: var(--workspace-
    # outer-inset)` at wide width; `.trash-panel` (Trash's own content
    # column, a flex sibling of the rail in the same row) needs the
    # exact same top inset to align its top edge with theirs -- it
    # already had a matching `margin-bottom`, so this fixes an
    # asymmetry, not a wholly new concern.
    css = _app_css()

    trash_panel_start = css.index(".trash-panel {")
    trash_panel_end = css.index("}", trash_panel_start)
    trash_panel_rule = css[trash_panel_start:trash_panel_end]

    assert "margin-top: var(--workspace-outer-inset);" in trash_panel_rule
    assert "margin-bottom: var(--workspace-outer-inset);" in trash_panel_rule


def test_admin_recovery_has_no_workspace_shell_to_align_with():
    # Administrator Recovery is a standalone admin page with no tree
    # rail/`workspace-shell` -- the Trash top-alignment fix above does
    # not apply there, and must not be silently introduced.
    content = pathlib.Path("notes/templates/notes/admin_recovery.html").read_text()

    assert "workspace-shell" not in content
    assert "_workspace_tree_rail" not in content


# -- Home tooltip buried under the tree toolbar --


def test_home_and_all_notes_rail_outranks_the_sticky_tree_stacking_context():
    # `position: sticky` (used only on Home/All Notes) makes the rail
    # and tree two *separate* sibling stacking contexts; with neither
    # given an explicit z-index they stack by DOM order, and the tree
    # (containing the tree-toolbar action box) comes after the rail --
    # burying the whole rail, including the toggle's own tooltip,
    # beneath it. A z-index on the rail alone (not the tree) reorders
    # only these two sibling contexts.
    css = _app_css()

    sticky_rule_start = css.index(".workspace-shell--home .workspace-shell__tree-rail,")
    sticky_rule_end = css.index("}", sticky_rule_start)
    assert "position: sticky;" in css[sticky_rule_start:sticky_rule_end]

    zindex_rule_start = css.index(
        ".workspace-shell--home .workspace-shell__tree-rail,",
        sticky_rule_end,
    )
    zindex_rule_end = css.index("}", zindex_rule_start)
    zindex_rule = css[zindex_rule_start:zindex_rule_end]

    assert "z-index: 1;" in zindex_rule
    # Scoped to the rail only -- the tree itself must not also be raised,
    # or the two would tie again.
    assert ".workspace-shell__tree {" not in zindex_rule
    assert ".workspace-shell--home .workspace-shell__tree,\n" not in zindex_rule
