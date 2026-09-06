"""Shared row/list foundation.

Home Recent Notes, All Notes, owner Trash, and Administrator Recovery now
render their rows through one shared `.note-list__*` anatomy (title-row
with an optional pin badge/type label, a secondary metadata line, and a
right-aligned actions region), rather than each surface maintaining its
own lookalike markup. These tests cover that shared contract.
Global search's own highlight/segment tests remain in
`core/static/core/src/global-search.test.ts`, unaffected
(no `global-search.ts` change is involved here).
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


def _recent_section(content: str) -> str:
    recent_start = content.index('class="home-recent"')
    return content[recent_start : content.index('id="workspace-drawer"')]


def _all_notes_panel_section(content: str) -> str:
    start = content.index('class="all-notes-panel"')
    return content[start : content.index('id="workspace-drawer"')]


def _note_item_section(content: str, note_id: int) -> str:
    # Captured from the row's own `<li` (not from the `data-note-id`
    # marker itself) so the earlier `class="note-list__item"` attribute
    # on the same opening tag is included, not truncated away.
    marker = f'data-note-id="{note_id}"'
    marker_index = content.index(marker)
    start = content.rindex("<li", 0, marker_index)
    end = content.index("</li>", start)
    return content[start:end]


def _app_css() -> str:
    return pathlib.Path("core/static/core/css/app.css").read_text()


# -- Home and All Notes share one anatomy ------------------------------------


@pytest.mark.django_db
def test_home_and_all_notes_render_the_same_row_classes_for_an_untagged_note():
    owner = create_account("shared-anatomy-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Shared Anatomy Note")

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    for shared_class in (
        'class="note-list__item"',
        'class="note-list__row"',
        'class="note-list__content"',
        'class="note-list__title-row"',
        'class="note-list__title"',
        'class="note-list__meta"',
        'class="note-list__timestamp"',
        'class="note-list__location"',
    ):
        assert shared_class in home_row, shared_class
        assert shared_class in all_notes_row, shared_class


@pytest.mark.django_db
def test_home_and_all_notes_both_render_an_actions_region():
    # Home Recent rows have
    # the same Pin/Move/overflow-menu actions All Notes rows have, via the
    # same shared partial -- see `test_home_actions.py` for the full
    # behavioral contract (which routes each action posts to, and why).
    owner = create_account("shared-actions-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Actions Region Note")

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    assert 'class="note-list__actions"' in home_row
    assert 'class="note-list__actions"' in all_notes_row


@pytest.mark.django_db
def test_home_and_all_notes_both_show_pin_badge_via_shared_class():
    owner = create_account("shared-pin-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Shared Pin Note")
    services.set_note_pinned(note=note, pinned=True)

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    badge = '<span class="note-list__pin-badge" role="img" aria-label="Pinned">★</span>'
    assert badge in home_row
    assert badge in all_notes_row


@pytest.mark.django_db
def test_home_and_all_notes_use_identical_tag_chip_markup():
    owner = create_account("shared-tag-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Shared Tag Note")
    tag = services.get_or_create_tag(owner=owner, name="Focus", color="green")
    services.assign_tag_to_note(note=note, tag=tag)

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    chip = (
        '<span class="note-list__tag-chip" data-tag-color="green">'
        '<span class="note-list__tag-chip__label">Focus</span></span>'
    )
    assert chip in home_row
    assert chip in all_notes_row


# -- Consistent typography (no browser-driven text scaling) -------------------


def test_html_resets_text_size_adjust_to_prevent_per_element_font_inflation():
    # Without this, a width-
    # constrained element's text (e.g. a bounded-`max-width` tag chip)
    # could render at a visibly different effective size than an
    # unconstrained sibling under a browser's own automatic text-
    # inflation heuristic -- text that shares one fixed `font-size`
    # declaration then looks like two different components. This is a
    # single, app-wide reset, not scoped to tag chips specifically.
    css = _app_css()
    rule_start = css.index("html {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    assert "text-size-adjust: 100%" in rule
    assert "-webkit-text-size-adjust: 100%" in rule


# -- Compact base height (wide/medium) ----------------------------------------


def test_note_list_item_uses_one_shared_compact_padding_token():
    # One shared class governs Home, All Notes, Trash, and Administrator
    # Recovery rows alike -- a tagged row and an untagged row necessarily
    # share this same base padding, since neither surface nor tag
    # presence changes which rule applies.
    css = _app_css()
    rule_start = css.index(".note-list__item {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    assert "padding: var(--space-4) 0" in rule


def test_tag_chip_has_bounded_width_and_does_not_clip_itself():
    css = _app_css()
    rule_start = css.index(".note-list__tag-chip {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    assert "max-width:" in rule
    # Without this, a chip could shrink
    # narrower than its own content/max-width as a flex item of
    # `.note-list__tags`, compressing its text instead of truncating or
    # wrapping to the next line.
    assert "flex-shrink: 0" in rule
    # The outer chip is the
    # tooltip's own positioning anchor (`position: relative`) and must
    # not itself clip content, or its own tooltip would be invisible --
    # ellipsis/overflow clipping lives on the inner label rule instead
    # (see the sibling test below), never here.
    assert "overflow: visible" in rule
    assert "position: relative" in rule


def test_tag_chip_label_is_the_element_that_actually_clips_and_ellipsizes():
    css = _app_css()
    rule_start = css.index(".note-list__tag-chip__label {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]

    assert "overflow: hidden" in rule
    assert "text-overflow: ellipsis" in rule
    assert "white-space: nowrap" in rule
    # A flex child's default `min-width: auto` would otherwise resist
    # shrinking below its own content width, defeating the ellipsis.
    assert "min-width: 0" in rule


def test_tag_chip_tooltip_reuses_the_shared_data_tooltip_mechanism():
    css = _app_css()
    assert ".note-list__tag-chip[data-tooltip]::after" in css
    rule_start = css.index(".note-list__tag-chip[data-tooltip]::after {")
    rule_end = css.index("}", rule_start)
    rule = css[rule_start:rule_end]
    assert "content: attr(data-tooltip)" in rule
    assert "position: absolute" in rule

    hover_start = css.index(".note-list__tag-chip[data-tooltip]:hover::after,")
    hover_end = css.index("}", hover_start)
    hover_rule = css[hover_start:hover_end]
    assert ":focus-visible::after" in hover_rule
    assert "opacity: 1" in hover_rule


@pytest.mark.django_db
def test_no_tag_chip_is_server_rendered_with_a_tooltip_or_tabindex():
    # A
    # character-count estimate of "will this truncate" was rejected as
    # unreliable. The server never guesses truncation for *any* chip,
    # long or short -- `tag-chip-truncation.ts` (see its own Vitest
    # coverage) decides this correctly after real browser layout, from
    # each chip's own `scrollWidth > clientWidth`, and is the only thing
    # that ever sets `data-tooltip`/`tabindex` on a chip.
    owner = create_account("tag-chip-server-render-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Tag Chip Server Render Note")
    short_tag = services.get_or_create_tag(owner=owner, name="Short", color="blue")
    long_tag = services.get_or_create_tag(
        owner=owner, name="A Genuinely Long Tag Name That Would Truncate", color="rose"
    )
    services.assign_tag_to_note(note=note, tag=short_tag)
    services.assign_tag_to_note(note=note, tag=long_tag)

    client = authenticated_client(owner)
    home_row = _note_item_section(
        _recent_section(client.get(reverse("home")).content.decode()), note.id
    )
    all_notes_row = _note_item_section(
        _all_notes_panel_section(client.get(reverse("notes:all_notes")).content.decode()),
        note.id,
    )

    for row in (home_row, all_notes_row):
        # Bounded from the tags region up to the actions region -- the
        # Pin/Move icon buttons in `.note-list__actions` legitimately
        # carry their own, unrelated `data-tooltip` attributes (e.g.
        # "Pin note"), which an unbounded row-wide check would wrongly
        # trip on.
        tags_start = row.index('class="note-list__tags"')
        actions_start = row.index('class="note-list__actions"')
        tags_markup = row[tags_start:actions_start]
        assert "data-tooltip" not in tags_markup
        assert "tabindex" not in tags_markup


@pytest.mark.django_db
def test_tagged_and_untagged_rows_use_the_same_item_and_row_classes():
    owner = create_account("tagged-untagged-owner")
    tagged = services.create_note(owner=owner)
    services.rename_note(note=tagged, title="Tagged Row Note")
    tag = services.get_or_create_tag(owner=owner, name="Focus", color="green")
    services.assign_tag_to_note(note=tagged, tag=tag)
    untagged = services.create_note(owner=owner)
    services.rename_note(note=untagged, title="Untagged Row Note")

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    panel = _all_notes_panel_section(response.content.decode())
    tagged_row = _note_item_section(panel, tagged.id)
    untagged_row = _note_item_section(panel, untagged.id)

    assert 'class="note-list__item"' in tagged_row
    assert 'class="note-list__item"' in untagged_row
    assert "note-list__tags" in tagged_row
    assert "note-list__tags" not in untagged_row


# -- Actions stay right-aligned, unaffected by title length -------------------


@pytest.mark.django_db
def test_long_title_does_not_displace_actions_region():
    owner = create_account("long-title-owner")
    long_title = "A very long note title " * 10
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title=long_title.strip())

    response = authenticated_client(owner).get(reverse("notes:all_notes"))
    content = response.content.decode()
    row = _note_item_section(_all_notes_panel_section(content), note.id)

    content_start = row.index('class="note-list__content"')
    actions_start = row.index('class="note-list__actions"')
    assert content_start < actions_start
    assert "note-list__pin-toggle" in row
    assert "note-list__move-action" in row


def test_actions_region_never_shrinks_and_content_region_may():
    # The narrow-only (indented, inside `@media`) override of the same
    # selector comes earlier in the file with different declarations --
    # anchored on a leading newline with no indentation to land on the
    # base (wide/medium) rule specifically, not that one.
    css = _app_css()
    content_rule_start = css.index("\n.note-list__content {") + 1
    content_rule_end = css.index("}", content_rule_start)
    content_rule = css[content_rule_start:content_rule_end]
    actions_rule_start = css.index("\n.note-list__actions {") + 1
    actions_rule_end = css.index("}", actions_rule_start)
    actions_rule = css[actions_rule_start:actions_rule_end]

    assert "min-width: 0" in content_rule
    assert "flex: 1 1 auto" in content_rule
    assert "flex-shrink: 0" in actions_rule


# -- Narrow layout -------------------------------------------------------------


def test_narrow_row_stacking_applies_to_the_shared_row_class_generally():
    # previously scoped to `.trash-item` only --
    # generalized so All Notes' own icon-button actions region gets the
    # same narrow-width stacking (no horizontal overflow) Trash/
    # Administrator Recovery already had. The narrow-only override is
    # nested inside `@media`, so it is indented -- distinguishing it
    # textually from the unindented base rule declared later in the
    # file, which must keep the exact opposite (wide/medium) behavior.
    css = _app_css()

    assert "  .note-list__row {\n    flex-wrap: wrap;" in css
    assert "  .note-list__content {\n    flex-basis: 100%;" in css
    assert "  .note-list__actions {\n    justify-content: flex-end;\n    width: 100%;" in css
    assert ".trash-item .note-list__row {" not in css
    assert ".trash-item .note-list__content {" not in css
    assert ".trash-item .note-list__actions {\n" not in css


# -- Trash retains all lifecycle fields and Restore ---------------------------


@pytest.mark.django_db
def test_trash_retains_all_lifecycle_fields_and_restore_after_row_unification():
    owner = create_account("trash-lifecycle-owner")
    folder = services.create_folder(owner=owner, name="Trash Lifecycle Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trash Lifecycle Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    for label in ("Moved to Trash", "Leaves your Trash", "Final purge"):
        assert content.count(f"<dt>{label}</dt>") == 2
    assert content.count("trash-item__lifecycle-entry--emphasis") == 2
    assert f'action="{reverse("notes:note_restore", args=[note.id])}"' in content
    assert f'data-restore-action="{reverse("notes:folder_restore", args=[folder.id])}"' in content


@pytest.mark.django_db
def test_trash_rows_show_shared_type_label():
    owner = create_account("trash-type-label-owner")
    folder = services.create_folder(owner=owner, name="Type Label Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Type Label Note")
    services.move_note_to_trash(note=note)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert '<span class="note-list__type">Note</span>' in content
    assert '<span class="note-list__type">Folder</span>' in content


# -- Administrator Recovery retains type labels, counts, lifecycle, Restore ---


@pytest.mark.django_db
def test_admin_recovery_retains_type_labels_counts_lifecycle_and_restore():
    owner = create_account("recovery-retain-owner")
    admin = create_admin("recovery-retain-admin")
    folder = services.create_folder(owner=owner, name="Recovery Retain Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Recovery Retain Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert '<span class="note-list__type">Note</span>' in content
    assert '<span class="note-list__type">Folder</span>' in content
    assert "Restores 0 note" in content
    for label in ("Moved to Trash", "Emptied", "Left owner Trash", "Final purge"):
        assert f"<dt>{label}</dt>" in content
    assert f'action="{reverse("notes:admin_note_restore", args=[note.id])}"' in content
    assert (
        f'data-restore-action="{reverse("notes:admin_folder_restore", args=[folder.id])}"'
        in content
    )
