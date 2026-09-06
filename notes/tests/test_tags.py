import threading

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.forms import NoteTagAssignForm
from notes.models import TAG_COLOR_CHOICES, Tag

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


# -- Tag model -----------------------------------------------------------


@pytest.mark.django_db
def test_tag_can_be_created_with_owner_name_and_color():
    owner = create_account("tag-create-owner")

    tag = Tag.objects.create(owner=owner, name="Work", color="blue")

    assert tag.owner_id == owner.id
    assert tag.name == "Work"
    assert tag.color == "blue"
    assert tag.created_at is not None


@pytest.mark.django_db
def test_tag_name_must_not_be_blank():
    owner = create_account("tag-blank-owner")

    with pytest.raises(IntegrityError):
        Tag.objects.create(owner=owner, name="", color="blue")


@pytest.mark.django_db
def test_tag_color_must_be_within_fixed_palette():
    owner = create_account("tag-palette-owner")

    with pytest.raises(IntegrityError):
        Tag.objects.create(owner=owner, name="Invalid color", color="not-a-real-color")


@pytest.mark.django_db
def test_tag_owner_scoped_names_are_unique_case_insensitively():
    owner = create_account("tag-unique-owner")
    Tag.objects.create(owner=owner, name="Work", color="blue")

    with pytest.raises(IntegrityError):
        Tag.objects.create(owner=owner, name="WORK", color="green")


@pytest.mark.django_db
def test_different_owners_may_use_the_same_tag_name():
    owner_one = create_account("tag-owner-one")
    owner_two = create_account("tag-owner-two")
    Tag.objects.create(owner=owner_one, name="Work", color="blue")

    tag_two = Tag.objects.create(owner=owner_two, name="Work", color="green")

    assert tag_two.owner_id == owner_two.id


# -- get_or_create_tag (service) ------------------------------------------


@pytest.mark.django_db
def test_get_or_create_tag_creates_a_new_tag_for_new_name():
    owner = create_account("goc-create-owner")

    tag = services.get_or_create_tag(owner=owner, name="Ideas", color="amber")

    assert tag.name == "Ideas"
    assert tag.color == "amber"
    assert Tag.objects.filter(owner=owner, name="Ideas").count() == 1


@pytest.mark.django_db
def test_get_or_create_tag_reuses_existing_tag_case_insensitively():
    owner = create_account("goc-reuse-owner")
    existing = services.get_or_create_tag(owner=owner, name="Ideas", color="amber")

    reused = services.get_or_create_tag(owner=owner, name="IDEAS", color="rose")

    assert reused.id == existing.id
    assert reused.color == "amber"
    assert Tag.objects.filter(owner=owner, name__iexact="ideas").count() == 1


@pytest.mark.django_db
def test_get_or_create_tag_requires_a_non_blank_name():
    owner = create_account("goc-blank-owner")

    with pytest.raises(ValueError):
        services.get_or_create_tag(owner=owner, name="   ", color="blue")


# -- assign / remove (service) --------------------------------------------


@pytest.mark.django_db
def test_assign_tag_to_note_adds_tag_to_notes_tag_set():
    owner = create_account("assign-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")

    services.assign_tag_to_note(note=note, tag=tag)

    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
def test_assigning_an_existing_tag_by_name_does_not_create_a_duplicate():
    owner = create_account("assign-existing-owner")
    note_one = services.create_note(owner=owner)
    note_two = services.create_note(owner=owner)
    first_tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note_one, tag=first_tag)

    second_tag = services.get_or_create_tag(owner=owner, name="work", color="green")
    services.assign_tag_to_note(note=note_two, tag=second_tag)

    assert first_tag.id == second_tag.id
    assert Tag.objects.filter(owner=owner, name__iexact="work").count() == 1


@pytest.mark.django_db
def test_remove_tag_from_note_removes_the_association_only():
    owner = create_account("remove-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    services.remove_tag_from_note(note=note, tag=tag)

    assert list(note.tags.all()) == []
    assert Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_assigning_a_tag_does_not_change_note_version_or_modified_at():
    owner = create_account("assign-metadata-owner")
    note = services.create_note(owner=owner)
    original_version = note.version
    original_modified_at = note.modified_at
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")

    services.assign_tag_to_note(note=note, tag=tag)

    note.refresh_from_db()
    assert note.version == original_version
    assert note.modified_at == original_modified_at


@pytest.mark.django_db
def test_removing_a_tag_does_not_change_note_version_or_modified_at():
    owner = create_account("remove-metadata-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)
    note.refresh_from_db()
    original_version = note.version
    original_modified_at = note.modified_at

    services.remove_tag_from_note(note=note, tag=tag)

    note.refresh_from_db()
    assert note.version == original_version
    assert note.modified_at == original_modified_at


# -- tag_for_owner_or_404 (service) ----------------------------------------


@pytest.mark.django_db
def test_tag_for_owner_or_404_returns_owned_tag():
    owner = create_account("lookup-owner")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")

    found = services.tag_for_owner_or_404(tag_id=tag.id, owner=owner)

    assert found.id == tag.id


@pytest.mark.django_db
def test_tag_for_owner_or_404_raises_for_other_owners_tag():
    owner = create_account("lookup-owner-a")
    other = create_account("lookup-owner-b")
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")

    with pytest.raises(Exception) as exc_info:
        services.tag_for_owner_or_404(tag_id=tag.id, owner=other)
    assert exc_info.typename == "Http404"


# -- note_tag_assign view --------------------------------------------------


@pytest.mark.django_db
def test_note_tag_assign_view_creates_and_assigns_new_tag():
    # The view applies uppercase
    # normalization (currently always on, no preference exists yet to
    # disable it) -- "Personal" is stored as "PERSONAL". See the
    # "tag-name normalization" section below for dedicated coverage;
    # this test's own concern (create-and-assign wiring) is otherwise
    # unchanged.
    owner = create_account("view-assign-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Personal", "color": "green"},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    tag = Tag.objects.get(owner=owner, name="PERSONAL")
    assert tag.color == "green"
    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
def test_note_tag_assign_view_assigns_existing_tag_case_insensitively():
    owner = create_account("view-assign-existing-owner")
    note = services.create_note(owner=owner)
    existing_tag = services.get_or_create_tag(owner=owner, name="Personal", color="green")

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "PERSONAL", "color": "rose"},
    )

    assert Tag.objects.filter(owner=owner, name__iexact="personal").count() == 1
    note.refresh_from_db()
    assert list(note.tags.all()) == [existing_tag]
    existing_tag.refresh_from_db()
    assert existing_tag.color == "green"


@pytest.mark.django_db
def test_note_tag_assign_view_rejects_a_tag_already_on_this_note():
    # Re-adding a tag already
    # on this note must not fall through to the "success" redirect (a no-op
    # `note.tags.add()`), which would cause an unexplained extra POST-then-GET round
    # trip -- a visible flicker with no error message. It is caught
    # before the tag service is reached and rendered in place, matching the
    # blank-name convention below.
    owner = create_account("view-assign-dup-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Work", "color": "green"},
    )

    assert response.status_code == 400
    content = response.content.decode()
    assert "This tag is already added to this note." in content
    note.refresh_from_db()
    assert list(note.tags.all()) == [tag]
    assert Tag.objects.filter(owner=owner, name__iexact="work").count() == 1


@pytest.mark.django_db
def test_note_tag_assign_view_rejects_a_tag_already_on_this_note_case_insensitively():
    owner = create_account("view-assign-dup-ci-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "WORK", "color": "green"},
    )

    assert response.status_code == 400
    note.refresh_from_db()
    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
def test_note_tag_assign_view_rejecting_a_duplicate_tag_preserves_attempted_value_and_autofocuses():
    # The preserved value is the *normalized*
    # attempted name (uppercased) -- the view's duplicate check runs
    # after `NoteTagAssignForm.clean_name()` has already normalized it,
    # not the raw submitted string.
    owner = create_account("view-assign-dup-preserve-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Work", "color": "green"},
    )

    content = response.content.decode()
    assert 'value="WORK"' in content
    assert "autofocus" in content
    assert 'aria-invalid="true"' in content
    assert 'aria-describedby="note-tag-error"' in content


@pytest.mark.django_db
def test_note_tag_assign_view_duplicate_tag_error_does_not_produce_a_global_message():
    owner = create_account("view-assign-dup-no-global-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Work", "color": "green"},
    )

    content = response.content.decode()
    assert 'class="messages"' not in content


@pytest.mark.django_db
def test_note_tag_assign_view_rejects_blank_name():
    owner = create_account("view-assign-blank-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "   ", "color": "blue"},
    )

    # Rendered in place rather than a redirect, matching
    # the existing New Folder validation convention -- the error appears
    # directly beside the tag controls instead of via a global message.
    assert response.status_code == 400
    assert Tag.objects.filter(owner=owner).count() == 0
    content = response.content.decode()
    assert "Tag name is required." in content


@pytest.mark.django_db
def test_note_tag_assign_view_rejecting_blank_name_preserves_attempted_value():
    owner = create_account("view-assign-blank-preserve-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "   ", "color": "blue"},
    )

    content = response.content.decode()
    assert 'value="   "' in content


@pytest.mark.django_db
def test_note_tag_assign_view_rejecting_blank_name_does_not_touch_the_service():
    owner = create_account("view-assign-blank-no-service-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "", "color": "blue"},
    )

    assert list(note.tags.all()) == []
    assert Tag.objects.filter(owner=owner).count() == 0


@pytest.mark.django_db
def test_note_tag_assign_view_cross_owner_note_returns_404():
    owner = create_account("view-assign-cross-owner")
    other = create_account("view-assign-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Personal", "color": "blue"},
    )

    assert response.status_code == 404
    assert Tag.objects.filter(owner=other).count() == 0


@pytest.mark.django_db
def test_note_tag_assign_view_unauthenticated_post_is_blocked():
    owner = create_account("view-assign-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Personal", "color": "blue"},
    )

    assert response.status_code == 302
    assert Tag.objects.filter(owner=owner).count() == 0


@pytest.mark.django_db
def test_note_tag_assign_view_get_is_not_allowed():
    owner = create_account("view-assign-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_tag_assign", args=[note.id]))

    assert response.status_code == 405


# -- note_tag_remove view ---------------------------------------------------


@pytest.mark.django_db
def test_note_tag_remove_view_removes_tag_and_redirects_to_detail():
    owner = create_account("view-remove-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Personal", color="green")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_remove", args=[note.id]), {"tag_id": tag.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    note.refresh_from_db()
    assert list(note.tags.all()) == []
    assert Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_note_tag_remove_view_cross_owner_tag_returns_404():
    owner = create_account("view-remove-cross-owner")
    other = create_account("view-remove-cross-other")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Personal", color="green")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(other).post(
        reverse("notes:note_tag_remove", args=[note.id]), {"tag_id": tag.id}
    )

    assert response.status_code == 404
    note.refresh_from_db()
    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
def test_note_tag_remove_view_unauthenticated_post_is_blocked():
    owner = create_account("view-remove-unauth-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Personal", color="green")
    services.assign_tag_to_note(note=note, tag=tag)

    response = Client().post(reverse("notes:note_tag_remove", args=[note.id]), {"tag_id": tag.id})

    assert response.status_code == 302
    note.refresh_from_db()
    assert list(note.tags.all()) == [tag]


@pytest.mark.django_db
def test_note_tag_remove_view_get_is_not_allowed():
    owner = create_account("view-remove-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_tag_remove", args=[note.id]))

    assert response.status_code == 405


# -- Note-detail rendering ---------------------------------------------------


@pytest.mark.django_db
def test_note_detail_renders_assigned_tags_as_labeled_chips():
    owner = create_account("render-chip-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'class="note-tags__chip" data-tag-color="blue"' in content
    assert "Work" in content
    assert f'aria-label="Remove tag {tag.name}"' in content


@pytest.mark.django_db
def test_note_detail_renders_empty_state_when_note_has_no_tags():
    owner = create_account("render-empty-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "No tags yet." in content
    assert "note-tags__chip" not in content


@pytest.mark.django_db
def test_note_detail_renders_assign_form_targeting_note_tag_assign_route():
    owner = create_account("render-assign-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    expected_action = reverse("notes:note_tag_assign", args=[note.id])
    assert f'action="{expected_action}"' in content
    for value, label in TAG_COLOR_CHOICES:
        assert f'<option value="{value}">{label}</option>' in content


@pytest.mark.django_db
def test_note_detail_tag_controls_render_outside_the_note_editor_form():
    owner = create_account("render-outside-form-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    tags_block_start = content.index("data-note-tags")
    editor_form_start = content.index("data-note-form")
    assert tags_block_start < editor_form_start


# -- Regression: no tag UI outside note-detail --------------------------------


@pytest.mark.django_db
def test_home_renders_read_only_tags_with_no_assign_or_remove_controls():
    # Home legitimately renders read-only tag
    # chips (see notes/tests/test_home_tags.py for full display coverage) --
    # this test only guards the assign/create/remove-control boundary, which
    # remains note-detail-only.
    owner = create_account("no-home-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "note-tags__assign-form" not in content
    assert "note-tags__remove-form" not in content
    assert "note_tag_assign" not in content
    assert "note_tag_remove" not in content


@pytest.mark.django_db
def test_note_detail_tree_does_not_render_any_tag_ui():
    owner = create_account("no-tree-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    tree_section = content[content.index("data-tree-pane") : content.index('<div class="note-tags')]
    assert "note-tags" not in tree_section
    assert "row-action-menu" in tree_section


@pytest.mark.django_db
def test_row_action_menu_does_not_gain_tag_controls():
    owner = create_account("no-row-menu-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    panel_start = content.index("tree-nav__row-menu-panel")
    panel_end = content.index("</div>", panel_start)
    panel = content[panel_start:panel_end]
    assert "tag" not in panel.lower()


# -- Tag section: compact metadata row --------


@pytest.mark.django_db
def test_note_detail_tag_controls_render_in_compact_metadata_row():
    owner = create_account("compact-meta-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    meta_start = content.index("note-workspace__meta")
    tags_start = content.index("data-note-tags")
    # The title-bar overflow menu references this form
    # (`form="note-duplicate-form"`) from up in the header, ahead of the
    # metadata row -- anchor on the hidden form's own declaration
    # (`id="note-duplicate-form"`) rather than the bare id string, which the
    # button's `form=` attribute would also match earlier on the page.
    duplicate_form_start = content.index('id="note-duplicate-form"')
    assert meta_start < tags_start < duplicate_form_start


@pytest.mark.django_db
def test_note_detail_add_tag_button_uses_compact_control_class():
    # Add tag has its own
    # compact-control class (`.note-tags__submit`), matched in CSS to the
    # same height as the tag-name input and color select, rather than the
    # much taller default global button style. Structural hook only -- the
    # actual rendered height is a CSS concern validated by browser
    # inspection, not asserted here.
    owner = create_account("compact-submit-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert '<button type="submit" class="note-tags__submit">Add tag</button>' in content


@pytest.mark.django_db
def test_note_detail_no_tag_error_shown_without_a_failed_attempt():
    owner = create_account("no-tag-error-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "note-tags__error" not in content
    assert "Tag name is required." not in content


@pytest.mark.django_db
def test_note_detail_renders_tag_error_beside_the_tag_controls_after_failed_assign():
    owner = create_account("tag-error-render-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "", "color": "blue"},
    )
    content = response.content.decode()

    tags_start = content.index("data-note-tags")
    error_start = content.index("note-tags__error")
    assert tags_start < error_start
    assert "Tag name is required." in content


@pytest.mark.django_db
def test_note_detail_tag_error_response_still_renders_the_editor_form():
    owner = create_account("tag-error-editor-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "", "color": "blue"},
    )
    content = response.content.decode()

    assert "data-note-form" in content
    assert "data-note-editor-root" in content


# ---------------------------------------------------------------------------
# Tag identity, canonicalization, and limits
# ---------------------------------------------------------------------------

# -- normalize_tag_name() ----------------------------------------------------


def test_normalize_tag_name_uppercases_lowercase_input():
    assert services.normalize_tag_name("docker", uppercase=True) == "DOCKER"


def test_normalize_tag_name_uppercases_mixed_case_input():
    assert services.normalize_tag_name("DoCkEr", uppercase=True) == "DOCKER"


def test_normalize_tag_name_preserves_case_when_uppercase_disabled():
    assert services.normalize_tag_name("DoCkEr", uppercase=False) == "DoCkEr"


def test_normalize_tag_name_trims_outer_whitespace():
    assert services.normalize_tag_name("   Windows Server   ", uppercase=False) == "Windows Server"


def test_normalize_tag_name_collapses_repeated_internal_whitespace():
    assert services.normalize_tag_name("WINDOWS     SERVER", uppercase=False) == "WINDOWS SERVER"


def test_normalize_tag_name_trims_and_collapses_together():
    assert (
        services.normalize_tag_name("   WINDOWS   SERVER   ", uppercase=False) == "WINDOWS SERVER"
    )


def test_normalize_tag_name_rejects_tab_rather_than_collapsing_it():
    # TAB is a prohibited ASCII control
    # character (U+0009, within U+0000-U+001F) -- it must be rejected
    # outright, never silently absorbed by whitespace collapse.
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME\tLAB", uppercase=False)


def test_normalize_tag_name_rejects_lf_rather_than_collapsing_it():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME\nLAB", uppercase=False)


def test_normalize_tag_name_rejects_cr_rather_than_collapsing_it():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME\rLAB", uppercase=False)


def test_normalize_tag_name_rejects_ff_rather_than_collapsing_it():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME\fLAB", uppercase=False)


def test_normalize_tag_name_rejects_vt_rather_than_collapsing_it():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME\vLAB", uppercase=False)


def test_normalize_tag_name_rejects_a_leading_tab_rather_than_silently_trimming_it():
    # The rejection check runs against the raw, untouched input -- a
    # prohibited character at the very edge (where a plain `.strip()`
    # would otherwise silently remove it before any check ever saw it)
    # must still be caught.
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("\tHOME LAB", uppercase=False)


def test_normalize_tag_name_rejects_a_trailing_newline_rather_than_silently_trimming_it():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("HOME LAB\n", uppercase=False)


def test_normalize_tag_name_still_collapses_repeated_ordinary_spaces():
    # Only the *ordinary* space (U+0020) -- never a rejected ASCII
    # control -- is what "collapsible whitespace" means in practice:
    # control characters are rejected before
    # collapse ever runs, so collapse only ever sees legitimate
    # whitespace.
    assert services.normalize_tag_name("HOME     LAB", uppercase=False) == "HOME LAB"


def test_normalize_tag_name_deliberately_still_collapses_non_ascii_unicode_whitespace():
    # Documented, deliberate breadth, not an oversight: a non-breaking
    # space (U+00A0) is legitimate Unicode, not a prohibited ASCII
    # control or bidi/format character, and is still treated as
    # ordinary collapsible whitespace, consistent with allowing broad
    # legitimate Unicode elsewhere in a tag name.
    assert services.normalize_tag_name("HOME LAB", uppercase=False) == "HOME LAB"


@pytest.mark.parametrize(
    "raw",
    ["C#", "O365/ADMIN", "BACKUP-PLAN", "VMWARE/ESXI", "WINDOWS SERVER"],
)
def test_normalize_tag_name_allows_useful_punctuation(raw):
    assert services.normalize_tag_name(raw, uppercase=False) == raw


@pytest.mark.parametrize("raw", ["カフェ", "café", "Москва", "naïve"])
def test_normalize_tag_name_allows_broad_unicode(raw):
    assert services.normalize_tag_name(raw, uppercase=False) == raw


@pytest.mark.parametrize("code_point", list(range(0x00, 0x20)) + [0x7F])
def test_normalize_tag_name_rejects_every_ascii_control_character(code_point):
    # The full prohibited set, U+0000-U+001F and
    # U+007F, with no whitespace-classified exclusions -- every one of
    # these must be rejected, never silently collapsed into a space.
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name(f"Tag{chr(code_point)}Name", uppercase=False)


@pytest.mark.parametrize(
    "code_point",
    [0x061C, 0x200E, 0x200F, 0xFEFF] + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)),
)
def test_normalize_tag_name_rejects_bidi_and_format_control_characters(code_point):
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name(f"Tag{chr(code_point)}Name", uppercase=False)


def test_normalize_tag_name_accepts_exactly_40_characters():
    name = "A" * 40
    assert services.normalize_tag_name(name, uppercase=False) == name


def test_normalize_tag_name_rejects_41_characters():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("A" * 41, uppercase=False)


def test_normalize_tag_name_never_truncates_over_length_input():
    # A failure must be a clean rejection, never a silently shortened value.
    with pytest.raises(services.TagNameValidationError) as exc_info:
        services.normalize_tag_name("A" * 41, uppercase=False)
    assert "40" in str(exc_info.value)


def test_normalize_tag_name_length_check_applies_to_normalized_not_raw_length():
    # Uppercase normalization must not change the length outcome for
    # ordinary ASCII input -- the check is authoritative post-normalization.
    name = "a" * 40
    assert len(services.normalize_tag_name(name, uppercase=True)) == 40


def test_normalize_tag_name_rejects_blank_after_collapse():
    with pytest.raises(services.TagNameValidationError):
        services.normalize_tag_name("   ", uppercase=True)


# -- get_or_create_tag() case-insensitive reuse, preserved -------------------


@pytest.mark.django_db
def test_get_or_create_tag_reuse_still_works_after_normalization_is_applied_upstream():
    owner = create_account("normalize-reuse-owner")
    first = services.get_or_create_tag(
        owner=owner, name=services.normalize_tag_name("docker", uppercase=True), color="blue"
    )
    second = services.get_or_create_tag(
        owner=owner, name=services.normalize_tag_name("Docker", uppercase=True), color="rose"
    )

    assert first.pk == second.pk
    assert Tag.objects.filter(owner=owner, name__iexact="docker").count() == 1
    first.refresh_from_db()
    assert first.color == "blue"  # unchanged by the reuse


@pytest.mark.django_db(transaction=True)
def test_get_or_create_tag_concurrent_case_variant_creation_converges_on_one_row(monkeypatch):
    # A genuine two-thread race, mirroring this project's established
    # `test_purge_race.py` pattern: one real thread is paused, via
    # monkeypatch, immediately before its own `Tag.objects.create()`
    # call, while a second, concurrent creation with a case-variant name
    # completes fully -- then the paused thread is released and must hit
    # the existing `IntegrityError`-recovery path, converging on exactly
    # one row, exercising `get_or_create_tag()`'s pre-existing race
    # guarantee explicitly under the new normalization-driven call
    # pattern (case identity, not the normalization itself, is what's
    # racing here).
    owner = create_account("tag-race-owner")
    original_create = Tag.objects.create
    ready = threading.Event()
    proceed = threading.Event()

    call_count = {"n": 0}

    def pausing_create(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            ready.set()
            assert proceed.wait(timeout=5), "second creation never signaled proceed"
        return original_create(*args, **kwargs)

    monkeypatch.setattr(Tag.objects, "create", pausing_create)

    outcome_a: dict = {}

    def create_a():
        try:
            outcome_a["tag"] = services.get_or_create_tag(owner=owner, name="docker", color="blue")
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = threading.Thread(target=create_a)
    thread_a.start()
    assert ready.wait(timeout=5), "first thread never reached tag creation"

    tag_b = services.get_or_create_tag(owner=owner, name="DOCKER", color="rose")

    proceed.set()
    thread_a.join(timeout=5)

    assert "error" not in outcome_a
    assert Tag.objects.filter(owner=owner, name__iexact="docker").count() == 1
    assert outcome_a["tag"].pk == tag_b.pk


# -- assign_tag_to_note() 20-tag-per-note limit -------------------------------


@pytest.mark.django_db
def test_assign_tag_to_note_allows_the_twentieth_tag():
    owner = create_account("tag-limit-20-owner")
    note = services.create_note(owner=owner)
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)

    assert note.tags.count() == 20


@pytest.mark.django_db
def test_assign_tag_to_note_rejects_the_twenty_first_tag():
    owner = create_account("tag-limit-21-owner")
    note = services.create_note(owner=owner)
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)
    overflow_tag = services.get_or_create_tag(owner=owner, name="TAG20", color="rose")

    with pytest.raises(services.TagLimitExceededError):
        services.assign_tag_to_note(note=note, tag=overflow_tag)
    assert note.tags.count() == 20


@pytest.mark.django_db
def test_assign_tag_to_note_already_attached_tag_does_not_count_against_the_limit():
    owner = create_account("tag-limit-already-attached-owner")
    note = services.create_note(owner=owner)
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)
    already_attached = Tag.objects.get(owner=owner, name="TAG0")

    # Re-adding a tag already on the note at the limit must not raise.
    services.assign_tag_to_note(note=note, tag=already_attached)

    assert note.tags.count() == 20


@pytest.mark.django_db
def test_assign_tag_to_note_remove_then_add_succeeds_at_the_limit():
    owner = create_account("tag-limit-remove-then-add-owner")
    note = services.create_note(owner=owner)
    tags = []
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)
        tags.append(tag)

    services.remove_tag_from_note(note=note, tag=tags[0])
    replacement = services.get_or_create_tag(owner=owner, name="REPLACEMENT", color="rose")
    services.assign_tag_to_note(note=note, tag=replacement)

    assert note.tags.count() == 20
    assert replacement in note.tags.all()
    assert tags[0] not in note.tags.all()


@pytest.mark.django_db
def test_assign_tag_to_note_limit_is_owner_scoped_and_cannot_be_manipulated_cross_owner():
    owner = create_account("tag-limit-cross-owner-a")
    other = create_account("tag-limit-cross-owner-b")
    note = services.create_note(owner=owner)
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)

    # A tag belonging to a different owner cannot be attached at all
    # (the view/service layer never resolves a cross-owner tag), and the
    # limit itself is computed purely from this note's own relationships
    # -- confirmed here at the service layer directly.
    other_tag = services.get_or_create_tag(owner=other, name="OTHERTAG", color="rose")
    with pytest.raises(services.TagLimitExceededError):
        services.assign_tag_to_note(note=note, tag=other_tag)
    assert note.tags.count() == 20
    assert other_tag not in note.tags.all()


@pytest.mark.django_db
def test_note_tag_assign_view_twenty_first_tag_fails_safely():
    owner = create_account("view-tag-limit-owner")
    note = services.create_note(owner=owner)
    for index in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{index}", color="blue")
        services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "OneTagTooMany", "color": "blue"},
    )

    assert response.status_code == 400
    content = response.content.decode()
    assert "at most 20 tags" in content
    note.refresh_from_db()
    assert note.tags.count() == 20


# -- ordering, color, and removal regressions preserved ----------------------


@pytest.mark.django_db
def test_tag_alphabetical_case_insensitive_ordering_preserved():
    owner = create_account("tag-ordering-owner")
    services.get_or_create_tag(owner=owner, name="banana", color="blue")
    services.get_or_create_tag(owner=owner, name="Apple", color="green")
    services.get_or_create_tag(owner=owner, name="cherry", color="rose")

    names = list(Tag.objects.filter(owner=owner).values_list("name", flat=True))

    assert names == ["Apple", "banana", "cherry"]


@pytest.mark.django_db
def test_note_tags_all_are_alphabetically_ordered_regardless_of_assignment_order():
    owner = create_account("note-tag-ordering-owner")
    note = services.create_note(owner=owner)
    cherry = services.get_or_create_tag(owner=owner, name="cherry", color="blue")
    apple = services.get_or_create_tag(owner=owner, name="Apple", color="green")
    banana = services.get_or_create_tag(owner=owner, name="banana", color="rose")
    services.assign_tag_to_note(note=note, tag=cherry)
    services.assign_tag_to_note(note=note, tag=apple)
    services.assign_tag_to_note(note=note, tag=banana)

    assert list(note.tags.all()) == [apple, banana, cherry]


@pytest.mark.django_db
def test_note_tag_assign_view_normalization_never_changes_an_existing_tags_color():
    owner = create_account("tag-no-recolor-owner")
    existing = services.get_or_create_tag(owner=owner, name="DOCKER", color="violet")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "docker", "color": "blue"},
    )

    existing.refresh_from_db()
    assert existing.color == "violet"


@pytest.mark.django_db
def test_note_tag_remove_still_works_after_normalization_changes():
    owner = create_account("tag-remove-still-works-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(
        owner=owner, name=services.normalize_tag_name("docker", uppercase=True), color="blue"
    )
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_remove", args=[note.id]),
        {"tag_id": tag.id},
    )

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.tags.count() == 0


# ---------------------------------------------------------------------------
# Semantic initial tag color behavior
# ---------------------------------------------------------------------------

# -- semantic_tag_color() -----------------------------------------------


@pytest.mark.parametrize(
    ("alias", "expected_color"),
    [
        ("SLATE", "slate"),
        ("GRAY", "slate"),
        ("GREY", "slate"),
        ("BLUE", "blue"),
        ("TEAL", "teal"),
        ("CYAN", "teal"),
        ("GREEN", "green"),
        ("YELLOW", "yellow"),
        ("AMBER", "amber"),
        ("ORANGE", "orange"),
        ("RED", "red"),
        ("ROSE", "rose"),
        ("PINK", "rose"),
        ("VIOLET", "violet"),
        ("PURPLE", "violet"),
        ("BROWN", "brown"),
    ],
)
def test_semantic_tag_color_maps_each_exact_alias(alias, expected_color):
    assert services.semantic_tag_color(alias) == expected_color


@pytest.mark.parametrize(
    "near_miss",
    [
        "BLUEPRINT",
        "GREENHOUSE",
        "REDDIT",
        "AMBER ALERT",
        "PURPLE TEAM",
        "TEALIGHT",
        "BROWNIE",
        "ORANGEBOX",
        "YELLOWSTONE",
    ],
)
def test_semantic_tag_color_rejects_near_misses(near_miss):
    assert services.semantic_tag_color(near_miss) is None


def test_semantic_tag_color_is_none_for_ordinary_names():
    assert services.semantic_tag_color("SERVER") is None


def test_semantic_tag_color_never_normalizes_its_own_input():
    # Deliberately lowercase/whitespace-padded -- this function must
    # never trim/uppercase/collapse itself; only an already-normalized
    # candidate (uppercase, no padding) can match.
    assert services.semantic_tag_color("blue") is None
    assert services.semantic_tag_color(" BLUE") is None
    assert services.semantic_tag_color("BLUE ") is None


# -- resolve_tag_color_for_creation() ------------------------------------


def test_resolve_tag_color_uses_explicit_color_when_provided():
    assert services.resolve_tag_color_for_creation("blue", "RED") == "blue"


def test_resolve_tag_color_falls_back_to_semantic_match_when_no_explicit_color():
    assert services.resolve_tag_color_for_creation("", "RED") == "red"


def test_resolve_tag_color_falls_back_to_semantic_match_for_brown():
    assert services.resolve_tag_color_for_creation("", "BROWN") == "brown"


def test_resolve_tag_color_falls_back_to_default_when_no_explicit_and_no_semantic_match():
    assert services.resolve_tag_color_for_creation("", "SERVER") == "slate"


def test_resolve_tag_color_explicit_color_wins_even_with_a_semantic_match():
    assert services.resolve_tag_color_for_creation("violet", "BLUE") == "violet"


def test_resolve_tag_color_explicit_color_wins_for_an_ordinary_name_too():
    assert services.resolve_tag_color_for_creation("amber", "SERVER") == "amber"


def test_resolve_tag_color_semantic_disabled_falls_back_to_default():
    assert (
        services.resolve_tag_color_for_creation("", "RED", semantic_color_enabled=False) == "slate"
    )


def test_resolve_tag_color_semantic_disabled_still_honors_explicit_color():
    assert (
        services.resolve_tag_color_for_creation("blue", "RED", semantic_color_enabled=False)
        == "blue"
    )


# Caller-supplied default_color.


def test_resolve_tag_color_custom_default_used_when_no_explicit_and_no_semantic_match():
    assert services.resolve_tag_color_for_creation("", "SERVER", default_color="violet") == "violet"


def test_resolve_tag_color_custom_default_used_when_semantic_disabled():
    assert (
        services.resolve_tag_color_for_creation(
            "", "RED", semantic_color_enabled=False, default_color="violet"
        )
        == "violet"
    )


def test_resolve_tag_color_semantic_match_wins_over_custom_default():
    assert services.resolve_tag_color_for_creation("", "RED", default_color="violet") == "red"


def test_resolve_tag_color_explicit_wins_over_custom_default_and_semantic():
    assert (
        services.resolve_tag_color_for_creation("amber", "RED", default_color="violet") == "amber"
    )


def test_resolve_tag_color_no_default_color_argument_still_falls_back_to_slate():
    # Backward-compatibility: a caller that doesn't pass `default_color`
    # still gets the same fallback behavior.
    assert services.resolve_tag_color_for_creation("", "SERVER") == "slate"


# -- view integration: creation with semantic aliases --------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("typed_name", "expected_color"),
    [
        ("slate", "slate"),
        ("gray", "slate"),
        ("grey", "slate"),
        ("blue", "blue"),
        ("teal", "teal"),
        ("cyan", "teal"),
        ("green", "green"),
        ("yellow", "yellow"),
        ("amber", "amber"),
        ("orange", "orange"),
        ("red", "red"),
        ("rose", "rose"),
        ("pink", "rose"),
        ("violet", "violet"),
        ("purple", "violet"),
        ("brown", "brown"),
    ],
)
def test_note_tag_assign_view_new_tag_gets_semantic_color_with_default_selector(
    typed_name, expected_color
):
    owner = create_account(f"semantic-view-{typed_name}-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": typed_name, "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name__iexact=typed_name)
    assert tag.color == expected_color


@pytest.mark.django_db
def test_note_tag_assign_view_normalized_padded_name_still_gets_semantic_color():
    # Raw "  blue  " -> normalized "BLUE" -> semantic "blue", proving the
    # semantic lookup runs on the normalized candidate, not the
    # raw submitted string.
    owner = create_account("semantic-view-padded-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "  blue  ", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="BLUE")
    assert tag.color == "blue"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "near_miss",
    [
        "BLUEPRINT",
        "GREENHOUSE",
        "REDDIT",
        "AMBER ALERT",
        "PURPLE TEAM",
        "TEALIGHT",
        "BROWNIE",
        "ORANGEBOX",
        "YELLOWSTONE",
    ],
)
def test_note_tag_assign_view_near_miss_names_get_ordinary_default_color(near_miss):
    owner = create_account(f"semantic-near-miss-{abs(hash(near_miss))}-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": near_miss, "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name=near_miss)
    assert tag.color == "slate"


@pytest.mark.django_db
def test_note_tag_assign_view_explicit_color_overrides_semantic_match():
    owner = create_account("semantic-explicit-override-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": "blue"},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "blue"


@pytest.mark.django_db
def test_note_tag_assign_view_explicit_slate_overrides_semantic_match():
    owner = create_account("semantic-explicit-slate-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": "slate"},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "slate"


@pytest.mark.django_db
def test_note_tag_assign_view_explicit_violet_overrides_semantic_blue():
    owner = create_account("semantic-explicit-violet-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "BLUE", "color": "violet"},
    )

    tag = Tag.objects.get(owner=owner, name="BLUE")
    assert tag.color == "violet"


@pytest.mark.django_db
def test_note_tag_assign_view_explicit_color_with_ordinary_name():
    owner = create_account("semantic-explicit-ordinary-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "SERVER", "color": "amber"},
    )

    tag = Tag.objects.get(owner=owner, name="SERVER")
    assert tag.color == "amber"


@pytest.mark.django_db
def test_note_tag_assign_view_missing_color_field_behaves_as_default():
    owner = create_account("semantic-missing-color-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED"},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "red"


# -- existing-tag preservation --------------------------------------------


@pytest.mark.django_db
def test_note_tag_assign_view_existing_semantic_name_tag_never_recolored_with_default():
    owner = create_account("semantic-existing-default-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="blue")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "blue"


@pytest.mark.django_db
def test_note_tag_assign_view_existing_semantic_name_tag_never_recolored_with_explicit_selection():
    owner = create_account("semantic-existing-explicit-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="blue")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": "rose"},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "blue"


@pytest.mark.django_db
def test_note_tag_assign_view_existing_tag_reused_case_insensitively_never_recolored():
    owner = create_account("semantic-existing-ci-owner")
    services.get_or_create_tag(owner=owner, name="Red", color="blue")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "red", "color": ""},
    )

    assert Tag.objects.filter(owner=owner, name__iexact="red").count() == 1
    tag = Tag.objects.get(owner=owner, name__iexact="red")
    assert tag.color == "blue"


# -- form: blank/default/invalid color ------------------------------------


def test_note_tag_assign_form_blank_color_is_valid():
    form = NoteTagAssignForm({"name": "Server", "color": ""})

    assert form.is_valid()
    assert form.cleaned_data["color"] == ""


def test_note_tag_assign_form_missing_color_is_valid_and_blank():
    form = NoteTagAssignForm({"name": "Server"})

    assert form.is_valid()
    assert form.cleaned_data["color"] == ""


@pytest.mark.parametrize(
    "color",
    [
        "slate",
        "blue",
        "teal",
        "green",
        "yellow",
        "amber",
        "orange",
        "red",
        "rose",
        "violet",
        "brown",
    ],
)
def test_note_tag_assign_form_accepts_every_valid_explicit_color(color):
    form = NoteTagAssignForm({"name": "Server", "color": color})

    assert form.is_valid()
    assert form.cleaned_data["color"] == color


def test_note_tag_assign_form_rejects_invalid_nonblank_color():
    form = NoteTagAssignForm({"name": "Server", "color": "chartreuse"})

    assert not form.is_valid()
    assert "color" in form.errors


# -- Owner-aware uppercase preference --------------


def test_note_tag_assign_form_without_user_still_uppercases_by_default():
    # Backward-compatibility: no real call site does this,
    # but a form built without `user=` must behave exactly as it
    # otherwise would, rather than raising.
    form = NoteTagAssignForm({"name": "server room", "color": ""})

    assert form.is_valid()
    assert form.cleaned_data["name"] == "SERVER ROOM"


@pytest.mark.django_db
def test_note_tag_assign_form_uppercases_when_owner_preference_is_on():
    owner = create_account("form-uppercase-on-owner")

    form = NoteTagAssignForm({"name": "server room", "color": ""}, user=owner)

    assert form.is_valid()
    assert form.cleaned_data["name"] == "SERVER ROOM"


@pytest.mark.django_db
def test_note_tag_assign_form_preserves_case_when_owner_preference_is_off():
    owner = create_account("form-uppercase-off-owner", tag_uppercase_enabled=False)

    form = NoteTagAssignForm({"name": "Server Room", "color": ""}, user=owner)

    assert form.is_valid()
    assert form.cleaned_data["name"] == "Server Room"


@pytest.mark.django_db
def test_note_tag_assign_form_uppercase_off_still_rejects_prohibited_characters():
    owner = create_account("form-uppercase-off-prohibited-owner", tag_uppercase_enabled=False)

    form = NoteTagAssignForm({"name": "bad\tname", "color": ""}, user=owner)

    assert not form.is_valid()


# -- concurrency -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_semantic_creation_converges_on_one_row_with_no_post_race_recolor(monkeypatch):
    # Two threads race to create the same normalized tag name, both with
    # no explicit color selected -- both independently resolve the same
    # semantic color, but the point is the *loser* must never write
    # anything: it must reuse whatever row actually got persisted,
    # exactly as `get_or_create_tag()` already guarantees. Mirrors the
    # exact pattern established by the identity/canonicalization create-race test.
    owner = create_account("semantic-race-owner")
    original_create = Tag.objects.create
    ready = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}

    def pausing_create(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            ready.set()
            assert proceed.wait(timeout=5), "second creation never signaled proceed"
        return original_create(*args, **kwargs)

    monkeypatch.setattr(Tag.objects, "create", pausing_create)

    outcome_a: dict = {}

    def create_a():
        try:
            name = services.normalize_tag_name("red", uppercase=True)
            color = services.resolve_tag_color_for_creation("", name)
            outcome_a["tag"] = services.get_or_create_tag(owner=owner, name=name, color=color)
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = threading.Thread(target=create_a)
    thread_a.start()
    assert ready.wait(timeout=5), "first thread never reached tag creation"

    name = services.normalize_tag_name("RED", uppercase=True)
    color = services.resolve_tag_color_for_creation("", name)
    tag_b = services.get_or_create_tag(owner=owner, name=name, color=color)

    proceed.set()
    thread_a.join(timeout=5)

    assert "error" not in outcome_a
    assert Tag.objects.filter(owner=owner, name__iexact="red").count() == 1
    assert outcome_a["tag"].pk == tag_b.pk
    assert outcome_a["tag"].color == "red"


# -- owner isolation / no unrelated mutation -------------------------------


def test_resolve_tag_color_for_creation_takes_no_owner_and_cannot_see_cross_owner_data():
    # Purely a signature/contract check: this function is pure, has no
    # `owner` parameter, and performs no query -- confirmed by direct
    # inspection of its call, not just its behavior.
    import inspect

    parameters = inspect.signature(services.resolve_tag_color_for_creation).parameters
    assert "owner" not in parameters


@pytest.mark.django_db
def test_note_tag_assign_view_semantic_creation_does_not_affect_other_owners_tags():
    owner = create_account("semantic-isolation-owner")
    other = create_account("semantic-isolation-other")
    services.get_or_create_tag(owner=other, name="RED", color="violet")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    owner_tag = Tag.objects.get(owner=owner, name="RED")
    other_tag = Tag.objects.get(owner=other, name="RED")
    assert owner_tag.color == "red"
    assert other_tag.color == "violet"  # completely unaffected


@pytest.mark.django_db
def test_note_tag_assign_view_semantic_creation_does_not_mutate_unrelated_existing_tags():
    owner = create_account("semantic-unrelated-owner")
    unrelated = services.get_or_create_tag(owner=owner, name="Personal", color="amber")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    unrelated.refresh_from_db()
    assert unrelated.color == "amber"


# ---------------------------------------------------------------------------
# Tag Preferences: note_tag_assign() view
# integration under each preference. Preference storage/form/view
# coverage itself lives in accounts/tests/test_preferences_tags.py; this
# section is scoped to how note-detail Add Tag's creation actually
# behaves once wired to an owner's real preferences.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_tag_assign_view_uppercase_off_preserves_entered_case():
    owner = create_account("view-uppercase-off-owner", tag_uppercase_enabled=False)
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "Server Room", "color": ""},
    )

    assert Tag.objects.filter(owner=owner, name="Server Room").exists()
    assert not Tag.objects.filter(owner=owner, name="SERVER ROOM").exists()


@pytest.mark.django_db
def test_note_tag_assign_view_uppercase_off_still_enforces_case_insensitive_collision():
    owner = create_account("view-uppercase-off-collision-owner", tag_uppercase_enabled=False)
    services.get_or_create_tag(owner=owner, name="Server", color="red")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "SERVER", "color": ""},
    )

    # Case-insensitive reuse still applies regardless of the uppercase
    # preference -- attaches the existing tag, never creates a second,
    # differently-cased duplicate.
    assert response.status_code == 302
    assert Tag.objects.filter(owner=owner, name__iexact="server").count() == 1
    note.refresh_from_db()
    assert list(note.tags.values_list("name", flat=True)) == ["Server"]


@pytest.mark.django_db
def test_search_tags_for_owner_matches_mixed_case_stored_tags():
    owner = create_account("view-uppercase-off-autocomplete-owner", tag_uppercase_enabled=False)
    services.get_or_create_tag(owner=owner, name="Server Room", color="red")

    results = services.search_tags_for_owner(owner=owner, query="server")

    assert [tag.name for tag in results] == ["Server Room"]


@pytest.mark.django_db
def test_note_tag_assign_view_semantic_off_falls_back_to_default_not_semantic():
    owner = create_account("view-semantic-off-owner", tag_semantic_color_enabled=False)
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "slate"


@pytest.mark.django_db
def test_note_tag_assign_view_semantic_on_still_applies_when_enabled():
    owner = create_account("view-semantic-on-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "red"


@pytest.mark.django_db
def test_note_tag_assign_view_custom_default_color_applies_to_ordinary_name():
    owner = create_account("view-custom-default-owner", tag_default_color="violet")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "SERVER", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="SERVER")
    assert tag.color == "violet"


@pytest.mark.django_db
def test_note_tag_assign_view_semantic_wins_over_custom_default():
    owner = create_account("view-semantic-vs-default-owner", tag_default_color="violet")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": ""},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "red"


@pytest.mark.django_db
def test_note_tag_assign_view_explicit_wins_over_semantic_and_custom_default():
    owner = create_account("view-explicit-wins-owner", tag_default_color="violet")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "RED", "color": "amber"},
    )

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "amber"


@pytest.mark.django_db
def test_note_tag_assign_view_preferences_are_owner_scoped():
    owner = create_account(
        "view-prefs-scope-owner",
        tag_uppercase_enabled=False,
        tag_semantic_color_enabled=False,
        tag_default_color="violet",
    )
    other = create_account("view-prefs-scope-other")
    note = services.create_note(owner=other)

    authenticated_client(other).post(
        reverse("notes:note_tag_assign", args=[note.id]),
        {"name": "red", "color": ""},
    )

    # `other` never touched `owner`'s preferences -- their own tag still
    # creates with the codebase's real defaults (uppercase, semantic).
    tag = Tag.objects.get(owner=other, name="RED")
    assert tag.color == "red"
    assert Tag.objects.filter(owner=owner).count() == 0
