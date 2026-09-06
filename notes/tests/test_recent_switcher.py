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


def _switcher_section(content: str) -> str:
    start = content.index("data-note-recent-switcher")
    end = content.index("</details>", start)
    return content[start:end]


# -- recent_notes_for_owner (service) ---------------------------------------


@pytest.mark.django_db
def test_recent_notes_for_owner_excludes_the_given_note():
    owner = create_account("recent-exclude-owner")
    current = services.create_note(owner=owner)
    other = services.create_note(owner=owner)

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert current not in recent
    assert other in recent


@pytest.mark.django_db
def test_recent_notes_for_owner_orders_by_modified_at_then_id_descending():
    owner = create_account("recent-order-owner")
    current = services.create_note(owner=owner)
    first = services.create_note(owner=owner)
    second = services.create_note(owner=owner)
    # Force identical modified_at so the -id tie-break is exercised.
    same_time = timezone.now()
    Note.objects.filter(pk__in=[first.pk, second.pk]).update(modified_at=same_time)

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert [n.id for n in recent] == sorted([first.id, second.id], reverse=True)


@pytest.mark.django_db
def test_recent_notes_for_owner_ignores_pin_state_by_default():
    # `pinned_first` defaults to False, preserving
    # this function's order for its existing caller (this
    # switcher) -- pin state must have zero effect unless explicitly
    # requested.
    owner = create_account("recent-default-no-pin-owner")
    current = services.create_note(owner=owner)
    older_pinned = services.create_note(owner=owner)
    services.set_note_pinned(note=older_pinned, pinned=True)
    newer_unpinned = services.create_note(owner=owner)
    Note.objects.filter(pk=older_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert [n.id for n in recent] == [newer_unpinned.id, older_pinned.id]


@pytest.mark.django_db
def test_recent_notes_for_owner_ignores_pin_state_when_explicitly_false():
    owner = create_account("recent-explicit-false-owner")
    current = services.create_note(owner=owner)
    older_pinned = services.create_note(owner=owner)
    services.set_note_pinned(note=older_pinned, pinned=True)
    newer_unpinned = services.create_note(owner=owner)
    Note.objects.filter(pk=older_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    recent = services.recent_notes_for_owner(
        owner=owner, exclude_note_id=current.id, pinned_first=False
    )

    assert [n.id for n in recent] == [newer_unpinned.id, older_pinned.id]


@pytest.mark.django_db
def test_recent_notes_for_owner_promotes_pinned_notes_when_requested():
    owner = create_account("recent-pinned-first-owner")
    current = services.create_note(owner=owner)
    older_pinned = services.create_note(owner=owner)
    services.set_note_pinned(note=older_pinned, pinned=True)
    newer_unpinned = services.create_note(owner=owner)
    Note.objects.filter(pk=older_pinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=2)
    )
    Note.objects.filter(pk=newer_unpinned.pk).update(
        modified_at=timezone.now() - timezone.timedelta(hours=1)
    )

    recent = services.recent_notes_for_owner(
        owner=owner, exclude_note_id=current.id, pinned_first=True
    )

    assert [n.id for n in recent] == [older_pinned.id, newer_unpinned.id]


@pytest.mark.django_db
def test_recent_notes_for_owner_caps_at_eight():
    owner = create_account("recent-cap-owner")
    current = services.create_note(owner=owner)
    for _ in range(10):
        services.create_note(owner=owner)

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert len(recent) == 8


@pytest.mark.django_db
def test_recent_notes_for_owner_does_not_include_other_owners_notes():
    owner = create_account("recent-scope-owner")
    other = create_account("recent-scope-other")
    current = services.create_note(owner=owner)
    other_note = services.create_note(owner=other)

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert other_note not in recent


# -- note-detail rendering ---------------------------------------------------


@pytest.mark.django_db
def test_note_detail_renders_recent_switcher():
    owner = create_account("switcher-render-owner")
    current = services.create_note(owner=owner)
    other = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert "note-recent-switcher" in switcher
    expected_href = reverse("notes:detail", args=[other.id])
    assert f'href="{expected_href}"' in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_remains_recent_first_without_pin_priority():
    # Home's Recent module has pin priority,
    # but this switcher's own call site (`recent_notes_for_owner(owner=
    # note.owner, exclude_note_id=note.id)`) deliberately has no
    # `pinned_first` argument -- so its rendered order
    # must stay pure recency, unaffected by pin state.
    owner = create_account("switcher-no-pin-promotion-owner")
    current = services.create_note(owner=owner)
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

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert switcher.index(">Newer unpinned note<") < switcher.index(">Older pinned note<")


@pytest.mark.django_db
def test_note_detail_recent_switcher_excludes_current_note():
    owner = create_account("switcher-exclude-owner")
    current = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    current_href = reverse("notes:detail", args=[current.id])
    assert f'href="{current_href}"' not in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_shows_empty_state_with_no_other_notes():
    owner = create_account("switcher-empty-owner")
    current = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert "No other recent notes." in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_caps_at_eight_links():
    owner = create_account("switcher-cap-owner")
    current = services.create_note(owner=owner)
    for _ in range(10):
        services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert switcher.count("note-recent-switcher__link") == 8


@pytest.mark.django_db
def test_note_detail_recent_switcher_does_not_show_cross_owner_notes():
    owner = create_account("switcher-cross-owner")
    other = create_account("switcher-cross-other")
    current = services.create_note(owner=owner)
    other_note = services.create_note(owner=other)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    other_href = reverse("notes:detail", args=[other_note.id])
    assert f'href="{other_href}"' not in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_entries_are_plain_links_not_forms():
    owner = create_account("switcher-plain-link-owner")
    current = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert "<form" not in switcher
    assert "csrfmiddlewaretoken" not in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_has_typed_filter_input():
    # The switcher legitimately renders a typed
    # title/path filter input.
    owner = create_account("switcher-filter-owner")
    current = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[current.id]))
    content = response.content.decode()
    switcher = _switcher_section(content)

    assert "data-quick-switch-input" in switcher
    assert "<input" in switcher


@pytest.mark.django_db
def test_note_detail_recent_switcher_is_outside_the_note_editor_form():
    owner = create_account("switcher-outside-form-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    switcher_start = content.index("data-note-recent-switcher")
    editor_form_start = content.index("data-note-form")
    assert switcher_start < editor_form_start


# Home's note-finding affordance is the
# dashboard's always-visible Recent module, not a quick-switch-
# enabled `<details>` disclosure, which exists only on note-detail
# (its own tests above are unaffected). Coverage for the Recent module
# itself lives in
# `test_home_dashboard.py` (`test_home_recent_module_caps_at_five`,
# `test_home_recent_module_orders_newest_modified_first`,
# `test_home_recent_module_owner_scoped`, and the "no tags or action
# buttons" test covering plain-link-only rows).


@pytest.mark.django_db
def test_home_has_functional_global_search_and_no_command_palette_overlay():
    # Asserting the absence of the literal string "Global Search" would be
    # misleading -- that exact phrase never appears as visible text anywhere in the
    # app (the button/dialog are both simply titled "Search"). Global
    # Search's real trigger/dialog/runtime are present and functional on
    # Home via the default header; this test checks those actual
    # functional markers instead.
    owner = create_account("switcher-home-search-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "data-global-search-toggle" in content
    assert 'id="global-search-panel"' in content
    assert "global-search-trigger" in content
    assert "command-palette" not in content


@pytest.mark.django_db
def test_home_row_action_menu_unaffected_by_dashboard():
    # The tree's own row-action menu (present in Home's wide tree
    # rail, not just the drawer) has its full expected item set.
    owner = create_account("switcher-home-regression-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "row-action-menu" in content
    assert ">Duplicate<" in content


@pytest.mark.django_db
def test_note_detail_has_no_global_search_or_command_palette_overlay():
    # Typed quick-switch filtering legitimately
    # exists, but Global Search and any command-palette overlay remain
    # unauthorized and absent.
    owner = create_account("switcher-no-overlay-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "Global Search" not in content
    assert "command-palette" not in content


# -- Regression: existing note-detail behavior unaffected --------------------


@pytest.mark.django_db
def test_note_detail_title_input_still_present_with_switcher():
    owner = create_account("switcher-title-owner")
    note = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'name="title"' in content


@pytest.mark.django_db
def test_note_detail_tags_block_still_present_with_switcher():
    owner = create_account("switcher-tags-owner")
    note = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "data-note-tags" in content


@pytest.mark.django_db
def test_note_detail_row_action_menu_unaffected_by_switcher():
    owner = create_account("switcher-row-menu-owner")
    note = services.create_note(owner=owner)
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "row-action-menu" in content
    assert ">Duplicate<" in content
