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
from notes.models import Note
from notes.views import HOME_DASHBOARD_RECENT_LIMIT

PASSWORD = "LongUniquePassword123!"

RETURNING_USER_LEAD = (
    "A private, self-hosted scratch pad for basic and rich-text notes. "
    "Browse from the tree, search across everything, or continue with "
    "something recent."
)
ACTIVE_EMPTY_PRIMARY = (
    "RidgeNote is a private, self-hosted scratch pad for basic and "
    "rich-text notes. It is built for quickly writing down ideas, "
    "reminders, reference information, and other things you want to "
    "organize without the complexity of a full note-management suite."
)
ACTIVE_EMPTY_FEATURES = (
    "Create notes, organize them into folders, add tags, search their "
    "contents, and recover deleted items from Trash."
)
ACTIVE_EMPTY_SIMPLICITY_PREFIX = (
    "RidgeNote is intentionally simple. If you need advanced personal "
    "knowledge management, complex note relationships, scripting, "
    "attachments, revision history, or other extensive features, "
)
ACTIVE_EMPTY_SIMPLICITY_SUFFIX = " may be a better fit."
TRILIUM_URL = "https://triliumnotes.org/"


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


def _recent_section(content: str) -> str:
    recent_start = content.index('class="home-recent"')
    return content[recent_start : content.index('id="workspace-drawer"')]


# -- Shell ---------------------------------------------------------------


@pytest.mark.django_db
def test_home_renders_inside_workspace_shell_with_tree_rail_and_separator():
    owner = create_account("shell-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'class="workspace-shell workspace-shell--home"' in content
    assert 'class="workspace-shell__layout"' in content
    assert 'class="workspace-shell__tree-rail"' in content
    assert "data-tree-pane" in content
    assert "data-tree-separator" in content
    assert 'class="home-dashboard home-dashboard--populated"' in content


@pytest.mark.django_db
def test_home_narrow_drawer_still_present_and_unduplicated():
    owner = create_account("shell-drawer-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert content.count('id="workspace-drawer"') == 1
    # The tree-collapse
    # anti-flash script's own text also contains the substring
    # "data-tree-shell" (inside its `closest("[data-tree-shell]")` call), so
    # a bare substring count would over-count by one. Matching the real
    # attribute's own trailing `>` excludes that script-text occurrence.
    assert content.count("data-tree-shell>") == 1


@pytest.mark.django_db
def test_home_content_shell_workspace_class_applied():
    owner = create_account("shell-class-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "content-shell content-shell--workspace" in content


@pytest.mark.django_db
def test_note_detail_still_renders_shell_after_extraction():
    # Regression: extracting `_workspace_tree_rail.html` out of
    # detail.html must not change its rendered shell structure.
    owner = create_account("shell-detail-regression-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'class="workspace-shell"' in content
    assert 'class="workspace-shell__tree-rail"' in content
    assert "data-tree-separator" in content
    assert 'class="note-workspace"' in content


# -- Tree regressions (note=None Home context via the new wide rail) --------


@pytest.mark.django_db
def test_home_wide_tree_rail_folder_actions_target_home_context_routes():
    # The wide tree rail uses the same
    # `_tree_nav.html` partial the narrow drawer uses on Home
    # -- this confirms the shared shell extraction didn't change which
    # routes it resolves to (`_home`-suffixed, since `note` is None here).
    owner = create_account("tree-regression-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    wide_rail_section = content[
        content.index('class="workspace-shell__tree-rail"') : content.index('id="workspace-drawer"')
    ]

    assert f'action="{reverse("notes:folder_create_home")}"' in wide_rail_section
    assert f'action="{reverse("notes:folder_rename_home", args=[folder.id])}"' in wide_rail_section
    # The Delete link is a dialog-opening
    # button (`data-delete-trigger`) carrying its validated route/origin
    # as data attributes instead of an `href` -- confirm it targets
    # the right route and origin.
    assert (
        f'data-delete-action="{reverse("notes:folder_delete_home", args=[folder.id])}"'
        in wide_rail_section
    )
    assert 'data-delete-origin="home"' in wide_rail_section
    assert f'action="{reverse("notes:note_rename_home", args=[note.id])}"' in wide_rail_section
    assert f'action="{reverse("notes:note_move_home", args=[note.id])}"' in wide_rail_section
    assert "tree-nav__drag-handle" in wide_rail_section


# -- Dashboard panel structure ------------------------------------------------


@pytest.mark.django_db
def test_home_dashboard_is_one_containing_panel():
    # Welcome content, New Note, and the
    # Recent module all live inside the same single `.home-dashboard`
    # element, which is itself the bordered/rounded panel -- not
    # several nested cards and not a per-row box.
    owner = create_account("panel-structure-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    dashboard_start = content.index('class="home-dashboard home-dashboard--populated"')
    dashboard_tag_start = content.rfind("<section", 0, dashboard_start)
    dashboard_end = content.index("</section>", dashboard_start)
    # The dashboard's own closing tag is the *second* </section> after
    # its opening tag: the nested `.home-recent` region (also a
    # <section>) closes first.
    dashboard_end = content.index("</section>", dashboard_end + 1)
    panel = content[dashboard_tag_start:dashboard_end]

    assert "home-dashboard__intro" in panel
    assert "Welcome to RidgeNote" in panel
    assert "home-dashboard__new-note" in panel
    assert "home-recent__heading" in panel
    assert "home-recent__list" in panel or "home-recent__empty" in panel
    # No per-row rounded card class exists anywhere in the codebase's
    # Home output -- each row is a plain `<li>`, not its own panel.
    assert "home-recent__item-panel" not in panel


@pytest.mark.django_db
def test_home_dashboard_panel_uses_accepted_shared_geometry_tokens():
    owner = create_account("panel-tokens-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    assert response.status_code == 200

    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()
    rule_start = css.index(".home-dashboard {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    assert "var(--workspace-border-width)" in rule
    assert "var(--workspace-radius)" in rule
    assert "var(--color-bg-surface-muted)" in rule
    assert "var(--workspace-panel-padding)" in rule


def test_home_dashboard_wide_mode_rule_has_no_forced_growth_or_internal_scroll():
    # The wide-mode (`@media (width >=
    # 640px)`) `.home-dashboard` rule must not stretch to fill the row or
    # own an internal scrollbar. `.home-dashboard {` appears three times in
    # the file: the base rule, this wide-mode override (the one under
    # test), and a narrow-mode (`width < 640px`) override -- picked out by
    # position rather than `rindex`, since the narrow-mode rule is last
    # and trivially has none of the properties being asserted against.
    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()
    occurrences = []
    search_from = 0
    while True:
        idx = css.find(".home-dashboard {", search_from)
        if idx == -1:
            break
        occurrences.append(idx)
        search_from = idx + 1
    assert len(occurrences) == 3, occurrences

    rule_start = occurrences[1]
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    # `flex: 1 1 0` legitimately remains: in this flex *row*, it governs
    # horizontal fill (main axis), not vertical stretch (cross axis) --
    # `align-self: flex-start` is what opts the panel out of the row's
    # default vertical stretch, which is the property under test here.
    assert "overflow-y: auto" not in rule
    assert "overflow-y: hidden" not in rule
    assert "height: 100%" not in rule
    assert "align-self: flex-start" in rule


@pytest.mark.django_db
def test_home_content_shell_home_class_present_and_scoped():
    # `.content-shell--home` is what lets Home opt back out of the
    # note-detail-only viewport lock; it must be on Home's own shell and
    # must never leak onto note-detail's (a regression there would
    # silently change note-detail's editor-scrolling behavior).
    owner = create_account("content-shell-home-owner")
    note = services.create_note(owner=owner)

    home_content = authenticated_client(owner).get(reverse("home")).content.decode()
    assert "content-shell--home" in home_content

    detail_content = (
        authenticated_client(owner).get(reverse("notes:detail", args=[note.id])).content.decode()
    )
    assert "content-shell--home" not in detail_content


@pytest.mark.django_db
def test_home_dashboard_state_class_populated_when_recent_rows_exist():
    # An explicit, server-rendered state hook
    # (not a CSS selector inferring state from rendered copy) so the
    # populated state can get its own `min-height` behavior without
    # touching the empty state.
    owner = create_account("dashboard-state-populated-owner")
    services.create_note(owner=owner)

    content = authenticated_client(owner).get(reverse("home")).content.decode()

    assert "home-dashboard--populated" in content
    assert "home-dashboard--empty" not in content


@pytest.mark.django_db
def test_home_dashboard_state_class_empty_for_all_three_empty_states():
    brand_new_owner = create_account("dashboard-state-brand-new-owner")
    content = authenticated_client(brand_new_owner).get(reverse("home")).content.decode()
    assert "home-dashboard--empty" in content
    assert "home-dashboard--populated" not in content

    trashed_owner = create_account("dashboard-state-trashed-owner")
    trashed_note = services.create_note(owner=trashed_owner)
    services.rename_note(note=trashed_note, title="Trashed Note 1")
    services.move_note_to_trash(note=trashed_note)
    content = authenticated_client(trashed_owner).get(reverse("home")).content.decode()
    assert "home-dashboard--empty" in content
    assert "home-dashboard--populated" not in content


def test_home_dashboard_populated_wide_mode_reuses_tree_row_height_expression():
    # The populated dashboard must reuse the
    # exact same viewport-relative expression the tree pane's own
    # `max-height` uses, not a second independent formula.
    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()

    token_start = css.index("--workspace-home-row-height:")
    token_end = css.index(";", token_start)
    token_declaration = css[token_start:token_end]

    populated_start = css.index(".home-dashboard--populated {")
    populated_end = css.index("}", populated_start)
    populated_rule = css[populated_start:populated_end]

    tree_start = css.index(".workspace-shell--home .workspace-shell__tree-rail")
    tree_end = css.index("}", tree_start)
    tree_rule = css[tree_start:tree_end]

    assert "var(--workspace-home-row-height)" in populated_rule
    assert "var(--workspace-home-row-height)" in tree_rule
    assert "min-height:" in populated_rule
    # Normalize whitespace: prettier may wrap the `calc(...)` expression
    # across multiple lines.
    normalized_declaration = " ".join(token_declaration.split())
    assert "calc( 100dvh" in normalized_declaration or "calc(100dvh" in normalized_declaration


def test_home_row_height_formula_subtracts_real_header_height():
    # The formula must reuse the existing
    # `--workspace-header-height` custom property (measured by
    # `workspace-header-height.ts`) rather than only subtracting the
    # outer inset -- omitting the header is what left the shared bottom
    # edge ~64px below the visible viewport. No second, hard-coded
    # header-height literal should appear anywhere in this expression.
    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()
    token_start = css.index("--workspace-home-row-height:")
    token_end = css.index(";", token_start)
    normalized = " ".join(css[token_start:token_end].split())

    assert "var(--workspace-header-height" in normalized
    # The same fallback narrow-mode's ribbon offset already uses for this
    # property, not a newly-invented value.
    assert "3.1rem" in normalized


@pytest.mark.django_db
def test_home_default_header_carries_workspace_header_height_hook():
    # `workspace-header-height.ts` only measures `[data-workspace-header]`
    # -- without this attribute on Home's plain default header, the
    # measurement never runs there and the formula above silently falls
    # back to the static `3.1rem` guess on every Home page load.
    owner = create_account("home-header-hook-owner")
    content = authenticated_client(owner).get(reverse("home")).content.decode()

    header_start = content.index('class="app-header"')
    header_tag_end = content.index(">", header_start)
    header_tag = content[content.rfind("<header", 0, header_start) : header_tag_end]
    assert "data-workspace-header" in header_tag


@pytest.mark.django_db
def test_note_detail_header_hook_unaffected_by_home_header_change():
    # Regression: note-detail already had its own `data-workspace-header`
    # on its own distinct header before this pass -- confirms that
    # markup, and the editor's locked-viewport geometry it feeds, is
    # untouched.
    owner = create_account("detail-header-hook-owner")
    note = services.create_note(owner=owner)
    content = (
        authenticated_client(owner).get(reverse("notes:detail", args=[note.id])).content.decode()
    )

    assert 'class="app-header app-header--workspace" data-workspace-header' in content


def test_home_dashboard_empty_state_has_no_min_height_override():
    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()

    empty_start = css.index(".home-dashboard--empty {")
    empty_end = css.index("}", empty_start)
    empty_rule = css[empty_start:empty_end]

    assert "min-height:" not in empty_rule


@pytest.mark.django_db
def test_home_dashboard_intro_copy_has_max_width_wrapper():
    owner = create_account("intro-copy-wrapper-owner")
    services.create_note(owner=owner)

    content = authenticated_client(owner).get(reverse("home")).content.decode()
    assert 'class="home-dashboard__copy"' in content

    import pathlib

    css = pathlib.Path("core/static/core/css/app.css").read_text()
    rule_start = css.index(".home-dashboard__copy {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]
    assert "max-width" in rule
    assert "width:" not in rule.replace("max-width:", "")


def test_home_recent_list_and_new_note_are_not_inside_intro_copy_wrapper():
    # The `max-width` constraint must apply only to the explanatory
    # paragraphs -- New Note and the Recent module must stay full-width.
    import pathlib

    html = pathlib.Path("notes/templates/notes/home.html").read_text()
    copy_start = html.index('class="home-dashboard__copy"')
    copy_div_end = html.index("</div>", copy_start)
    copy_block = html[copy_start:copy_div_end]

    assert "home-dashboard__new-note" not in copy_block
    assert "home-recent" not in copy_block


# -- Dashboard content -----------------------------------------------------


@pytest.mark.django_db
def test_home_dashboard_has_prominent_new_note_action():
    owner = create_account("dashboard-new-note-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    create_url = reverse("notes:create")
    assert f'action="{create_url}"' in content
    assert ">New Note<" in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_home_dashboard_has_no_full_note_list_sort_form_or_recent_disclosure():
    owner = create_account("dashboard-removed-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'class="note-list"' not in content
    assert "home-sort-form" not in content
    assert "home-sort-select" not in content
    assert "note-recent-switcher" not in content


@pytest.mark.django_db
def test_home_dashboard_recent_module_has_pin_move_and_overflow_actions():
    # This module has read-only tag chips and a read-only pin
    # marker (see test_home_tags.py) with deliberately no
    # interactive controls for tags -- that restraint does not apply to
    # note actions:
    # Home Recent rows carry the same Pin/Move/overflow-menu controls
    # All Notes rows do, reusing the identical shared partial and the
    # tree's own already-established Home-context routes (see
    # test_home_actions.py for the full behavioral contract). Tag
    # assignment/removal remains absent here, same as it always has been
    # on All Notes -- this test confirms that boundary specifically.
    owner = create_account("dashboard-recent-actions-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)
    services.set_note_pinned(note=note, pinned=True)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    assert "note-list__pin-toggle" in recent_section
    assert "note-list__move-action" in recent_section
    assert "note-list__row-menu" in recent_section
    assert "aria-pressed" in recent_section
    assert "note-tags__assign-form" not in recent_section
    assert 'aria-label="Remove tag' not in recent_section


# -- Returning-user copy -------------------------------------------------


@pytest.mark.django_db
def test_home_returning_user_shows_compact_copy_no_trilium():
    owner = create_account("returning-user-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">Welcome to RidgeNote</h1>" in content
    assert RETURNING_USER_LEAD in content
    assert ACTIVE_EMPTY_PRIMARY not in content
    assert ACTIVE_EMPTY_FEATURES not in content
    assert ACTIVE_EMPTY_SIMPLICITY_PREFIX not in content
    assert "Trilium" not in content
    assert 'class="note-list__title"' in content


# -- Active-empty (brand-new and only-trashed) copy --------------------------


@pytest.mark.django_db
def test_home_active_empty_user_shows_expanded_identity_copy_and_trilium_link():
    owner = create_account("active-empty-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">Welcome to RidgeNote</h1>" in content
    assert ACTIVE_EMPTY_PRIMARY in content
    assert ACTIVE_EMPTY_FEATURES in content
    assert ACTIVE_EMPTY_SIMPLICITY_PREFIX in content
    assert ACTIVE_EMPTY_SIMPLICITY_SUFFIX in content
    assert RETURNING_USER_LEAD not in content

    trilium_start = content.index(f'href="{TRILIUM_URL}"')
    trilium_tag_end = content.index(">", trilium_start)
    trilium_tag = content[content.rfind("<a", 0, trilium_start) : trilium_tag_end]
    assert f'href="{TRILIUM_URL}"' in trilium_tag
    assert 'target="_blank"' in trilium_tag
    assert 'rel="noopener"' in trilium_tag
    assert ">Trilium Notes<" in content
    # The simplicity paragraph's prefix ends immediately before the
    # link, and its suffix begins immediately after -- confirms the
    # link sits inline inside that one sentence, not detached from it.
    assert content.index(ACTIVE_EMPTY_SIMPLICITY_PREFIX) < trilium_start
    assert trilium_start < content.index(ACTIVE_EMPTY_SIMPLICITY_SUFFIX)


@pytest.mark.django_db
def test_home_active_empty_trilium_link_absent_once_notes_exist():
    owner = create_account("active-empty-then-active-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Trilium" not in content
    assert TRILIUM_URL not in content


# -- Empty states -------------------------------------------------------------


@pytest.mark.django_db
def test_home_zero_notes_shows_no_notes_yet_with_guidance():
    owner = create_account("empty-zero-notes-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert ">New Note<" in content
    assert "No notes yet" in content
    assert (
        "Create your first note and start writing. "
        "You can organize it later as your scratch pad grows." in content
    )
    assert "No active notes" not in content


@pytest.mark.django_db
def test_home_only_trashed_notes_shows_no_active_notes_with_trash_link():
    owner = create_account("empty-only-trashed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Should not appear in recent")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "No active notes" in content
    assert "No notes yet" not in content
    assert "Should not appear in recent" not in content

    # The tree's own fixed Trash destination link
    # (earlier in the document than this guidance prose) also
    # `href`s to the same route. There is a further
    # "View My Trash" account-menu link -- once in the default header
    # (before this guidance prose) and once more in the narrow drawer's
    # own account footer, which renders *after* this guidance prose in
    # document order. Scoping to everything before the drawer excludes
    # that later match, so `rindex` within that scope still finds the
    # inline guidance prose link this test is actually about.
    trash_url = reverse("notes:trash")
    before_drawer = content[: content.index('id="workspace-drawer"')]
    trash_href_start = before_drawer.rindex(f'href="{trash_url}"')
    trash_tag_start = before_drawer.rfind("<a", 0, trash_href_start)
    trash_link_end = before_drawer.index("</a>", trash_href_start) + len("</a>")
    assert ">Trash<" in before_drawer[trash_tag_start:trash_link_end]
    # Active-empty copy (including Trilium) applies here too -- only-
    # trashed is one of the two active-empty cases, per decision 5.
    assert ACTIVE_EMPTY_PRIMARY in content
    assert "Trilium" in content
    # No dedicated Trash dashboard module -- just the one guidance link.
    assert "home-trash" not in content


@pytest.mark.django_db
def test_home_one_active_note_renders_recent_module_not_empty_state():
    owner = create_account("empty-one-note-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'class="note-list__title"' in content
    assert "No notes yet" not in content
    assert "No active notes" not in content


# -- Recent module semantics (10-item cap) ------------------------------------


@pytest.mark.django_db
def test_home_recent_module_caps_at_exactly_ten():
    owner = create_account("recent-cap-owner")
    notes = []
    for i in range(11):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i}")
        notes.append(note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    assert HOME_DASHBOARD_RECENT_LIMIT == 10
    assert recent_section.count('class="note-list__title"') == 10


@pytest.mark.django_db
def test_home_recent_module_eleventh_note_is_absent():
    owner = create_account("recent-eleventh-owner")
    now = timezone.now()
    notes = []
    for i in range(11):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Note {i}")
        Note.objects.filter(pk=note.pk).update(modified_at=now - timezone.timedelta(hours=i))
        notes.append(note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    # Note 10 has the oldest modified_at (10 hours ago) -- the 11th and
    # least-recent note, correctly excluded by the 10-item cap.
    assert ">Note 0<" in recent_section
    assert ">Note 9<" in recent_section
    assert ">Note 10<" not in recent_section


@pytest.mark.django_db
def test_home_recent_module_orders_newest_modified_first():
    owner = create_account("recent-order-owner")
    older = services.create_note(owner=owner)
    services.rename_note(note=older, title="Older note")
    newer = services.create_note(owner=owner)
    services.rename_note(note=newer, title="Newer note")
    Note.objects.filter(pk=older.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    assert recent_section.index(">Newer note<") < recent_section.index(">Older note<")


@pytest.mark.django_db
def test_home_recent_module_promotes_pinned_notes_first():
    # Home's Recent module deliberately promotes pinned notes --
    # ordering is pinned-first, then
    # most-recently-modified. This does not touch the *tree*'s own,
    # already-separate pinned-first behavior (never affected either
    # way), nor the note-detail Recent-notes switcher, which keeps its
    # own pure recency order unchanged (see test_recent_switcher.py).
    owner = create_account("recent-pin-promotion-owner")
    older_pinned = services.create_note(owner=owner)
    services.rename_note(note=older_pinned, title="Older pinned note")
    services.set_note_pinned(note=older_pinned, pinned=True)
    newer_unpinned = services.create_note(owner=owner)
    services.rename_note(note=newer_unpinned, title="Newer unpinned note")
    Note.objects.filter(pk=older_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    # The older *pinned* note now appears first despite being less
    # recently modified than the newer unpinned note.
    assert recent_section.index(">Older pinned note<") < recent_section.index(
        ">Newer unpinned note<"
    )


@pytest.mark.django_db
def test_home_recent_module_orders_multiple_pinned_notes_by_recency():
    owner = create_account("recent-pin-multi-owner")
    older_pinned = services.create_note(owner=owner)
    services.rename_note(note=older_pinned, title="Older pinned note")
    services.set_note_pinned(note=older_pinned, pinned=True)
    newer_pinned = services.create_note(owner=owner)
    services.rename_note(note=newer_pinned, title="Newer pinned note")
    services.set_note_pinned(note=newer_pinned, pinned=True)
    unpinned = services.create_note(owner=owner)
    services.rename_note(note=unpinned, title="Unpinned note")
    Note.objects.filter(pk=older_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=3)
    )
    Note.objects.filter(pk=newer_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    # Both pinned notes precede the unpinned note, and within the pinned
    # group the more recently modified one comes first.
    newer_pinned_pos = recent_section.index(">Newer pinned note<")
    older_pinned_pos = recent_section.index(">Older pinned note<")
    unpinned_pos = recent_section.index(">Unpinned note<")
    assert newer_pinned_pos < older_pinned_pos < unpinned_pos


@pytest.mark.django_db
def test_home_recent_module_orders_unpinned_notes_by_recency_then_id():
    owner = create_account("recent-unpinned-order-owner")
    older = services.create_note(owner=owner)
    services.rename_note(note=older, title="Older unpinned note")
    newer = services.create_note(owner=owner)
    services.rename_note(note=newer, title="Newer unpinned note")
    same_time = timezone.now() - timezone.timedelta(hours=1)
    Note.objects.filter(pk__in=[older.pk, newer.pk]).update(modified_at=same_time)
    assert newer.pk > older.pk

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    # Equal modified_at: the deterministic higher-id tie-break wins.
    assert recent_section.index(">Newer unpinned note<") < recent_section.index(
        ">Older unpinned note<"
    )


@pytest.mark.django_db
def test_home_recent_module_unpinning_moves_a_note_back_to_the_unpinned_group():
    owner = create_account("recent-unpin-move-owner")
    pinned_then_unpinned = services.create_note(owner=owner)
    services.rename_note(note=pinned_then_unpinned, title="Formerly pinned note")
    services.set_note_pinned(note=pinned_then_unpinned, pinned=True)
    newer_unpinned = services.create_note(owner=owner)
    services.rename_note(note=newer_unpinned, title="Newer unpinned note")
    Note.objects.filter(pk=pinned_then_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    services.set_note_pinned(note=pinned_then_unpinned, pinned=False)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    recent_section = _recent_section(content)

    # No longer pinned, so pure recency applies: the newer note wins.
    assert recent_section.index(">Newer unpinned note<") < recent_section.index(
        ">Formerly pinned note<"
    )


@pytest.mark.django_db
def test_home_recent_module_owner_scoped():
    owner = create_account("recent-owner-scope-owner")
    other = create_account("recent-owner-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Other Owner Note")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Other Owner Note" not in content


@pytest.mark.django_db
def test_home_recent_module_excludes_trashed_notes():
    owner = create_account("recent-trash-exclude-owner")
    trashed = services.create_note(owner=owner)
    services.rename_note(note=trashed, title="Trashed Note Title")
    services.move_note_to_trash(note=trashed)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Trashed Note Title" not in content


# -- Note-detail Recent popover regression (independent cap unchanged) -------


@pytest.mark.django_db
def test_note_detail_recent_popover_cap_unaffected_by_home_cap_change():
    owner = create_account("detail-recent-cap-owner")
    current = services.create_note(owner=owner)
    for i in range(12):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Detail note {i}")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher_start = content.index("data-note-recent-switcher")
    switcher_end = content.index("</details>", switcher_start)
    switcher = content[switcher_start:switcher_end]

    # Note-detail's own popover keeps its original, independent 8-item
    # cap -- the Home dashboard's cap change to 10 must not leak here.
    assert switcher.count("note-recent-switcher__link") == 8


# -- Query-count coverage ------------------------------------------------


@pytest.mark.django_db
def test_home_recent_module_query_count_does_not_scale_beyond_ten():
    owner = create_account("recent-query-scale-owner")
    for _ in range(10):
        services.create_note(owner=owner)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as baseline_queries:
        client.get(reverse("home"))
    baseline_count = len(baseline_queries.captured_queries)

    for _ in range(15):
        services.create_note(owner=owner)

    with CaptureQueriesContext(connection) as scaled_queries:
        client.get(reverse("home"))
    scaled_count = len(scaled_queries.captured_queries)

    assert scaled_count <= baseline_count + 2


@pytest.mark.django_db
def test_home_active_empty_detection_adds_no_per_note_query():
    # Active-empty detection (zero active notes -> an extra `.exists()`
    # check for trashed notes) must stay a fixed, small number of
    # queries regardless of how many trashed notes exist.
    owner = create_account("active-empty-query-owner")
    for _ in range(10):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title="Note 2")
        services.move_note_to_trash(note=note)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("home"))

    assert response.status_code == 200
    assert "No active notes" in response.content.decode()
    assert len(queries.captured_queries) <= 12


@pytest.mark.django_db
def test_home_active_user_incurs_no_trashed_note_query():
    # The `home_has_trashed_notes` check only ever runs on the
    # active-empty path -- confirmed indirectly by checking query count
    # stays low for a user who plainly has active notes (no wasted work
    # checking for trashed notes when it's already known to be
    # irrelevant).
    owner = create_account("active-user-no-trash-check-owner")
    services.create_note(owner=owner)

    client = authenticated_client(owner)
    with CaptureQueriesContext(connection) as queries:
        client.get(reverse("home"))

    assert len(queries.captured_queries) <= 10
