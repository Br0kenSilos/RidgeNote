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


def _excluding_help_panel(content: str) -> str:
    """Everything before the in-app Help dialog (`id="help-panel"`), which
    is embedded on every page and legitimately discusses common UI-action
    words (New folder, Rename, etc.) in its own copy -- whole-page text
    counts must exclude it to test actual tree/toolbar markup, not
    incidental Help wording."""
    return content[: content.index('id="help-panel"')]


def _wide_tree_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


@pytest.mark.django_db
def test_home_tree_renders_new_note_action_and_keeps_existing_home_buttons():
    # Home's single New Note dashboard action coexists with the tree's own
    # "New note" toolbar action, which renders twice (wide + drawer).
    # Total create-route actions are 3 (1 dashboard + 2 tree copies).
    owner = create_account("newnote-home-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    create_url = reverse("notes:create")

    assert content.count(f'action="{create_url}"') == 3
    assert content.count('aria-label="New note"') == 2
    assert content.count(">New Note<") == 1


@pytest.mark.django_db
def test_note_detail_renders_new_note_action_in_both_tree_copies():
    owner = create_account("newnote-note-detail-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    create_url = reverse("notes:create")

    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)

    assert wide.count(f'action="{create_url}"') == 1
    assert drawer.count(f'action="{create_url}"') == 1
    assert wide.count('aria-label="New note"') == 1
    assert drawer.count('aria-label="New note"') == 1


@pytest.mark.django_db
def test_new_note_action_creates_a_note_and_redirects_like_existing_home_action():
    # The create-and-redirect behavior itself (generated title, redirect to
    # the new note's editor) is already covered generically by
    # notes/tests/test_notes.py::test_note_create_view_redirects_to_immediate_new_note_editor
    # and ::test_admin_can_create_and_open_a_private_note. This test only
    # confirms the tree-header action posts to that same existing route.
    owner = create_account("newnote-submit-owner")
    seed_note = services.create_note(owner=owner)
    starting_total = Note.objects.filter(owner=owner).count()

    response = authenticated_client(owner).post(reverse("notes:create"))

    assert response.status_code == 302
    assert Note.objects.filter(owner=owner).count() == starting_total + 1
    assert reverse("notes:detail", args=[seed_note.id]) != response.url


@pytest.mark.django_db
def test_note_detail_tree_still_renders_new_folder_filter_move_rename_pins_and_links():
    owner = create_account("newnote-coexistence-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    current_note = services.create_note(owner=owner)
    services.assign_note_folder(note=current_note, folder=folder)
    services.set_note_pinned(note=current_note, pinned=True)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = _excluding_help_panel(response.content.decode())

    assert content.count('aria-label="New note"') == 2
    # "New folder" appears twice per tree copy -- aria-label and the
    # deterministic data-tooltip attribute (there is no native title
    # attribute) -- across both copies, plus one more from
    # the active-note overflow disclosure summary.
    assert content.count("New folder") == 5
    assert content.count('placeholder="Filter notes…"') == 2
    assert content.count("Move note") == 4
    # "Rename" appears five times per tree copy -- the folder-rename
    # disclosure's trigger and submit button, plus the
    # "Rename to" label and "Rename" submit button for the note row's own
    # Rename form, plus the compact disclosure
    # summary for that same note-row Rename -- across both the wide tree
    # and narrow drawer copies, plus one more from
    # the active-note overflow Rename button (rendered once, not per tree
    # copy, since the overflow itself is not duplicated wide/narrow).
    assert content.count("Rename") == 11
    assert "tree-nav__pin-marker" in content
    assert f'href="{reverse("notes:detail", args=[current_note.id])}"' in content


@pytest.mark.django_db
def test_home_dashboard_still_has_no_drag_affordances_with_new_note_action_present():
    # Home's narrow-drawer tree copy and wide tree rail both carry drag
    # handles legitimately. Only Home's own
    # dashboard content pane (not the tree beside it) is expected to
    # stay drag-free.
    owner = create_account("newnote-home-no-drag-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()
    dashboard_start = content.index('class="home-dashboard home-dashboard--populated"')
    dashboard_section = content[dashboard_start : content.index('id="workspace-drawer"')]

    assert "tree-nav__drag-handle" not in dashboard_section
    assert "data-move-url-template" not in dashboard_section


@pytest.mark.django_db
def test_note_detail_tree_still_has_drag_affordances_with_new_note_action_present():
    owner = create_account("newnote-note-detail-drag-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # One drag handle per tree copy for the single existing note.
    assert content.count("tree-nav__drag-handle") == 2
    assert content.count("data-move-url-template") == 2


@pytest.mark.django_db
def test_note_detail_renders_no_duplicate_element_ids_with_new_note_action():
    owner = create_account("newnote-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


# -- Confirming tree note ordering is completely
# -- unaffected by which route created a note.
# -- `notes_grouped_for_tree()` and `services.create_note()` are shared
# -- identically by every creation route -- these tests prove that
# -- sharing holds, rather than just asserting it from reading the code.
# -- The tree's ordering rule is `-pinned, Lower(title),
# -- -id` (`TREE_NOTE_DISPLAY_ORDER`), distinct from `-pinned,
# -- -modified_at, -id` (`TREE_NOTE_ORDER`) used elsewhere -- these
# -- tests assert alphabetical order to describe what they actually verify.


def _set_modified_at(note, *, hours_ago):
    from datetime import timedelta

    from django.utils import timezone

    Note.objects.filter(pk=note.pk).update(modified_at=timezone.now() - timedelta(hours=hours_ago))
    note.refresh_from_db()
    return note


def _set_title(note, *, title):
    Note.objects.filter(pk=note.pk).update(title=title)
    note.refresh_from_db()
    return note


@pytest.mark.django_db
def test_note_create_sibling_notes_sort_alphabetically_with_every_other_route():
    # Each note's
    # `modified_at` is set in the *reverse* of its title's alphabetical
    # position, so this test only passes if the tree is genuinely
    # ordering by title, not by recency, regardless of which route
    # created the note.
    owner = create_account("tree-order-interleave-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    # Via the tree toolbar's per-folder route.
    via_folder_toolbar = services.note_for_owner_or_404(
        note_id=services.create_note(owner=owner, folder=folder).id, owner=owner
    )
    _set_title(via_folder_toolbar, title="Charlie")
    _set_modified_at(via_folder_toolbar, hours_ago=3)

    # The currently open note whose overflow creates the sibling below --
    # also lives in this folder, so it needs its own controlled title/
    # timestamp too, not just the notes the assertion is really about.
    current_note = services.create_note(owner=owner, folder=folder)
    _set_title(current_note, title="Delta")
    _set_modified_at(current_note, hours_ago=4)
    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]),
        {"folder": folder.id},
    )
    via_overflow_id = int(response.url.split("/notes/")[1].split("/")[0])
    via_overflow = services.note_for_owner_or_404(note_id=via_overflow_id, owner=owner)
    _set_title(via_overflow, title="Alpha")
    _set_modified_at(via_overflow, hours_ago=2)

    # Created via the plain root `notes:create` route, then moved into the
    # same folder (mirroring how a real user would place it) -- also
    # given the *most recent* `modified_at` of the four, despite sorting
    # second alphabetically.
    via_plain_create = services.note_for_owner_or_404(
        note_id=services.create_note(owner=owner).id, owner=owner
    )
    services.assign_note_folder(note=via_plain_create, folder=folder)
    _set_title(via_plain_create, title="Bravo")
    _set_modified_at(via_plain_create, hours_ago=1)

    folders, _ = services.notes_grouped_for_tree(owner=owner)
    notes_in_folder = [n for f in folders if f.id == folder.id for n in f.notes.all()]

    # Alphabetical by title (Alpha, Bravo, Charlie, Delta), regardless of
    # which route created each note and regardless of `modified_at` --
    # exactly TREE_NOTE_DISPLAY_ORDER, no separate rule for any one
    # creation path.
    assert [n.id for n in notes_in_folder] == [
        via_overflow.id,
        via_plain_create.id,
        via_folder_toolbar.id,
        current_note.id,
    ]


@pytest.mark.django_db
def test_note_create_sibling_created_note_follows_established_unfiled_ordering():
    # The newly-created sibling is deliberately
    # back-dated far into the past (so it is the *least* recently
    # modified note) while still sorting first alphabetically -- this
    # only passes if Unfiled ordering is genuinely alphabetical, not
    # recency-based.
    owner = create_account("tree-order-unfiled-owner")
    current_note = services.create_note(owner=owner)
    _set_title(current_note, title="Bravo")
    _set_modified_at(current_note, hours_ago=1)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": ""}
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])
    new_note = services.note_for_owner_or_404(note_id=new_id, owner=owner)
    _set_title(new_note, title="Alpha")
    _set_modified_at(new_note, hours_ago=10)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [new_note.id, current_note.id]


@pytest.mark.django_db
def test_note_create_sibling_does_not_affect_pin_promotion_ordering():
    # Pin promotion still outranks everything for a note created through
    # this route, exactly as it already does for every other creation
    # path. Among the unpinned notes, "Alpha" is deliberately given the
    # *oldest* `modified_at` (10h ago, vs. "Zulu"'s 1h ago) -- this only
    # passes if the unpinned tiebreak is genuinely alphabetical.
    owner = create_account("tree-order-pin-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    pinned = services.create_note(owner=owner, folder=folder)
    _set_title(pinned, title="Zulu Pinned")
    services.set_note_pinned(note=pinned, pinned=True)
    _set_modified_at(pinned, hours_ago=5)

    current_note = services.create_note(owner=owner, folder=folder)
    _set_title(current_note, title="Zulu")
    _set_modified_at(current_note, hours_ago=1)
    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]),
        {"folder": folder.id},
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])
    new_note = services.note_for_owner_or_404(note_id=new_id, owner=owner)
    _set_title(new_note, title="Alpha")
    _set_modified_at(new_note, hours_ago=10)

    folders, _ = services.notes_grouped_for_tree(owner=owner)
    notes_in_folder = [n for f in folders if f.id == folder.id for n in f.notes.all()]

    assert [n.id for n in notes_in_folder] == [pinned.id, new_note.id, current_note.id]


# -- Focused coverage for the tree's own
# -- alphabetical ordering rule (`TREE_NOTE_DISPLAY_ORDER`), distinct
# -- from Home/Recent-switcher/All-Notes/Trash, which are all unaffected.


@pytest.mark.django_db
def test_pinned_notes_appear_before_all_unpinned_notes():
    owner = create_account("tree-pin-before-owner")
    pinned = services.create_note(owner=owner)
    _set_title(pinned, title="Zebra Pinned")
    services.set_note_pinned(note=pinned, pinned=True)
    _set_modified_at(pinned, hours_ago=10)
    unpinned = services.create_note(owner=owner)
    _set_title(unpinned, title="Apple Unpinned")
    _set_modified_at(unpinned, hours_ago=1)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [pinned.id, unpinned.id]


@pytest.mark.django_db
def test_pinned_notes_sort_case_insensitively_by_title():
    owner = create_account("tree-pin-case-owner")
    apple = services.create_note(owner=owner)
    _set_title(apple, title="apple")
    services.set_note_pinned(note=apple, pinned=True)
    _set_modified_at(apple, hours_ago=1)
    banana = services.create_note(owner=owner)
    _set_title(banana, title="Banana")
    services.set_note_pinned(note=banana, pinned=True)
    _set_modified_at(banana, hours_ago=5)
    cherry = services.create_note(owner=owner)
    _set_title(cherry, title="Cherry")
    services.set_note_pinned(note=cherry, pinned=True)
    _set_modified_at(cherry, hours_ago=3)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [apple.id, banana.id, cherry.id]


@pytest.mark.django_db
def test_unpinned_notes_sort_case_insensitively_by_title():
    owner = create_account("tree-unpinned-case-owner")
    apple = services.create_note(owner=owner)
    _set_title(apple, title="apple")
    _set_modified_at(apple, hours_ago=1)
    banana = services.create_note(owner=owner)
    _set_title(banana, title="Banana")
    _set_modified_at(banana, hours_ago=5)
    cherry = services.create_note(owner=owner)
    _set_title(cherry, title="Cherry")
    _set_modified_at(cherry, hours_ago=3)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [apple.id, banana.id, cherry.id]


@pytest.mark.django_db
def test_equal_normalized_titles_tie_break_by_id_descending():
    owner = create_account("tree-tie-break-owner")
    first = services.create_note(owner=owner)
    _set_title(first, title="Draft")
    second = services.create_note(owner=owner)
    _set_title(second, title="draft")
    assert second.pk > first.pk

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [second.id, first.id]


@pytest.mark.django_db
def test_foldered_and_unfiled_notes_use_the_identical_ordering_rule():
    owner = create_account("tree-foldered-parity-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    foldered_apple = services.create_note(owner=owner, folder=folder)
    _set_title(foldered_apple, title="Apple")
    _set_modified_at(foldered_apple, hours_ago=10)
    foldered_zebra = services.create_note(owner=owner, folder=folder)
    _set_title(foldered_zebra, title="Zebra")
    _set_modified_at(foldered_zebra, hours_ago=1)

    unfiled_apple = services.create_note(owner=owner)
    _set_title(unfiled_apple, title="Apple")
    _set_modified_at(unfiled_apple, hours_ago=10)
    unfiled_zebra = services.create_note(owner=owner)
    _set_title(unfiled_zebra, title="Zebra")
    _set_modified_at(unfiled_zebra, hours_ago=1)

    folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)
    notes_in_folder = list(folders[0].notes.all())

    assert [n.id for n in notes_in_folder] == [foldered_apple.id, foldered_zebra.id]
    assert [n.id for n in unfiled_notes] == [unfiled_apple.id, unfiled_zebra.id]


@pytest.mark.django_db
def test_wide_tree_and_narrow_drawer_render_identical_tree_order():
    # Both tree copies are rendered from the same server-side
    # `notes_grouped_for_tree()` call within a single response -- there is
    # no separate narrow-drawer query path to drift out of sync, so one
    # order assertion spanning both sections is sufficient (mirrors the
    # wide/drawer-section-splitting pattern used throughout this file and
    # `test_tree_pinning.py`).
    owner = create_account("tree-wide-drawer-parity-owner")
    current_note = services.create_note(owner=owner)
    _set_title(current_note, title="Middle")
    zebra = services.create_note(owner=owner)
    _set_title(zebra, title="Zebra")
    _set_modified_at(zebra, hours_ago=1)
    apple = services.create_note(owner=owner)
    _set_title(apple, title="Apple")
    _set_modified_at(apple, hours_ago=10)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current_note.id]))
    content = response.content.decode()
    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)

    for section in (wide, drawer):
        apple_pos = section.index(f'data-note-id="{apple.id}"')
        current_pos = section.index(f'data-note-id="{current_note.id}"')
        zebra_pos = section.index(f'data-note-id="{zebra.id}"')
        assert apple_pos < current_pos < zebra_pos


@pytest.mark.django_db
def test_folder_ordering_is_still_alphabetical_sanity_check():
    # Folder ordering uses
    # Lower("name").asc() -- a simple sanity check alongside the
    # note-ordering coverage above.
    owner = create_account("tree-folder-order-owner")
    services.create_folder(owner=owner, name="cherry")
    services.create_folder(owner=owner, name="Apple")
    services.create_folder(owner=owner, name="banana")

    folders, _ = services.notes_grouped_for_tree(owner=owner)

    assert [f.name for f in folders] == ["Apple", "banana", "cherry"]


@pytest.mark.django_db
def test_pinning_an_existing_note_moves_it_into_pinned_alphabetical_position():
    owner = create_account("tree-pin-reposition-owner")
    pinned_apple = services.create_note(owner=owner)
    _set_title(pinned_apple, title="Apple")
    services.set_note_pinned(note=pinned_apple, pinned=True)
    pinned_cherry = services.create_note(owner=owner)
    _set_title(pinned_cherry, title="Cherry")
    services.set_note_pinned(note=pinned_cherry, pinned=True)
    to_promote = services.create_note(owner=owner)
    _set_title(to_promote, title="Banana")
    _set_modified_at(to_promote, hours_ago=1)

    services.set_note_pinned(note=to_promote, pinned=True)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [pinned_apple.id, to_promote.id, pinned_cherry.id]


@pytest.mark.django_db
def test_unpinning_moves_note_back_into_unpinned_alphabetical_position():
    owner = create_account("tree-unpin-reposition-owner")
    still_pinned = services.create_note(owner=owner)
    _set_title(still_pinned, title="Zebra Pinned")
    services.set_note_pinned(note=still_pinned, pinned=True)
    apple = services.create_note(owner=owner)
    _set_title(apple, title="Apple")
    delta = services.create_note(owner=owner)
    _set_title(delta, title="Delta")
    to_unpin = services.create_note(owner=owner)
    _set_title(to_unpin, title="Charlie")
    services.set_note_pinned(note=to_unpin, pinned=True)

    services.set_note_pinned(note=to_unpin, pinned=False)

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [still_pinned.id, apple.id, to_unpin.id, delta.id]


@pytest.mark.django_db
def test_renaming_a_note_changes_its_tree_position_after_refresh():
    owner = create_account("tree-rename-reposition-owner")
    apple = services.create_note(owner=owner)
    _set_title(apple, title="Apple")
    to_rename = services.create_note(owner=owner)
    _set_title(to_rename, title="Bravo")
    zebra = services.create_note(owner=owner)
    _set_title(zebra, title="Zebra")

    _, before = services.notes_grouped_for_tree(owner=owner)
    assert [n.id for n in before] == [apple.id, to_rename.id, zebra.id]

    services.rename_note(note=to_rename, title="Zulu")

    _, after = services.notes_grouped_for_tree(owner=owner)
    assert [n.id for n in after] == [apple.id, zebra.id, to_rename.id]


@pytest.mark.django_db
def test_move_preserves_destination_folders_alphabetical_ordering():
    owner = create_account("tree-move-ordering-owner")
    destination = services.create_folder(owner=owner, name="Destination")
    apple = services.create_note(owner=owner, folder=destination)
    _set_title(apple, title="Apple")
    cherry = services.create_note(owner=owner, folder=destination)
    _set_title(cherry, title="Cherry")

    moved = services.create_note(owner=owner)
    _set_title(moved, title="Banana")

    services.assign_note_folder(note=moved, folder=destination)

    folders, _ = services.notes_grouped_for_tree(owner=owner)
    destination_folder = next(f for f in folders if f.id == destination.id)

    assert [n.id for n in destination_folder.notes.all()] == [apple.id, moved.id, cherry.id]


@pytest.mark.django_db
def test_new_note_creation_appears_in_correct_alphabetical_tree_position():
    owner = create_account("tree-create-position-owner")
    apple = services.create_note(owner=owner)
    _set_title(apple, title="Apple")
    zebra = services.create_note(owner=owner)
    _set_title(zebra, title="Zebra")

    new_note = services.create_note(owner=owner)
    _set_title(new_note, title="Middle")

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    # "Middle" is neither first nor last -- proving position is computed
    # by title, not just appended/prepended.
    assert [n.id for n in unfiled_notes] == [apple.id, new_note.id, zebra.id]


@pytest.mark.django_db
def test_duplicate_appears_in_correct_alphabetical_tree_position():
    owner = create_account("tree-duplicate-position-owner")
    original = services.create_note(owner=owner)
    _set_title(original, title="Bravo")
    early = services.create_note(owner=owner)
    _set_title(early, title="Apple")
    late = services.create_note(owner=owner)
    _set_title(late, title="Zebra")

    duplicate = services.duplicate_note(note=original)
    # `duplicate_note` prefixes with "Copy of " -- "Copy of Bravo" sorts
    # after "Bravo" (C > B) and before "Zebra" alphabetically.
    assert duplicate.title == "Copy of Bravo"

    _, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert [n.id for n in unfiled_notes] == [early.id, original.id, duplicate.id, late.id]


@pytest.mark.django_db
def test_restored_note_appears_in_correct_alphabetical_tree_position():
    owner = create_account("tree-restore-position-owner")
    apple = services.create_note(owner=owner)
    _set_title(apple, title="Apple")
    to_restore = services.create_note(owner=owner)
    _set_title(to_restore, title="Middle")
    zebra = services.create_note(owner=owner)
    _set_title(zebra, title="Zebra")

    services.move_note_to_trash(note=to_restore)
    _, before_restore = services.notes_grouped_for_tree(owner=owner)
    assert [n.id for n in before_restore] == [apple.id, zebra.id]

    services.restore_note_from_trash(note=to_restore)

    _, after_restore = services.notes_grouped_for_tree(owner=owner)
    assert [n.id for n in after_restore] == [apple.id, to_restore.id, zebra.id]
