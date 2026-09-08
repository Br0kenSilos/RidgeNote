import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from notes import documents, services
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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


@pytest.mark.django_db
def test_create_note_uses_canonical_defaults():
    user = create_account("note-owner")

    note = services.create_note(owner=user)

    assert note.owner == user
    assert note.body_json == documents.EMPTY_DOCUMENT
    assert note.body_plain_text == ""
    assert note.editor_schema_version == 1
    assert note.version == 1
    assert note.title == documents.generated_title_for_timestamp(note.created_at)


@pytest.mark.django_db
def test_blank_title_restores_stable_generated_title():
    user = create_account("title-owner")
    note = services.create_note(owner=user)
    original_title = note.title

    services.save_note(
        note=note, title="Named note", body_json=documents.EMPTY_DOCUMENT, version=note.version
    )
    services.save_note(
        note=note, title="   ", body_json=documents.EMPTY_DOCUMENT, version=note.version
    )

    note.refresh_from_db()
    assert note.title == original_title


def test_validate_canonical_document_rejects_unsupported_node():
    with pytest.raises(ValidationError):
        documents.validate_canonical_document({"type": "doc", "content": [{"type": "blockquote"}]})


def test_validate_canonical_document_accepts_hard_break_nodes():
    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Line one"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Line two"},
                ],
            }
        ],
    }

    assert documents.validate_canonical_document(document) == document


def test_validate_link_href_rejects_forbidden_schemes():
    for href in ("javascript:alert(1)", "data:text/plain,hi", "file:///tmp/x", "//example.com"):
        with pytest.raises(ValidationError):
            documents.validate_link_href(href)


def test_validate_link_href_rejects_malformed_bracketed_host_as_validation_error():
    """A malformed authority component beginning
    with `[` (not valid IPv6 bracket notation) previously escaped as a
    raw `ValueError` from `urlsplit()` -- confirmed directly against
    this repository's Python 3.13 runtime -- rather than RidgeNote's
    normal `ValidationError` path every other malformed-link case here
    already uses."""
    with pytest.raises(ValidationError):
        documents.validate_link_href("https://[not-valid")


def test_validate_link_href_malformed_bracketed_host_never_raises_raw_value_error():
    try:
        documents.validate_link_href("https://[not-valid")
    except ValidationError:
        pass
    except ValueError as exc:  # pragma: no cover - the exact regression this guards against
        pytest.fail(f"raw ValueError leaked instead of ValidationError: {exc!r}")


def test_validate_canonical_document_handles_malformed_link_href_as_validation_error():
    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "broken link",
                        "marks": [
                            {"type": "link", "attrs": {"href": "https://[not-valid"}}
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        documents.validate_canonical_document(document)


def test_plain_text_derivation_preserves_expected_line_breaks():
    document = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Heading"}],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Paragraph"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Second line"},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Item one"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Item two"}]}
                        ],
                    },
                ],
            },
        ],
    }

    assert documents.derive_plain_text(document) == (
        "Heading\n\nParagraph\nSecond line\n\nItem one\n\nItem two"
    )


def test_render_document_html_outputs_safe_line_breaks():
    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Line one"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Line two"},
                ],
            }
        ],
    }

    assert str(documents.render_document_html(document)) == "<p>Line one<br>Line two</p>"


@pytest.mark.django_db
def test_authenticated_home_shows_owner_notes_and_empty_state():
    # converged Home onto the persistent-tree
    # workspace shell with a minimal dashboard; "Your Notes" was the
    # prior standalone page's heading, replaced by the dashboard's own
    # welcome heading.
    user = create_account("home-user")
    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    assert b"Welcome to RidgeNote" in response.content
    assert b"No notes yet" in response.content


@pytest.mark.django_db
def test_authenticated_home_lists_only_current_users_notes():
    owner = create_account("owner")
    other = create_account("other")
    owner_note = services.create_note(owner=owner)
    other_note = services.create_note(owner=other)

    response = authenticated_client(owner).get(reverse("home"))

    assert response.status_code == 200
    assert owner_note.title.encode() in response.content
    other_note_url = reverse("notes:detail", args=[other_note.id])
    assert other_note_url.encode() not in response.content


@pytest.mark.django_db
def test_note_detail_uses_workspace_header_variant():
    user = create_account("workspace-shell-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content

    assert response.status_code == 200
    assert b"app-header app-header--workspace" in content
    assert b"data-workspace-header" in content
    assert b"workspace-drawer-trigger" in content
    assert b"data-drawer-toggle" in content
    assert b"workspace-header__spacer" not in content
    assert b"brand-link--workspace" not in content

    left_start = content.index(b'account-nav--left note-workspace-titlebar"')
    center_start = content.index(b'account-nav--center"')
    right_start = content.index(b'account-nav--right"')
    assert left_start < center_start < right_start

    left_zone = content[left_start:center_start]
    center_zone = content[center_start:right_start]
    right_zone = content[right_start:]

    assert b">Home<" in left_zone
    assert b"brand-mark" in center_zone
    assert b"RidgeNote</a>" in center_zone
    assert b'class="account-menu"' in right_zone
    assert b"data-help-toggle" in right_zone
    assert right_zone.index(b'class="account-menu"') < right_zone.index(b"data-help-toggle")

    assert b'aria-label="Workspace navigation"' in content
    assert b"data-workspace-ribbon" in content

    # toolbar convergence: Home, the quick-trash
    # action, Recent notes, and the note-action overflow all live in this
    # one title-bar action group now, left-to-right in that order, instead
    # of three separate command surfaces. replaced
    # the old "View Trash" navigation link here with the quick-trash
    # action (Trash itself moved into the tree as a fixed destination).
    home_pos = left_zone.index(b">Home<")
    trash_pos = left_zone.index(b'aria-label="Move current note to Trash"')
    recent_pos = left_zone.index(b'aria-label="Recent notes"')
    overflow_pos = left_zone.index(b'aria-label="More note actions"')
    assert home_pos < trash_pos < recent_pos < overflow_pos

    # Neither trigger is duplicated anywhere else on the page.
    assert content.count(b'aria-label="Recent notes"') == 1
    assert content.count(b'aria-label="More note actions"') == 1


@pytest.mark.django_db
def test_note_detail_quick_trash_carries_narrow_visibility_class_and_unchanged_form():
    # quick-trash gained an additional
    # `note-workspace-titlebar__quick-trash` class (alongside its existing
    # `note-workspace-titlebar__nav-item`) so a narrowly scoped CSS override
    # can keep it visible below 640px, independent of Home's own hide rule.
    # Everything about the form itself -- route, method, CSRF, hidden
    # fields, confirmation-relevant markup -- must remain byte-for-byte
    # unchanged.
    owner = create_account("quick-trash-narrow-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    form_start = content.index('action="' + reverse("notes:note_delete", args=[note.id]) + '"')
    form_open_start = content.rindex("<form", 0, form_start)
    form_end = content.index("</form>", form_start)
    quick_trash_form = content[form_open_start:form_end]

    expected_class = (
        "inline-form note-workspace-titlebar__nav-item note-workspace-titlebar__quick-trash"
    )
    assert f'class="{expected_class}"' in quick_trash_form
    assert 'method="post"' in quick_trash_form
    assert "csrfmiddlewaretoken" in quick_trash_form
    assert '<input type="hidden" name="origin" value="note_detail">' in quick_trash_form
    assert f'<input type="hidden" name="current_note" value="{note.id}">' in quick_trash_form
    assert 'aria-label="Move current note to Trash"' in quick_trash_form
    assert 'data-tooltip="Move current note to Trash"' in quick_trash_form
    assert 'type="submit"' in quick_trash_form


@pytest.mark.django_db
def test_note_detail_heading_order_places_the_page_h1_before_the_tags_h2():
    # both headings are `.visually-hidden`, so this
    # is a screen-reader navigation fix only, not a visual one -- the
    # page-level `<h1 id="note-title-heading">` must precede the Tags
    # `<h2>` in DOM/reading order, matching the section's own
    # `aria-labelledby="note-title-heading"`.
    owner = create_account("heading-order-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("<h1") == 1
    assert content.count('<h1 id="note-title-heading" class="visually-hidden">Edit note</h1>') == 1
    assert content.count('<h2 class="visually-hidden">Tags</h2>') == 1
    assert content.count('id="note-title-heading"') == 1
    assert content.count('aria-labelledby="note-title-heading"') == 1

    h1_pos = content.index('<h1 id="note-title-heading"')
    # Not a generic "<h2" search: `base.html` also renders (closed,
    # inert-to-assistive-tech) `<dialog>` elements containing their own
    # `<h2>`s (Search, Help, delete/restore confirm) earlier in the page,
    # which must not be conflated with the note-workspace's own Tags
    # heading -- match the Tags heading specifically instead.
    tags_h2_pos = content.index('<h2 class="visually-hidden">Tags</h2>')
    assert h1_pos < tags_h2_pos

    section_marker = '<section class="note-workspace" aria-labelledby="note-title-heading">'
    section_pos = content.index(section_marker)
    assert section_pos < h1_pos < tags_h2_pos

    # The h1 no longer lives inside the note form's ribbon.
    form_pos = content.index('class="note-form note-workspace__form"')
    assert h1_pos < form_pos


@pytest.mark.django_db
def test_note_detail_save_status_is_inside_note_context():
    user = create_account("save-status-context-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    content = response.content.decode()
    note_context_pos = content.find('class="note-context"')
    save_status_pos = content.find("data-note-save-status")
    actions_pos = content.find('class="note-workspace__actions"')
    assert note_context_pos != -1
    assert save_status_pos != -1
    assert actions_pos != -1
    assert note_context_pos < save_status_pos < actions_pos


@pytest.mark.django_db
def test_note_detail_uses_a_single_consolidated_overflow_for_note_actions():
    # A single always-present overflow menu covers
    # Print/Export/Download/Duplicate/Open in new tab, at every width, in
    # place of a direct-link-plus-responsive-overflow-duplicate pattern.
    # A compact Move disclosure sits at the front of
    # this same menu, closing the gap where Move would otherwise be
    # unreachable from note-detail whenever the tree pane is collapsed/hidden.
    # New note and New folder disclosures sit ahead of Move,
    # closing the same reachability gap for note/folder creation.
    user = create_account("consolidated-toolbar-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    content = response.content.decode()
    overflow_menu_pattern = re.compile(
        r'<div class="tree-nav__row-menu-panel note-workspace__overflow-menu">'
        r"(.*?)</div>\s*</details>",
        re.DOTALL,
    )
    match = overflow_menu_pattern.search(content)
    assert match is not None
    overflow_menu = match.group(1)

    assert content.count("data-note-print-link") == 1
    assert content.count("data-note-export-link") == 1
    assert content.count("data-note-download-link") == 1
    assert ">New note<" in overflow_menu
    assert ">New folder<" in overflow_menu
    assert ">Rename<" in overflow_menu
    assert ">Move<" in overflow_menu
    assert ">Print<" in overflow_menu
    assert ">Export JSON<" in overflow_menu
    assert ">Download<" in overflow_menu
    assert ">Duplicate<" in overflow_menu
    assert ">Open in new tab<" in overflow_menu

    # New note, then New folder, then Rename, then Move, then every other
    # overflow action -- Rename is ordered immediately before Move.
    assert (
        overflow_menu.index(">New note<")
        < overflow_menu.index(">New folder<")
        < overflow_menu.index(">Rename<")
        < overflow_menu.index(">Move<")
        < overflow_menu.index(">Print<")
    )

    duplicate_form_action = reverse("notes:note_duplicate", args=[note.id])
    assert f'action="{duplicate_form_action}"' in content
    assert 'form="note-duplicate-form"' in content
    # Duplicate remains a POST-backed <button type="submit" form="...">
    # referencing the standalone hidden form outside the editor form -- not
    # converted to a GET link. Its own markup shares the exact same
    # `tree-nav__row-menu-item` presentation class as every sibling <a>, so
    # alignment/padding/height/typography/hover/focus treatment are
    # identical regardless of the underlying element (a `justify-content`
    # fix ensures this class' `<button>` centers its label while `<a>`
    # siblings stay left-aligned).
    assert 'type="submit" form="note-duplicate-form"' in overflow_menu
    assert 'class="tree-nav__row-menu-item">Duplicate</button>' in overflow_menu
    # Exactly three real <form> elements now exist in
    # this menu -- New note's, New folder's, and Move's own collapsed-by-
    # default disclosure forms, all reusing the
    # `.tree-nav__action`/`.tree-nav__action-form` pattern and placed
    # outside the note-editor form (no nested form is introduced, since
    # this menu itself was never inside that form). Duplicate is still not
    # a real nested <form> -- it stays a `form=""`-referencing submit
    # button, unchanged.
    assert overflow_menu.count("<form") == 3

    move_url = reverse("notes:note_move", args=[note.id])
    assert '<details class="tree-nav__action tree-nav__action--move-note">' in overflow_menu
    assert '<summary class="tree-nav__row-menu-item">Move</summary>' in overflow_menu
    assert f'action="{move_url}"' in overflow_menu
    assert 'class="tree-nav__action-form"' in overflow_menu

    new_tab_href = reverse("notes:detail", args=[note.id])
    assert f'href="{new_tab_href}"' in overflow_menu
    assert 'target="_blank"' in overflow_menu
    assert 'rel="noopener"' in overflow_menu

    # No leftover responsive-duplication classes/markup from the old design.
    assert "note-workspace__action--print-direct" not in content
    assert "note-workspace__action--export-direct" not in content
    assert "note-workspace__action--download-direct" not in content
    assert "note-workspace__overflow-action--print" not in content
    assert "note-workspace__overflow-action--export" not in content
    assert "note-workspace__overflow-action--download" not in content


@pytest.mark.django_db
def test_note_detail_overflow_rename_reuses_the_existing_title_input_only():
    # Rename is a plain button that focuses/selects
    # the existing autosave-backed title input -- no new form, field, or
    # route. A discrete Rename action is required for the active note;
    # every other note-listing surface
    # (tree/Home/All Notes rows) already has one via a separate
    # `<details>`/prefilled-input/submit pattern posting to
    # `notes:note_rename` -- the active note deliberately does not reuse
    # that pattern, since it would create a second, separately-submitted
    # title-editing surface alongside the always-live autosave field.
    user = create_account("overflow-rename-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    overflow_menu_pattern = re.compile(
        r'<div class="tree-nav__row-menu-panel note-workspace__overflow-menu">'
        r"(.*?)</div>\s*</details>",
        re.DOTALL,
    )
    overflow_menu = overflow_menu_pattern.search(content).group(1)

    rename_button = (
        '<button type="button" class="tree-nav__row-menu-item" '
        "data-note-rename-trigger>Rename</button>"
    )
    assert content.count(rename_button) == 1
    assert overflow_menu.count(">Rename<") == 1

    # Not a form-backed or route-backed action within the overflow itself:
    # no new POST target, no `notes:note_rename` reference, no nested
    # `<form>` added for Rename. (The tree pane elsewhere on this same
    # page renders its own, separate, unrelated Rename disclosure per
    # note row -- unaffected by and unrelated to this new overflow
    # button -- so this check is scoped to the overflow menu only.)
    assert "note_rename" not in overflow_menu
    assert overflow_menu.count("<form") == 3

    # Exactly one Django-auto-id title input on the page -- Rename does
    # not add a second one; the ribbon's own `form.title` field remains
    # the sole rename mechanism. (`id="id_title">` rather than the bare
    # attribute, since `data-note-title-input-id="id_title"` -- the
    # existing attribute the Rename handler itself reads -- also
    # contains `id="id_title"` as a substring but is never followed
    # immediately by `>`.)
    assert content.count('id="id_title">') == 1


@pytest.mark.django_db
def test_note_detail_overflow_move_disclosure_offers_the_same_destinations_as_the_tree():
    # destination options and sentinel behavior must
    # match the existing tree Move popover exactly -- same folders, same
    # Unfiled option, same trashed-current-folder sentinel when applicable.
    owner = create_account("overflow-move-destinations-owner")
    folder_a = services.create_folder(owner=owner, name="Alpha")
    folder_b = services.create_folder(owner=owner, name="Beta")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder_a)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    overflow_menu_pattern = re.compile(
        r'<div class="tree-nav__row-menu-panel note-workspace__overflow-menu">'
        r"(.*?)</div>\s*</details>",
        re.DOTALL,
    )
    overflow_menu = overflow_menu_pattern.search(content).group(1)

    assert 'id="overflow-move-folder"' in overflow_menu
    assert ">Unfiled<" in overflow_menu
    assert f'value="{folder_a.id}" selected' in overflow_menu
    assert f">{folder_a.name}<" in overflow_menu
    assert f">{folder_b.name}<" in overflow_menu


def test_note_detail_overflow_move_disclosure_mirrors_the_toolbar_trashed_folder_sentinel():
    # the overflow Move disclosure's destination
    # options must preserve the existing toolbar Move popover's own
    # trashed-current-folder sentinel handling exactly. Asserted directly
    # against the template *source*, not a rendered page, since triggering
    # this exact state at runtime (an active note whose own folder is
    # trashed) does not currently arise through any existing user-facing
    # flow -- `move_folder_to_trash` cascades to trash the note too, and
    # restoring a note out of a still-trashed folder reassigns it to
    # Recovered Items rather than leaving it pointed at the trashed folder
    # (see `test_folder_trash.py`'s own documented finding that this
    # sentinel's underlying state "no longer arises via restore"). The
    # markup itself must still exist and match, regardless of whether any
    # current flow reaches it.
    detail_template = (
        Path(__file__).resolve().parent.parent / "templates" / "notes" / "detail.html"
    ).read_text()

    assert (
        'Currently in "{{ note.folder.name }}" (trashed) — choose a destination' in detail_template
    )
    assert 'value="keep-current-folder" selected' in detail_template


@pytest.mark.django_db
def test_note_detail_overflow_move_disclosure_actually_moves_the_note_on_post():
    # The overflow Move form posts to the exact same, unchanged route as
    # every other Move entry point -- functional behavior is identical.
    owner = create_account("overflow-move-post-owner")
    destination = services.create_folder(owner=owner, name="Destination")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": destination.id}
    )

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id == destination.id


@pytest.mark.django_db
def test_note_detail_overflow_move_disclosure_is_not_nested_inside_the_note_editor_form():
    # own binding unsaved-change-boundary
    # requirement: the Move form must live outside <form data-note-form>,
    # matching the existing, already-accepted tree Move precedent, so no
    # nested <form> is introduced and no new navigation-guard behavior is
    # implied.
    owner = create_account("overflow-move-form-placement-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    editor_form_start = content.index("data-note-form")
    overflow_move_form_start = content.index(
        '<details class="tree-nav__action tree-nav__action--move-note">'
    )
    assert overflow_move_form_start < editor_form_start


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_is_collapsed_by_default():
    # New note must be collapsed whenever the
    # overflow opens -- no unconditional `open` attribute.
    owner = create_account("overflow-new-note-collapsed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert '<details class="tree-nav__action tree-nav__action--new-note">' in content


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_preselects_the_current_folder():
    # New note must
    # offer a real destination selector, matching Move's own UI, rather
    # than forcing the current note's folder with no choice. The current
    # folder remains the *default* (preselected), not a forced destination.
    owner = create_account("overflow-new-note-preselect-foldered-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    other_folder = services.create_folder(owner=owner, name="Archive")
    note = services.create_note(owner=owner, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    new_note_start = content.index('<details class="tree-nav__action tree-nav__action--new-note">')
    new_note_end = content.index("</details>", new_note_start)
    new_note_disclosure = content[new_note_start:new_note_end]

    assert 'id="overflow-new-note-folder"' in new_note_disclosure
    assert f'value="{folder.id}" selected' in new_note_disclosure
    assert f">{folder.name}<" in new_note_disclosure
    assert f">{other_folder.name}<" in new_note_disclosure
    assert ">Unfiled<" in new_note_disclosure


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_preselects_unfiled_from_unfiled_context():
    owner = create_account("overflow-new-note-preselect-unfiled-owner")
    note = services.create_note(owner=owner)
    assert note.folder_id is None

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    new_note_start = content.index('<details class="tree-nav__action tree-nav__action--new-note">')
    new_note_end = content.index("</details>", new_note_start)
    new_note_disclosure = content[new_note_start:new_note_end]

    assert 'value="" selected' in new_note_disclosure


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_creates_sibling_note_in_the_submitted_folder():
    # Submitting the (default-preselected) current folder still lands the
    # sibling there -- the ordinary, unmodified-submission case.
    owner = create_account("overflow-new-note-submit-foldered-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": folder.id}
    )

    assert response.status_code == 302
    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert sibling.folder_id == folder.id


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_creates_unfiled_note_from_unfiled_context():
    owner = create_account("overflow-new-note-unfiled-owner")
    note = services.create_note(owner=owner)
    assert note.folder_id is None

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": ""}
    )

    assert response.status_code == 302
    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert sibling.folder_id is None


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_lets_the_user_choose_a_different_owned_folder():
    owner = create_account("overflow-new-note-choose-folder-owner")
    current_folder = services.create_folder(owner=owner, name="Projects")
    destination = services.create_folder(owner=owner, name="Archive")
    note = services.create_note(owner=owner, folder=current_folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": destination.id}
    )

    assert response.status_code == 302
    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert sibling.folder_id == destination.id


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_lets_user_choose_unfiled_from_folder():
    owner = create_account("overflow-new-note-choose-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": ""}
    )

    assert response.status_code == 302
    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert sibling.folder_id is None


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_rejects_a_tampered_cross_owner_folder_id():
    owner = create_account("overflow-new-note-cross-owner-owner")
    other_owner = create_account("overflow-new-note-cross-owner-victim")
    foreign_folder = services.create_folder(owner=other_owner, name="Not Yours")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": foreign_folder.id}
    )

    assert response.status_code == 302
    assert not Note.objects.exclude(pk=note.id).filter(owner=owner).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_rejects_a_submitted_trashed_folder_id():
    owner = create_account("overflow-new-note-trashed-choice-owner")
    doomed = services.create_folder(owner=owner, name="Doomed")
    note = services.create_note(owner=owner)
    services.move_folder_to_trash(folder=doomed)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id]), {"folder": doomed.id}
    )

    assert response.status_code == 302
    assert not Note.objects.exclude(pk=note.id).filter(owner=owner).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_navigates_to_the_new_note_immediately():
    owner = create_account("overflow-new-note-navigation-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id])
    )

    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert response.url == f"{reverse('notes:detail', args=[sibling.id])}?new=1"


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_reuses_the_existing_creation_contract():
    # Reuses services.create_note() unchanged: generated title and the
    # canonical empty document, exactly like every other creation route.
    owner = create_account("overflow-new-note-contract-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(reverse("notes:note_create_sibling", args=[note.id]))

    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)
    assert sibling.title == documents.generated_title_for_timestamp(sibling.created_at)
    assert sibling.body_json == documents.canonical_empty_document()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_preserves_untouched_placeholder_cleanup():
    # An abandoned sibling note created this way is still hard-deleted
    # (not routed through Trash) by the existing eligibility check.
    owner = create_account("overflow-new-note-placeholder-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(reverse("notes:note_create_sibling", args=[note.id]))
    sibling = Note.objects.exclude(pk=note.id).get(owner=owner)

    services.move_note_to_trash(note=sibling)

    assert not Note.objects.filter(pk=sibling.id).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_is_owner_scoped():
    owner = create_account("overflow-new-note-owner")
    other_owner = create_account("overflow-new-note-other-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(other_owner).post(
        reverse("notes:note_create_sibling", args=[note.id])
    )

    assert response.status_code == 404
    assert not Note.objects.exclude(pk=note.id).filter(owner=owner).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_rejects_a_trashed_context_note():
    owner = create_account("overflow-new-note-trashed-context-owner")
    note = services.create_note(owner=owner)
    # Pinning first keeps this note past the untouched-placeholder check, so
    # move_note_to_trash() performs a real trash rather than a hard delete.
    services.set_note_pinned(note=note, pinned=True)
    services.move_note_to_trash(note=note)
    assert note.trashed_at is not None

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id])
    )

    assert response.status_code == 302
    assert not Note.objects.exclude(pk=note.id).filter(owner=owner).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_rejects_a_trashed_destination_folder():
    # Defensive: trashing a folder cascades to trash its notes too, so this
    # note also ends up trashed and is caught by the earlier trashed-context
    # check -- but the view must still degrade safely (no sibling created,
    # no crash) regardless of which guard actually catches it, including if
    # create_note()'s own trashed-folder guard (TrashedItemMutationError)
    # were ever reached directly.
    owner = create_account("overflow-new-note-trashed-folder-owner")
    folder = services.create_folder(owner=owner, name="Doomed")
    note = services.create_note(owner=owner, folder=folder)
    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[note.id])
    )

    assert response.status_code == 302
    active_notes = Note.objects.exclude(pk=note.id).filter(owner=owner, trashed_at__isnull=True)
    assert not active_notes.exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_is_post_only():
    owner = create_account("overflow-new-note-post-only-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_create_sibling", args=[note.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_requires_csrf_token():
    owner = create_account("overflow-new-note-csrf-owner")
    note = services.create_note(owner=owner)

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    session = csrf_client.session
    session[account_services.SESSION_GENERATION_KEY] = owner.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = csrf_client.post(reverse("notes:note_create_sibling", args=[note.id]))

    assert response.status_code == 403
    assert not Note.objects.exclude(pk=note.id).filter(owner=owner).exists()


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_is_not_nested_inside_the_note_editor_form():
    owner = create_account("overflow-new-note-form-placement-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    editor_form_start = content.index("data-note-form")
    overflow_new_note_form_start = content.index(
        '<details class="tree-nav__action tree-nav__action--new-note">'
    )
    assert overflow_new_note_form_start < editor_form_start


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_is_collapsed_by_default():
    owner = create_account("overflow-new-folder-collapsed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert '<details class="tree-nav__action tree-nav__action--new-folder" >' in content


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_reuses_the_existing_folder_create_route():
    owner = create_account("overflow-new-folder-route-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Reference"}
    )

    assert response.status_code == 302
    folder = Folder.objects.get(owner=owner, name="Reference")
    assert folder.owner_id == owner.id


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_creates_at_root_regardless_of_current_folder():
    # Folders are flat -- the active note's own folder never becomes a
    # parent, matching every existing New Folder entry point.
    owner = create_account("overflow-new-folder-root-owner")
    existing_folder = services.create_folder(owner=owner, name="Existing")
    note = services.create_note(owner=owner, folder=existing_folder)

    authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Sibling folder"}
    )

    new_folder = Folder.objects.get(owner=owner, name="Sibling folder")
    # Folder has no parent field at all -- its mere existence at this name,
    # scoped only to owner, is proof it is not nested under anything.
    assert not hasattr(new_folder, "parent")


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_preserves_validation_and_collision_handling():
    owner = create_account("overflow-new-folder-validation-owner")
    services.create_folder(owner=owner, name="Taken")
    note = services.create_note(owner=owner)

    blank_response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "   "}
    )
    duplicate_response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "taken"}
    )

    assert blank_response.status_code == 400
    assert duplicate_response.status_code == 409
    assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_does_not_move_the_current_note():
    owner = create_account("overflow-new-folder-no-move-owner")
    original_folder = services.create_folder(owner=owner, name="Original")
    note = services.create_note(owner=owner, folder=original_folder)

    authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "New home"}
    )

    note.refresh_from_db()
    assert note.folder_id == original_folder.id


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_does_not_create_a_note_inside_the_new_folder():
    owner = create_account("overflow-new-folder-no-note-owner")
    note = services.create_note(owner=owner)
    notes_before = Note.objects.filter(owner=owner).count()

    authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Empty on purpose"}
    )

    assert Note.objects.filter(owner=owner).count() == notes_before


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_reopens_with_its_error_on_validation_failure():
    # No-JS fallback path: both the outer overflow and the nested New
    # folder disclosure must reopen showing the local error, matching the
    # existing tree copies' own `{% if new_folder_error %}open{% endif %}`
    # convention.
    owner = create_account("overflow-new-folder-error-reopen-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": ""},
        HTTP_ACCEPT="text/html",
    )
    content = response.content.decode()

    assert response.status_code == 400
    assert (
        '<details class="note-workspace__overflow row-action-menu"\n'
        '               data-menu-align="left"\n'
        "               open>" in content
    )
    assert '<details class="tree-nav__action tree-nav__action--new-folder" open>' in content
    assert "Folder name is required." in content


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_is_not_nested_inside_the_note_editor_form():
    owner = create_account("overflow-new-folder-form-placement-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    editor_form_start = content.index("data-note-form")
    overflow_new_folder_form_start = content.index(
        '<details class="tree-nav__action tree-nav__action--new-folder"'
    )
    assert overflow_new_folder_form_start < editor_form_start


@pytest.mark.django_db
def test_note_detail_overflow_new_folder_disclosure_has_a_visible_cancel_button():
    # parity with the tree-toolbar's own standalone
    # New Folder popover, which already has a `data-row-menu-cancel`
    # button. Same class, same wording, same generic wiring -- reused, not
    # reimplemented.
    owner = create_account("overflow-new-folder-cancel-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    disclosure_start = content.index(
        '<details class="tree-nav__action tree-nav__action--new-folder"'
    )
    disclosure_end = content.index("</details>", disclosure_start)
    disclosure = content[disclosure_start:disclosure_end]

    assert (
        '<button type="button" class="button-link button-link--secondary"'
        " data-row-menu-cancel>Cancel</button>" in disclosure
    )
    assert disclosure.count("<form") == 1
    assert '<button type="submit">Create folder</button>' in disclosure

    cancel_pos = disclosure.index("data-row-menu-cancel")
    submit_pos = disclosure.index('<button type="submit">Create folder</button>')
    assert cancel_pos < submit_pos


@pytest.mark.django_db
def test_note_detail_overflow_new_note_disclosure_has_a_visible_cancel_button():
    # Parity with the same overflow's own New
    # Folder disclosure, which already has a `data-row-menu-
    # cancel` button. Same class, same wording, same generic wiring --
    # reused, not reimplemented.
    owner = create_account("overflow-new-note-cancel-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    disclosure_start = content.index(
        '<details class="tree-nav__action tree-nav__action--new-note">'
    )
    disclosure_end = content.index("</details>", disclosure_start)
    disclosure = content[disclosure_start:disclosure_end]

    assert (
        '<button type="button" class="button-link button-link--secondary"'
        " data-row-menu-cancel>Cancel</button>" in disclosure
    )
    assert disclosure.count("<form") == 1
    assert '<button type="submit">Create note</button>' in disclosure

    cancel_pos = disclosure.index("data-row-menu-cancel")
    submit_pos = disclosure.index('<button type="submit">Create note</button>')
    assert cancel_pos < submit_pos


@pytest.mark.django_db
def test_note_detail_overflow_move_disclosure_has_a_visible_cancel_button():
    # Parity with the same overflow's own New
    # Folder disclosure, which already has a `data-row-menu-
    # cancel` button. Same class, same wording, same generic wiring --
    # reused, not reimplemented.
    owner = create_account("overflow-move-cancel-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    disclosure_start = content.index(
        '<details class="tree-nav__action tree-nav__action--move-note">'
    )
    disclosure_end = content.index("</details>", disclosure_start)
    disclosure = content[disclosure_start:disclosure_end]

    assert (
        '<button type="button" class="button-link button-link--secondary"'
        " data-row-menu-cancel>Cancel</button>" in disclosure
    )
    assert disclosure.count("<form") == 1
    assert '<button type="submit">Move</button>' in disclosure

    cancel_pos = disclosure.index("data-row-menu-cancel")
    submit_pos = disclosure.index('<button type="submit">Move</button>')
    assert cancel_pos < submit_pos


@pytest.mark.django_db
def test_note_detail_uses_structural_tree_shell_without_fake_tree_actions():
    user = create_account("tree-shell-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    content = response.content.decode()
    assert "data-tree-shell" in content
    assert 'aria-label="Note navigation"' in content
    assert "data-tree-toggle" in content
    assert "data-tree-separator" in content
    assert 'aria-label="Tree width"' in content
    # The "New note" tree-header action, the "Filter notes…" tree-title
    # filter, a real Rename row-action menu
    # item, and a real Duplicate row-action
    # menu item all superseded these placeholder-absence assertions.
    assert "Focus mode" not in content


@pytest.mark.django_db
def test_notes_home_keeps_default_authenticated_header():
    # converged Home onto the shared persistent-tree
    # *content* shell (`.workspace-shell`), but deliberately did not
    # adopt note-detail's own custom workspace *header* -- Home keeps
    # the plain default header (Home/Trash links, brand, account menu,
    # Search, Help, theme), which already contains everything Home
    # needs with no note-specific title-bar actions to omit.
    user = create_account("notes-home-shell-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    assert b"app-header app-header--workspace" not in response.content
    assert b"brand-link brand-link--workspace" not in response.content
    assert b"workspace-shell" in response.content
    assert b"home-dashboard" in response.content


@pytest.mark.django_db
def test_note_create_view_redirects_to_immediate_new_note_editor():
    user = create_account("creator")

    response = authenticated_client(user).post(reverse("notes:create"))

    note = Note.objects.get(owner=user)
    assert response.status_code == 302
    assert response.url == f"{reverse('notes:detail', args=[note.id])}?new=1"


@pytest.mark.django_db
def test_immediate_new_note_page_exposes_one_time_focus_metadata():
    user = create_account("new-note-visit")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]), {"new": "1"})

    assert response.status_code == 200
    assert response.context["is_immediate_new_note_page"] is True
    assert response.context["generated_title"] == note.title
    assert b'data-note-is-immediate-new-page="true"' in response.content
    assert b"data-note-toolbar-toggle" in response.content
    assert b"Show formatting" in response.content
    assert b"Back to Notes" not in response.content
    assert b"hidden" in response.content


@pytest.mark.django_db
def test_existing_note_visit_does_not_expose_immediate_new_note_behavior():
    user = create_account("existing-note-visit")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert response.context["is_immediate_new_note_page"] is False
    assert b'data-note-is-immediate-new-page="false"' in response.content


@pytest.mark.django_db
def test_admin_can_create_and_open_a_private_note():
    admin = create_account("admin-notes", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).post(reverse("notes:create"))

    note = Note.objects.get(owner=admin)
    assert response.status_code == 302
    assert response.url == f"{reverse('notes:detail', args=[note.id])}?new=1"

    detail_response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))
    assert detail_response.status_code == 200
    assert note.title.encode() in detail_response.content


@pytest.mark.django_db
def test_note_detail_save_updates_title_body_and_plain_text():
    user = create_account("editor")
    note = services.create_note(owner=user)
    document = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}],
    }

    response = authenticated_client(user).post(
        reverse("notes:detail", args=[note.id]),
        {"title": "Updated title", "body_json": json.dumps(document), "version": note.version},
    )

    note.refresh_from_db()
    assert response.status_code == 302
    assert note.title == "Updated title"
    assert note.body_json == document
    assert note.body_plain_text == "Hello world"


@pytest.mark.django_db
def test_note_detail_save_and_reload_preserves_hard_breaks():
    user = create_account("hard-break-editor")
    note = services.create_note(owner=user)
    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Line one"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Line two"},
                ],
            }
        ],
    }

    save_response = authenticated_client(user).post(
        reverse("notes:detail", args=[note.id]),
        {"title": "Two-line note", "body_json": json.dumps(document), "version": note.version},
    )

    note.refresh_from_db()
    reload_response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert save_response.status_code == 302
    assert note.body_json == document
    assert note.body_plain_text == "Line one\nLine two"
    assert reload_response.status_code == 200
    assert b'"type": "hardBreak"' in reload_response.content


@pytest.mark.django_db
def test_case_insensitive_login_keeps_owner_scoping_intact():
    create_account("bootstrap-admin", role=User.ROLE_ADMIN)
    owner = create_account("Owner-User")
    other = create_account("Other-User")
    owner_note = services.create_note(owner=owner)
    other_note = services.create_note(owner=other)
    client = Client()

    login_response = client.post(
        reverse("accounts:login"),
        {"username": "  OWNER-USER  ", "password": PASSWORD},
    )
    own_response = client.get(reverse("notes:detail", args=[owner_note.id]))
    other_response = client.get(reverse("notes:detail", args=[other_note.id]))

    assert login_response.status_code == 302
    assert own_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route_name", ["notes:detail", "notes:print", "notes:export", "notes:download_text"]
)
def test_cross_user_note_routes_return_not_found(route_name):
    owner = create_account("owner-user")
    other = create_account("other-user")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).get(reverse(route_name, args=[note.id]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_admin_has_no_special_access_to_other_users_note():
    owner = create_account("owner-note")
    admin = create_account("admin-note", role=User.ROLE_ADMIN)
    note = services.create_note(owner=owner)

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_unsupported_schema_shows_recovery_page_and_blocks_save_and_print():
    user = create_account("unsupported-user")
    note = Note.objects.create(
        owner=user,
        title="Old note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        editor_schema_version=999,
        version=1,
    )
    client = authenticated_client(user)

    detail_response = client.get(reverse("notes:detail", args=[note.id]))
    save_response = client.post(
        reverse("notes:detail", args=[note.id]),
        {
            "title": "Nope",
            "body_json": json.dumps(documents.EMPTY_DOCUMENT),
            "version": note.version,
        },
    )
    print_response = client.get(reverse("notes:print", args=[note.id]))
    export_response = client.get(reverse("notes:export", args=[note.id]))

    note.refresh_from_db()
    assert detail_response.status_code == 200
    assert b"unsupported document version" in detail_response.content.lower()
    assert save_response.status_code == 409
    assert print_response.status_code == 409
    assert note.title == "Old note"
    assert export_response.status_code == 200


@pytest.mark.django_db
def test_print_view_renders_controlled_html():
    user = create_account("print-user")
    note = services.create_note(owner=user)
    note.body_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Safe "},
                    {
                        "type": "text",
                        "text": "link",
                        "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
                    },
                ],
            }
        ],
    }
    note.body_plain_text = "Safe link"
    note.save(update_fields=["body_json", "body_plain_text"])

    response = authenticated_client(user).get(reverse("notes:print", args=[note.id]))

    assert response.status_code == 200
    assert b"<h1>" in response.content
    assert b'rel="noopener noreferrer nofollow"' in response.content


@pytest.mark.django_db
def test_print_view_invokes_window_print_after_load_and_has_a_manual_button():
    # the print page itself calls `window.print`
    # once loaded, and separately offers a visible manual fallback
    # button (both wired to the same trigger, neither ever calling
    # `window.close()` -- the print tab is never auto-closed).
    user = create_account("print-auto-trigger-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:print", args=[note.id]))
    content = response.content.decode()

    assert '<button type="button" data-print-trigger>Print</button>' in content
    assert 'addEventListener("load"' in content
    assert "window.print()" in content
    assert "window.close()" not in content


@pytest.mark.django_db
def test_export_view_returns_canonical_json_payload_without_owner_data():
    user = create_account("export-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:export", args=[note.id]))
    payload = json.loads(response.content)

    assert response.status_code == 200
    assert response["Content-Disposition"].endswith(f'{note.id}.ridgenote.json"')
    assert payload["format_identifier"] == documents.EXPORT_FORMAT_IDENTIFIER
    assert payload["format_version"] == documents.EXPORT_FORMAT_VERSION
    assert payload["editor_schema_version"] == note.editor_schema_version
    assert payload["title"] == note.title
    assert "owner" not in payload


@pytest.mark.django_db
def test_version_aware_save_increments_note_version():
    user = create_account("version-owner")
    note = services.create_note(owner=user)

    saved = services.save_note(
        note=note,
        title="Versioned note",
        body_json=documents.EMPTY_DOCUMENT,
        version=note.version,
    )

    assert saved.version == 2


@pytest.mark.django_db
def test_stale_version_save_raises_conflict_without_overwrite():
    user = create_account("stale-owner")
    note = services.create_note(owner=user)
    original_version = note.version

    services.save_note(
        note=note,
        title="Fresh title",
        body_json=documents.EMPTY_DOCUMENT,
        version=original_version,
    )

    with pytest.raises(services.NoteSaveConflictError):
        services.save_note(
            note=note,
            title="Stale title",
            body_json=documents.EMPTY_DOCUMENT,
            version=original_version,
        )

    note.refresh_from_db()
    assert note.title == "Fresh title"
    assert note.version == 2


@pytest.mark.django_db
def test_freshness_endpoint_returns_canonical_note_state_for_owner_when_server_is_newer():
    user = create_account("freshness-owner")
    note = services.create_note(owner=user)
    note = services.save_note(
        note=note,
        title="Fresh title",
        body_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Server copy"}],
                }
            ],
        },
        version=note.version,
    )

    response = authenticated_client(user).get(
        reverse("notes:freshness", args=[note.id]),
        {"version": note.version - 1},
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 200
    assert payload == {
        "ok": True,
        "has_update": True,
        "note": {
            "title": note.title,
            "body_json": note.body_json,
            "editor_schema_version": note.editor_schema_version,
            "version": note.version,
            "modified_at": note.modified_at.isoformat(),
        },
    }


@pytest.mark.django_db
def test_freshness_endpoint_reports_no_update_when_client_is_current():
    user = create_account("freshness-current")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(
        reverse("notes:freshness", args=[note.id]),
        {"version": note.version},
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 200
    assert payload == {
        "ok": True,
        "has_update": False,
        "version": note.version,
        "modified_at": note.modified_at.isoformat(),
    }


@pytest.mark.django_db
def test_freshness_endpoint_returns_404_for_other_users_note():
    owner = create_account("freshness-visible-owner")
    other = create_account("freshness-visible-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).get(
        reverse("notes:freshness", args=[note.id]),
        {"version": note.version},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_freshness_endpoint_returns_json_401_when_unauthenticated():
    user = create_account("freshness-anon")
    note = services.create_note(owner=user)

    response = Client().get(
        reverse("notes:freshness", args=[note.id]),
        {"version": note.version},
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 401
    assert payload == {"ok": False, "error": "authentication_required"}


@pytest.mark.django_db
def test_freshness_endpoint_returns_unsupported_schema_response():
    user = create_account("freshness-unsupported")
    note = Note.objects.create(
        owner=user,
        title="Old note",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        editor_schema_version=999,
        version=1,
    )

    response = authenticated_client(user).get(
        reverse("notes:freshness", args=[note.id]),
        {"version": 1},
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 409
    assert payload == {"ok": False, "error": "unsupported_schema"}


@pytest.mark.django_db
def test_freshness_check_does_not_refresh_idle_activity():
    user = create_account("freshness-idle")
    note = services.create_note(owner=user)
    client = authenticated_client(user)
    session = client.session
    old_activity = (timezone.now() - timedelta(seconds=30)).timestamp()
    session[account_services.LAST_ACTIVITY_KEY] = old_activity
    session.save()

    response = client.get(
        reverse("notes:freshness", args=[note.id]),
        {"version": note.version},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    assert client.session[account_services.LAST_ACTIVITY_KEY] == old_activity


@pytest.mark.django_db
@override_settings(RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS=60, RIDGENOTE_SESSION_WARNING_SECONDS=5)
def test_freshness_polling_cannot_keep_an_otherwise_idle_session_alive():
    user = create_account("freshness-poll")
    note = services.create_note(owner=user)
    client = authenticated_client(user)

    # Real activity happened 55s ago -- not yet past the 60s idle
    # timeout, but only 5s of margin left.
    almost_stale = (timezone.now() - timedelta(seconds=55)).timestamp()
    session = client.session
    session[account_services.LAST_ACTIVITY_KEY] = almost_stale
    session.save()

    # Repeated background freshness polls (as the note editor's own
    # 25s-interval timer would send) must succeed while not yet expired,
    # but must never resurrect/extend the recorded activity timestamp --
    # this is the exact assertion that would have failed before the fix,
    # since the old code refreshed activity on every authenticated
    # request including this one.
    for _ in range(3):
        poll_response = client.get(
            reverse("notes:freshness", args=[note.id]),
            {"version": note.version},
            HTTP_ACCEPT="application/json",
        )
        assert poll_response.status_code == 200
        assert client.session[account_services.LAST_ACTIVITY_KEY] == almost_stale

    # Simulate the remaining idle margin elapsing with nothing but those
    # background polls in between: since they never touched the activity
    # timestamp, a real request now (past the 60s deadline) must still
    # be rejected as idle-expired.
    session = client.session
    session[account_services.LAST_ACTIVITY_KEY] = (
        timezone.now() - timedelta(seconds=61)
    ).timestamp()
    session.save()

    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_autosave_endpoint_returns_success_json_and_increments_version():
    user = create_account("autosave-user")
    note = services.create_note(owner=user)
    client = authenticated_client(user)
    document = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Autosaved"}]}],
    }

    response = client.post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Autosave note",
                "body_json": document,
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    note.refresh_from_db()
    payload = json.loads(response.content)
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["title"] == "Autosave note"
    assert payload["version"] == 2
    assert note.version == 2
    assert note.body_plain_text == "Autosaved"


@pytest.mark.django_db
def test_autosave_endpoint_returns_validation_errors():
    user = create_account("autosave-validation-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Bad",
                "body_json": {"type": "doc", "content": [{"type": "blockquote"}]},
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 400
    assert payload["ok"] is False
    assert "body_json" in payload["errors"]


def _malformed_link_document():
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "broken link",
                        "marks": [
                            {"type": "link", "attrs": {"href": "https://[not-valid"}}
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.django_db
def test_autosave_endpoint_rejects_malformed_link_href_without_500():
    """A malformed link href must not crash
    autosave -- it must be rejected the same way any other invalid
    `body_json` already is (see `test_autosave_endpoint_returns_validation_errors`
    immediately above), never as an unhandled HTTP 500."""
    user = create_account("autosave-malformed-link-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Bad link",
                "body_json": _malformed_link_document(),
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 400
    assert payload["ok"] is False
    assert "body_json" in payload["errors"]


@pytest.mark.django_db
def test_autosave_malformed_link_href_does_not_save_or_advance_version():
    user = create_account("autosave-malformed-link-no-write-user")
    note = services.create_note(owner=user)
    services.rename_note(note=note, title="Original Title")
    original_version = note.version
    original_body_json = note.body_json

    authenticated_client(user).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Should not be saved",
                "body_json": _malformed_link_document(),
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    note.refresh_from_db()
    assert note.title == "Original Title"
    assert note.body_json == original_body_json
    assert note.version == original_version


@pytest.mark.django_db
def test_autosave_endpoint_returns_conflict_json():
    user = create_account("autosave-conflict-user")
    note = services.create_note(owner=user)
    client = authenticated_client(user)

    services.save_note(
        note=note,
        title="Server title",
        body_json=documents.EMPTY_DOCUMENT,
        version=note.version,
    )

    response = client.post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Stale title",
                "body_json": documents.EMPTY_DOCUMENT,
                "version": 1,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    note.refresh_from_db()
    assert response.status_code == 409
    assert payload == {
        "ok": False,
        "error": "conflict",
        "current_version": note.version,
        "current_modified_at": note.modified_at.isoformat(),
    }


@pytest.mark.django_db
def test_autosave_endpoint_returns_404_for_cross_user_note():
    owner = create_account("autosave-owner")
    other = create_account("autosave-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Nope",
                "body_json": documents.EMPTY_DOCUMENT,
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_manual_post_save_returns_conflict_without_overwrite():
    user = create_account("manual-conflict-user")
    note = services.create_note(owner=user)
    stale_version = note.version
    services.save_note(
        note=note,
        title="Fresh body",
        body_json=documents.EMPTY_DOCUMENT,
        version=stale_version,
    )

    response = authenticated_client(user).post(
        reverse("notes:detail", args=[note.id]),
        {
            "title": "Stale body",
            "body_json": json.dumps(documents.EMPTY_DOCUMENT),
            "version": stale_version,
        },
    )

    note.refresh_from_db()
    assert response.status_code == 409
    assert note.title == "Fresh body"


@pytest.mark.django_db
def test_unauthenticated_autosave_returns_json_401():
    user = create_account("unauth-autosave-user")
    note = services.create_note(owner=user)

    response = Client().post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Nope",
                "body_json": documents.EMPTY_DOCUMENT,
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 401
    assert payload["ok"] is False


@pytest.mark.django_db
def test_autosave_returns_json_401_when_session_generation_is_stale():
    user = create_account("stale-session-user")
    note = services.create_note(owner=user)
    client = authenticated_client(user)
    user.session_generation += 1
    user.save(update_fields=["session_generation"])

    response = client.post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Nope",
                "body_json": documents.EMPTY_DOCUMENT,
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 401
    assert payload["ok"] is False


@pytest.mark.django_db
def test_autosave_returns_json_401_for_disabled_account():
    admin = create_account("disable-admin", role=User.ROLE_ADMIN)
    user = create_account("disable-target")
    note = services.create_note(owner=user)
    client = authenticated_client(user)

    account_services.set_user_active(target=user, active=False, actor=admin)

    response = client.post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Nope",
                "body_json": documents.EMPTY_DOCUMENT,
                "version": note.version,
            }
        ),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
    )

    payload = json.loads(response.content)
    assert response.status_code == 401
    assert payload["ok"] is False


# --- template structure tests ---


@pytest.mark.django_db
def test_detail_page_contains_drawer_trigger_button():
    user = create_account("drawer-trigger-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b"data-drawer-toggle" in response.content
    assert b'aria-controls="workspace-drawer"' in response.content
    # aria-label="Open navigation" is unique to the drawer trigger, proving aria-expanded is on it
    assert b'aria-label="Open navigation"' in response.content
    assert b'aria-expanded="false"' in response.content


@pytest.mark.django_db
def test_detail_page_contains_drawer_with_required_attributes():
    user = create_account("drawer-attrs-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b'id="workspace-drawer"' in response.content
    assert b'role="dialog"' in response.content
    assert b'aria-modal="true"' in response.content
    assert b'aria-label="Navigation"' in response.content
    # data-drawer\n matches the standalone attribute on the dialog div, not data-drawer-* prefixes
    assert b"data-drawer\n" in response.content
    # drawer starts inert so screen readers cannot reach it before it is opened
    assert b" inert" in response.content


@pytest.mark.django_db
def test_detail_page_drawer_contains_branding_and_nav_links():
    user = create_account("drawer-nav-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    # workspace-drawer__header proves the drawer branding section exists
    assert b"workspace-drawer__header" in content
    # class="brand-link" (without --workspace suffix) is unique to the drawer brand link
    assert b'class="brand-link"' in content
    # workspace-drawer__nav proves the nav section is inside the drawer
    assert b"workspace-drawer__nav" in content
    # workspace-drawer__account proves the account section is inside the drawer
    assert b"workspace-drawer__account" in content
    # signed-in username appears in the drawer identity section
    assert b"drawer-nav-user" in content


@pytest.mark.django_db
def test_detail_page_drawer_contains_close_button():
    user = create_account("drawer-close-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b"data-drawer-close" in response.content
    assert b'aria-label="Close navigation"' in response.content


@pytest.mark.django_db
def test_detail_page_contains_drawer_backdrop():
    user = create_account("drawer-backdrop-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b"data-drawer-backdrop" in response.content


@pytest.mark.django_db
def test_detail_page_drawer_shows_users_link_for_admin():
    admin = create_account("drawer-admin-user", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    users_url = reverse("accounts:user_list").encode()
    assert users_url in response.content


@pytest.mark.django_db
def test_detail_page_drawer_does_not_show_users_link_for_regular_user():
    user = create_account("drawer-regular-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    # Regular users must not see the Users admin link
    assert b"workspace-drawer__nav" in response.content
    users_url = reverse("accounts:user_list").encode()
    assert users_url not in response.content


@pytest.mark.django_db
def test_detail_page_initial_drawer_is_inert():
    user = create_account("drawer-inert-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b'id="workspace-drawer"' in response.content
    # Drawer must start with inert so AT cannot reach closed drawer content
    assert b" inert" in response.content


# ---------------------------------------------------------------------------
# account menu tests (non-workspace home page via base.html)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_page_shows_account_menu_for_authenticated_user():
    user = create_account("home-menu-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    # details element with account-menu class is present
    assert b'class="account-menu"' in content
    # summary trigger is present
    assert b"account-menu__trigger" in content
    # dropdown container is present
    assert b"account-menu__dropdown" in content


@pytest.mark.django_db
def test_home_page_account_menu_shows_signed_in_identity():
    user = create_account("home-identity-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    # Identity label appears in the account menu dropdown
    assert b"account-menu__identity" in response.content
    assert b"home-identity-user" in response.content


@pytest.mark.django_db
def test_home_page_account_menu_contains_sign_out_post_form():
    user = create_account("home-signout-user")
    logout_url = reverse("accounts:logout").encode()

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    # Sign out form uses POST to the logout URL
    assert b'method="post"' in content
    assert logout_url in content
    # Sign out button is identifiable
    assert b"Sign out" in content
    # CSRF token is present (csrfmiddlewaretoken input)
    assert b"csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_home_page_account_menu_shows_admin_entry_for_admin():
    admin = create_account("home-admin-user", role=User.ROLE_ADMIN)
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(admin).get(reverse("home"))

    assert response.status_code == 200
    assert users_url in response.content
    assert b">Admin<" in response.content


@pytest.mark.django_db
def test_home_page_account_menu_hides_admin_entry_for_regular_user():
    user = create_account("home-regular-user")
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    assert users_url not in response.content


@pytest.mark.django_db
def test_home_page_account_menu_has_no_placeholder_items():
    # Preferences (the last
    # remaining disabled "Coming later" account-menu row) is a real
    # link, matching Account/Theme/Download library backup before it --
    # no disabled account-menu placeholder remains at all.
    user = create_account("home-disabled-items-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    assert b'href="#"' not in content
    assert b"Coming later" not in content
    assert b"account-menu__item--disabled" not in content


@pytest.mark.django_db
def test_home_page_shows_help_trigger():
    user = create_account("home-help-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    assert b"data-help-toggle" in content
    assert b'aria-controls="help-panel"' in content
    # V1 Help the trigger is a real <a href="/help/">, not a
    # bare <button>, so a JS-disabled session can still reach Help.
    assert f'href="{reverse("help")}"'.encode() in content


@pytest.mark.django_db
def test_home_page_contains_help_dialog():
    user = create_account("home-dialog-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    assert b'id="help-panel"' in content
    assert b'class="help-panel"' in content
    assert b"data-help-close" in content


# ---------------------------------------------------------------------------
# account menu tests (workspace detail page)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_detail_page_workspace_shows_account_menu():
    user = create_account("ws-menu-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    assert b'class="account-menu"' in content
    assert b"account-menu__trigger" in content
    assert b"account-menu__dropdown" in content


@pytest.mark.django_db
def test_detail_page_workspace_shows_help_trigger():
    user = create_account("ws-help-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    assert b"data-help-toggle" in content
    assert b'aria-controls="help-panel"' in content
    assert f'href="{reverse("help")}"'.encode() in content


@pytest.mark.django_db
def test_detail_page_workspace_account_menu_admin_entry_for_admin():
    admin = create_account("ws-admin-menu-user", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert users_url in response.content


@pytest.mark.django_db
def test_detail_page_workspace_account_menu_no_admin_entry_for_regular_user():
    user = create_account("ws-regular-menu-user")
    note = services.create_note(owner=user)
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert users_url not in response.content


@pytest.mark.django_db
def test_detail_page_no_href_hash_placeholders():
    user = create_account("ws-no-hash-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert b'href="#"' not in response.content


# ---------------------------------------------------------------------------
# drawer account section tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_detail_page_drawer_contains_account_section():
    user = create_account("drawer-account-section-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    assert b"workspace-drawer__account" in content
    assert b"workspace-drawer__account-identity" in content
    # No disabled "Coming
    # later" placeholder remains in the drawer's account section either.
    assert b"Coming later" not in content


@pytest.mark.django_db
def test_detail_page_drawer_account_section_contains_sign_out():
    user = create_account("drawer-signout-user")
    note = services.create_note(owner=user)
    logout_url = reverse("accounts:logout").encode()

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    assert logout_url in content
    assert b"Sign out" in content


@pytest.mark.django_db
def test_detail_page_drawer_shows_admin_entry_for_admin():
    admin = create_account("drawer-admin-account-user", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    # Admin link appears (in the drawer account section and/or workspace header)
    assert users_url in response.content
    assert b">Admin<" in response.content


@pytest.mark.django_db
def test_detail_page_drawer_does_not_show_admin_entry_for_regular_user():
    user = create_account("drawer-regular-account-user")
    note = services.create_note(owner=user)
    users_url = reverse("accounts:user_list").encode()

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert users_url not in response.content


def _account_menu_dropdown_html(content: str) -> str:
    """Isolates the note-detail header's own `.account-menu__dropdown`
    subtree -- the one container unique to the header account menu on
    this page (the narrow drawer reuses `account-menu__item` on its own
    entries but has no `.account-menu__dropdown` wrapper of its own),
    so a page-wide search can no longer be satisfied by the drawer's
    separate copy of the same links."""
    start = content.index('class="account-menu__dropdown"')
    end = content.index("</details>", start)
    return content[start:end]


def _nav_drawer_html(content: str) -> str:
    """Isolates the narrow navigation drawer's own subtree, independent
    of the header account menu, via its unique `id="workspace-drawer"`
    container. Bounded to the drawer's own matching closing tag (by
    tracking nested-div depth) rather than slicing to the end of the
    document, so later page content -- such as the note-detail header's
    own independent account-menu copy of the same links -- is not
    mistakenly included."""
    marker = 'id="workspace-drawer"'
    start = content.rindex("<div", 0, content.index(marker))
    pos = start
    depth = 0
    while True:
        next_open = content.find("<div", pos + 1)
        next_close = content.index("</div>", pos + 1)
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open
        else:
            if depth == 0:
                return content[start : next_close + len("</div>")]
            depth -= 1
            pos = next_close


@pytest.mark.django_db
def test_detail_page_account_menu_shows_trash_recovery_entry_for_admin():
    # the note-detail header's own account-menu
    # dropdown is a third, independent copy of the same admin-only
    # links as the default shared header and the narrow drawer -- this
    # asserts against that specific subtree, not the whole page, so it
    # cannot be silently satisfied by either of the other two copies.
    admin = create_account("account-menu-recovery-admin-user", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)
    recovery_url = reverse("notes:admin_recovery")

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    dropdown = _account_menu_dropdown_html(response.content.decode())
    assert f'href="{recovery_url}"' in dropdown
    assert ">Global Trash Recovery<" in dropdown
    assert dropdown.count(">Global Trash Recovery<") == 1
    # Existing Admin link remains present, immediately before it.
    assert ">Admin<" in dropdown
    assert dropdown.index(">Admin<") < dropdown.index(">Global Trash Recovery<")


@pytest.mark.django_db
def test_detail_page_account_menu_hides_trash_recovery_entry_for_regular_user():
    user = create_account("account-menu-recovery-regular-user")
    note = services.create_note(owner=user)
    recovery_url = reverse("notes:admin_recovery")

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    dropdown = _account_menu_dropdown_html(response.content.decode())
    assert f'href="{recovery_url}"' not in dropdown
    assert "Trash Recovery" not in dropdown
    assert ">Admin<" not in dropdown
    # Ordinary entries remain present and unaffected.
    assert ">Sign out<" in dropdown
    # Account is now a real link (">Account<"),
    # not the former disabled "Account <small>...</small>" row.
    assert ">Account<" in dropdown


@pytest.mark.django_db
def test_detail_page_drawer_shows_trash_recovery_entry_for_admin():
    admin = create_account("drawer-recovery-admin-user", role=User.ROLE_ADMIN)
    note = services.create_note(owner=admin)
    recovery_url = reverse("notes:admin_recovery")

    response = authenticated_client(admin).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    drawer = _nav_drawer_html(response.content.decode())
    assert f'href="{recovery_url}"' in drawer
    assert ">Global Trash Recovery<" in drawer
    # Existing Admin link remains present alongside the Global Trash Recovery link.
    assert ">Admin<" in drawer
    # Exactly one Global Trash Recovery link within the drawer -- no duplicate there.
    assert drawer.count(">Global Trash Recovery<") == 1


@pytest.mark.django_db
def test_detail_page_drawer_hides_trash_recovery_entry_for_regular_user():
    user = create_account("drawer-recovery-regular-user")
    note = services.create_note(owner=user)
    recovery_url = reverse("notes:admin_recovery").encode()

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    assert recovery_url not in response.content
    assert b"Trash Recovery" not in response.content


@pytest.mark.django_db
def test_detail_page_unauthenticated_does_not_expose_trash_recovery_entry():
    user = create_account("drawer-recovery-owner-user")
    note = services.create_note(owner=user)

    response = Client().get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 302
    assert b"Trash Recovery" not in response.content


@pytest.mark.django_db
def test_detail_page_drawer_contains_help_trigger():
    user = create_account("drawer-help-trigger-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 200
    content = response.content
    # data-help-toggle appears (at least once in the drawer header)
    assert b"data-help-toggle" in content
    assert b'aria-controls="help-panel"' in content
    assert f'href="{reverse("help")}"'.encode() in content


# ---------------------------------------------------------------------------
# Session-persistent light/dark theme toggle, controlled via a compact
# quick named-theme selector (a small form with
# one submit button per available theme, `data-theme-quick-form`/
# `data-theme-quick-option`). Living only inside the account-menu popover
# does not satisfy the
# "directly visible top-bar control" requirement, so the wide/desktop
# copy is a real, always-visible top-bar control
# (`.theme-quick-menu`, `core/_theme_quick_selector.html`) sitting beside
# Search/Help; the account-menu's own copy is removed everywhere it
# has a top-bar equivalent -- the narrow drawer keeps its own copy as the
# documented fallback for the one case (workspace header at narrow) where
# the top-bar control itself is hidden.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_page_has_a_directly_visible_top_bar_theme_control():
    user = create_account("home-theme-toggle-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    assert response.status_code == 200
    # One directly on the top bar (outside the account menu -- not nested
    # inside its <details> dropdown), one in the narrow drawer's account
    # section Home also renders (nav_drawer_available=True). No standalone
    # header/drawer-header control remains beyond these two.
    assert content.count("data-theme-quick-form") == 2
    assert "Theme <small" not in content

    account_menu_start = content.index('<details class="account-menu">')
    account_menu_end = content.index("</details>", account_menu_start) + len("</details>")
    account_menu_markup = content[account_menu_start:account_menu_end]
    # The account menu itself no longer contains any theme control --
    # its former Theme row was removed once the real top-bar control
    # shipped.
    assert "data-theme-quick-form" not in account_menu_markup
    assert "theme-quick-menu" not in account_menu_markup

    # The top-bar copy is a sibling *after* the account menu closes, not
    # nested inside it -- confirming it is reachable without opening the
    # account menu first.
    theme_pos = content.index("theme-quick-menu", account_menu_end)
    assert theme_pos > account_menu_end

    # Account/Preferences/Download remain real
    # links inside the account menu, unaffected by the theme control change.
    account_pos = content.index('href="{}"'.format(reverse("accounts:account")))
    preferences_pos = content.index('href="{}"'.format(reverse("accounts:preferences")))
    download_pos = content.index('href="{}"'.format(reverse("notes:library_backup_download")))
    assert account_pos < preferences_pos < download_pos < account_menu_end < theme_pos


@pytest.mark.django_db
def test_home_page_top_bar_theme_control_sits_beside_search_and_help():
    user = create_account("home-theme-placement-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    theme_pos = content.index("theme-quick-menu")
    search_pos = content.index("global-search-trigger")
    help_pos = content.index("help-trigger")
    assert theme_pos < search_pos < help_pos


@pytest.mark.django_db
def test_home_page_header_has_no_standalone_theme_toggle_trigger():
    user = create_account("home-no-standalone-theme-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "icon-button theme-toggle-trigger" not in content
    # The old binary toggle's own markers must not have resurfaced.
    assert "data-theme-toggle" not in content


@pytest.mark.django_db
def test_detail_page_has_a_directly_visible_top_bar_theme_control():
    user = create_account("ws-theme-toggle-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert response.status_code == 200
    # One directly on the workspace top bar, one in the narrow drawer's
    # account section -- no standalone header/drawer-header control
    # remains beyond these two.
    assert content.count("data-theme-quick-form") == 2
    assert "icon-button theme-toggle-trigger" not in content
    assert "Theme <small" not in content

    account_menu_start = content.index('<details class="account-menu">')
    account_menu_end = content.index("</details>", account_menu_start) + len("</details>")
    account_menu_markup = content[account_menu_start:account_menu_end]
    assert "data-theme-quick-form" not in account_menu_markup
    assert "theme-quick-menu" not in account_menu_markup


@pytest.mark.django_db
def test_detail_page_drawer_account_section_theme_group_between_preferences_and_download():
    user = create_account("drawer-theme-toggle-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    drawer_start = content.index('id="workspace-drawer"')
    account_start = content.index("workspace-drawer__account", drawer_start)
    account_section = content[account_start:]

    assert account_section.count("data-theme-quick-form") == 1
    account_pos = account_section.index('href="{}"'.format(reverse("accounts:account")))
    preferences_pos = account_section.index('href="{}"'.format(reverse("accounts:preferences")))
    theme_pos = account_section.index("data-theme-quick-group")
    download_pos = account_section.index(
        'href="{}"'.format(reverse("notes:library_backup_download"))
    )
    assert account_pos < preferences_pos < theme_pos < download_pos

    # The drawer *header* (search/help/close) no longer carries any theme
    # control at all -- it only ever exists once, inside the account
    # section checked above.
    drawer_header_end = content.index("workspace-drawer__nav", drawer_start)
    drawer_header = content[drawer_start:drawer_header_end]
    assert "data-theme-quick-form" not in drawer_header


# ---------------------------------------------------------------------------
# Account page foundation: the account-menu link
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_page_account_menu_account_is_a_real_link():
    user = create_account("home-account-link-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    account_url = reverse("accounts:account")
    # Two copies: the default header's account menu, and the narrow
    # drawer's account section Home also renders -- same duplication
    # pattern already established for Theme.
    assert content.count(f'<a class="account-menu__item" href="{account_url}">Account</a>') == 2
    assert "Account <small" not in content
    # Preferences is a real
    # link too, so no "Coming later" placeholder remains.
    assert "Coming later" not in content


@pytest.mark.django_db
def test_detail_page_workspace_account_menu_account_is_a_real_link():
    user = create_account("ws-account-link-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    account_url = reverse("accounts:account")
    assert content.count(f'<a class="account-menu__item" href="{account_url}">Account</a>') == 2
    assert "Account <small" not in content


@pytest.mark.django_db
def test_home_page_account_menu_preferences_is_a_real_link():
    # Preferences is the
    # user-preference home (Timezone to start), not a disabled
    # "Coming later" placeholder -- same real-link pattern already
    # established for Account/Theme/Download library backup.
    user = create_account("home-preferences-link-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    preferences_url = reverse("accounts:preferences")
    assert (
        content.count(f'<a class="account-menu__item" href="{preferences_url}">Preferences</a>')
        == 2
    )
    assert "Preferences <small" not in content


@pytest.mark.django_db
def test_detail_page_workspace_account_menu_preferences_is_a_real_link():
    user = create_account("ws-preferences-link-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    preferences_url = reverse("accounts:preferences")
    assert (
        content.count(f'<a class="account-menu__item" href="{preferences_url}">Preferences</a>')
        == 2
    )
    assert "Preferences <small" not in content


@pytest.mark.django_db
def test_account_menu_order_unchanged_apart_from_account_becoming_a_link():
    user = create_account("account-menu-order-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    account_pos = content.index(f'href="{reverse("accounts:account")}"')
    preferences_pos = content.index('href="{}"'.format(reverse("accounts:preferences")))
    # Download library backup is now a real link
    # too -- located the same way Account/Theme already are, since
    # "Download library backup <small" no longer exists.
    download_pos = content.index(f'href="{reverse("notes:library_backup_download")}"')
    trash_pos = content.index(reverse("notes:trash"))
    logout_pos = content.index(reverse("accounts:logout"))
    assert account_pos < preferences_pos < download_pos < trash_pos < logout_pos


# ---------------------------------------------------------------------------
# Download library backup account-menu activation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_page_download_library_backup_is_a_real_link_in_both_copies():
    user = create_account("home-backup-link-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    backup_url = reverse("notes:library_backup_download")
    # Default header's account menu, plus the narrow drawer's account
    # section Home also renders -- same duplication pattern already
    # established for Account and Theme.
    assert content.count(f'<a class="account-menu__item" href="{backup_url}">') == 2
    assert "Download library backup <small" not in content
    # Preferences is a real
    # link too, so no "Coming later" placeholder remains at all.
    assert content.count("Coming later") == 0


@pytest.mark.django_db
def test_detail_page_download_library_backup_is_a_real_link_in_both_copies():
    user = create_account("ws-backup-link-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    backup_url = reverse("notes:library_backup_download")
    assert content.count(f'<a class="account-menu__item" href="{backup_url}">') == 2
    assert "Download library backup <small" not in content
    assert content.count("Coming later") == 0


@pytest.mark.django_db
def test_top_bar_theme_control_lists_warm_light_and_dark_options():
    user = create_account("theme-button-label-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    # DOM order puts the top-bar copy first; the drawer's fallback copy
    # comes later in the document.
    group_start = content.index("data-theme-quick-group")
    form_end = content.index("</form>", group_start)
    group_markup = content[group_start:form_end]

    assert 'data-theme-quick-option="warm-light"' in group_markup
    assert 'data-theme-quick-option="dark"' in group_markup
    assert ">Warm Light<" in group_markup
    assert ">Dark<" in group_markup
    assert "Coming later" not in group_markup


@pytest.mark.django_db
def test_top_bar_theme_control_icon_button_reads_as_appearance_not_a_binary_toggle():
    user = create_account("theme-icon-label-user")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    trigger_start = content.index("theme-quick-menu__trigger")
    trigger_open_start = content.rindex("<summary", 0, trigger_start)
    trigger_open_end = content.index(">", trigger_start)
    trigger_open_tag = content[trigger_open_start:trigger_open_end]

    assert 'aria-label="Appearance"' in trigger_open_tag
    assert 'data-tooltip="Appearance"' in trigger_open_tag
    # Never phrased as a light/dark flip -- selecting from a named list,
    # not toggling a binary state.
    assert "Switch to dark theme" not in content
    assert "Switch to light theme" not in content


@pytest.mark.django_db
def test_top_bar_theme_control_marks_the_current_theme_current():
    user = create_account("theme-current-marker-user", theme="dark")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    group_start = content.index("data-theme-quick-group")
    form_end = content.index("</form>", group_start)
    group_markup = content[group_start:form_end]

    warm_light_start = group_markup.index('data-theme-quick-option="warm-light"')
    warm_light_button_start = group_markup.rindex("<button", 0, warm_light_start)
    warm_light_button_end = group_markup.index(">", warm_light_start)
    dark_start = group_markup.index('data-theme-quick-option="dark"')
    dark_button_start = group_markup.rindex("<button", 0, dark_start)
    dark_button_end = group_markup.index(">", dark_start)

    assert (
        'aria-current="true"'
        not in group_markup[warm_light_button_start:warm_light_button_end]
    )
    assert 'aria-current="true"' in group_markup[dark_button_start:dark_button_end]


@pytest.mark.django_db
def test_theme_quick_form_posts_to_the_quick_set_theme_endpoint_with_csrf_and_next():
    user = create_account("theme-form-target-user")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    marker_pos = content.index("data-theme-quick-form")
    form_start = content.rindex("<form", 0, marker_pos)
    form_open_end = content.index(">", marker_pos)
    form_open_tag = content[form_start:form_open_end]
    assert reverse("accounts:quick_set_theme") in form_open_tag

    form_end = content.index("</form>", form_start)
    form_markup = content[form_start:form_end]
    assert "csrfmiddlewaretoken" in form_markup
    assert f'name="next" value="{reverse("notes:detail", args=[note.id])}"' in form_markup


@pytest.mark.django_db
def test_unauthenticated_root_page_redirects_to_login():
    # The root URL is the application front door:
    # anonymous `/` does not render a page of its own -- it redirects to
    # Login, preserving `next=` -- so there is no anonymous-visible root
    # content left to assert a missing theme toggle against here. The
    # login page's own no-theme-toggle invariant is covered separately by
    # test_login_page_has_no_theme_toggle_and_remains_forced_dark above.
    response = Client().get(reverse("home"))

    assert response.status_code == 302
    assert response.url == f"{reverse('accounts:login')}?next=/"


@pytest.mark.django_db
def test_login_page_has_no_theme_control_and_remains_forced_dark():
    create_account("login-theme-admin-user", role=User.ROLE_ADMIN)

    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()

    assert response.status_code == 200
    # Login remains intentionally dark and ignores the authenticated
    # user's persisted preference entirely: no theme control of any
    # kind, and the page keeps its existing hardcoded dark theme.
    assert "data-theme-quick-form" not in content
    assert "data-theme-quick-group" not in content
    # Both the canonical <html>
    # element and <body> must be dark, not just one of the two.
    assert '<html lang="en" data-theme="dark">' in content
    assert '<body data-theme="dark" class="login-page">' in content


@pytest.mark.django_db
def test_authenticated_page_server_renders_the_persisted_theme_with_no_flash_script():
    # `request.user.theme` is rendered
    # directly into `data-theme` server-side (the authoritative source) --
    # there is no client-side theme value to read
    # before first paint, so no such script exists at all.
    user = create_account("anti-flash-script-user", theme="dark")

    authed_response = authenticated_client(user).get(reverse("home"))
    authed_content = authed_response.content.decode()
    # Both the canonical <html>
    # element and <body> must render the persisted theme.
    assert '<html lang="en" data-theme="dark">' in authed_content
    assert 'data-theme="dark"' in authed_content[authed_content.index("<body") :]
    assert b"ridgenote.theme" not in authed_response.content
    # The unrelated `data-js` marker script is untouched and still
    # authenticated-only.
    assert b'document.documentElement.dataset.js="true"' in authed_response.content

    # `home` redirects anonymous requests to Login (no rendered content to
    # check against) -- Login itself is the real unauthenticated page.
    create_account("anti-flash-script-admin", role=User.ROLE_ADMIN)
    anon_response = Client().get(reverse("accounts:login"))
    assert b'document.documentElement.dataset.js="true"' not in anon_response.content

    login_response = Client().get(reverse("accounts:login"))
    assert b"ridgenote.theme" not in login_response.content


# ---------------------------------------------------------------------------
# empty state tests (first-login / no-notes)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_home_empty_state_contains_new_note_action():
    # the New Note action always renders in the
    # dashboard intro (not conditionally inside an empty-state block),
    # so a user with no notes still sees it.
    user = create_account("empty-state-user")
    create_url = reverse("notes:create").encode()

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    assert b"home-recent__empty" in content
    # POST form action pointing to the note-creation endpoint is present
    assert b'method="post"' in content
    assert create_url in content
    # The CSRF token is present
    assert b"csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_home_empty_state_shows_brief_guidance_line():
    user = create_account("empty-guidance-user")

    response = authenticated_client(user).get(reverse("home"))

    assert response.status_code == 200
    content = response.content
    assert b"home-recent__empty" in content
    # One-line guidance text is present
    assert b"Create your first note" in content
