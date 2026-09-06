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


# -- duplicate_note (service) ----------------------------------------------


@pytest.mark.django_db
def test_duplicate_note_creates_new_note_owned_by_same_user():
    owner = create_account("dup-owner")
    note = services.create_note(owner=owner)

    duplicate = services.duplicate_note(note=note)

    assert duplicate.id != note.id
    assert duplicate.owner_id == owner.id


@pytest.mark.django_db
def test_duplicate_note_copies_body_fields_and_editor_schema_version():
    owner = create_account("dup-body-owner")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(
        body_json={"type": "doc", "content": [{"type": "paragraph", "content": []}]},
        body_plain_text="Original body text",
        editor_schema_version=1,
    )
    note.refresh_from_db()

    duplicate = services.duplicate_note(note=note)

    assert duplicate.body_json == note.body_json
    assert duplicate.body_plain_text == note.body_plain_text
    assert duplicate.editor_schema_version == note.editor_schema_version


@pytest.mark.django_db
def test_duplicate_note_copies_folder_placement():
    owner = create_account("dup-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    duplicate = services.duplicate_note(note=note)

    assert duplicate.folder_id == folder.id


@pytest.mark.django_db
def test_duplicate_note_unfiled_note_stays_unfiled():
    owner = create_account("dup-unfiled-owner")
    note = services.create_note(owner=owner)

    duplicate = services.duplicate_note(note=note)

    assert duplicate.folder_id is None


@pytest.mark.django_db
def test_duplicate_note_copies_assigned_tags():
    owner = create_account("dup-tags-owner")
    note = services.create_note(owner=owner)
    tag_one = services.get_or_create_tag(owner=owner, name="Work", color="blue")
    tag_two = services.get_or_create_tag(owner=owner, name="Urgent", color="rose")
    services.assign_tag_to_note(note=note, tag=tag_one)
    services.assign_tag_to_note(note=note, tag=tag_two)

    duplicate = services.duplicate_note(note=note)

    assert set(duplicate.tags.values_list("id", flat=True)) == {tag_one.id, tag_two.id}


@pytest.mark.django_db
def test_duplicate_note_untagged_note_has_no_tags():
    owner = create_account("dup-no-tags-owner")
    note = services.create_note(owner=owner)

    duplicate = services.duplicate_note(note=note)

    assert list(duplicate.tags.all()) == []


@pytest.mark.django_db
def test_duplicate_note_does_not_copy_pinned_state():
    owner = create_account("dup-pin-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    duplicate = services.duplicate_note(note=note)

    assert duplicate.pinned is False


@pytest.mark.django_db
def test_duplicate_note_new_duplicate_starts_unpinned_even_if_source_unpinned():
    owner = create_account("dup-unpinned-source-owner")
    note = services.create_note(owner=owner)

    duplicate = services.duplicate_note(note=note)

    assert duplicate.pinned is False


@pytest.mark.django_db
def test_duplicate_note_does_not_mutate_source_note():
    owner = create_account("dup-source-unchanged-owner")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(title="Original Title")
    note.refresh_from_db()
    original_version = note.version
    original_modified_at = note.modified_at
    original_title = note.title

    services.duplicate_note(note=note)

    note.refresh_from_db()
    assert note.title == original_title
    assert note.version == original_version
    assert note.modified_at == original_modified_at


@pytest.mark.django_db
def test_duplicate_note_title_convention_first_copy():
    owner = create_account("dup-title-owner")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(title="Meeting Notes")
    note.refresh_from_db()

    duplicate = services.duplicate_note(note=note)

    assert duplicate.title == "Copy of Meeting Notes"


@pytest.mark.django_db
def test_duplicate_note_title_convention_increments_on_conflict():
    owner = create_account("dup-title-conflict-owner")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(title="Meeting Notes")
    note.refresh_from_db()

    first_copy = services.duplicate_note(note=note)
    second_copy = services.duplicate_note(note=note)
    third_copy = services.duplicate_note(note=note)

    assert first_copy.title == "Copy of Meeting Notes"
    assert second_copy.title == "Copy 2 of Meeting Notes"
    assert third_copy.title == "Copy 3 of Meeting Notes"


@pytest.mark.django_db
def test_duplicate_note_title_conflict_matching_is_owner_scoped():
    owner = create_account("dup-title-scope-owner")
    other = create_account("dup-title-scope-other")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(title="Meeting Notes")
    note.refresh_from_db()
    # Another owner already has a note with the exact candidate title; this
    # must not affect the first owner's duplicate numbering.
    Note.objects.create(
        owner=other,
        title="Copy of Meeting Notes",
        body_json=note.body_json,
        body_plain_text=note.body_plain_text,
        editor_schema_version=note.editor_schema_version,
        version=1,
    )

    duplicate = services.duplicate_note(note=note)

    assert duplicate.title == "Copy of Meeting Notes"
    assert duplicate.owner_id == owner.id


@pytest.mark.django_db
def test_duplicate_note_title_length_edge_case_is_safely_truncated():
    owner = create_account("dup-title-length-owner")
    note = services.create_note(owner=owner)
    long_title = "A" * 255
    Note.objects.filter(pk=note.pk).update(title=long_title)
    note.refresh_from_db()

    duplicate = services.duplicate_note(note=note)

    assert len(duplicate.title) <= 255
    assert duplicate.title.startswith("Copy of ")


# -- note_duplicate view ----------------------------------------------------


@pytest.mark.django_db
def test_note_duplicate_view_creates_duplicate_and_redirects_to_new_note_detail():
    owner = create_account("view-dup-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 302
    new_note = Note.objects.exclude(pk=note.pk).get(owner=owner)
    assert response.url == reverse("notes:detail", args=[new_note.id])


@pytest.mark.django_db
def test_note_duplicate_view_is_owner_scoped():
    owner = create_account("view-dup-scope-owner")
    other = create_account("view-dup-scope-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))
    new_note_id = int(response.url.rstrip("/").rsplit("/", 1)[-1])
    new_note = Note.objects.get(pk=new_note_id)

    assert new_note.owner_id == owner.id
    assert new_note.owner_id != other.id


@pytest.mark.django_db
def test_note_duplicate_view_cross_owner_returns_not_found():
    owner = create_account("view-dup-cross-owner")
    other = create_account("view-dup-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 404
    assert Note.objects.filter(owner=other).count() == 0


@pytest.mark.django_db
def test_note_duplicate_view_get_is_not_allowed():
    owner = create_account("view-dup-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 405
    assert Note.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_note_duplicate_view_unauthenticated_post_is_blocked():
    owner = create_account("view-dup-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 302
    assert Note.objects.filter(owner=owner).count() == 1
