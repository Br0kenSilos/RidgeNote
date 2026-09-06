import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.forms import NoteRenameForm
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


# -- NoteRenameForm ------------------------------------------------------------


def test_note_rename_form_trims_surrounding_whitespace():
    form = NoteRenameForm({"title": "   Padded Title   "})
    assert form.is_valid()
    assert form.cleaned_data["title"] == "Padded Title"


def test_note_rename_form_rejects_blank_title():
    form = NoteRenameForm({"title": ""})
    assert not form.is_valid()
    assert "title" in form.errors


def test_note_rename_form_rejects_whitespace_only_title():
    form = NoteRenameForm({"title": "   "})
    assert not form.is_valid()
    assert "title" in form.errors


def test_note_rename_form_enforces_max_length():
    form = NoteRenameForm({"title": "x" * 256})
    assert not form.is_valid()
    assert "title" in form.errors


def test_note_rename_form_accepts_max_length_title():
    form = NoteRenameForm({"title": "x" * 255})
    assert form.is_valid()


def test_note_rename_form_accepts_duplicate_titles():
    # Note titles are not unique; the form has no uniqueness validation at all.
    form_a = NoteRenameForm({"title": "Same Title"})
    form_b = NoteRenameForm({"title": "Same Title"})
    assert form_a.is_valid()
    assert form_b.is_valid()


# -- rename_note() service ------------------------------------------------------


@pytest.mark.django_db
def test_rename_note_updates_title():
    owner = create_account("rename-service-owner")
    note = services.create_note(owner=owner)

    renamed = services.rename_note(note=note, title="New Title")

    assert renamed.title == "New Title"
    note.refresh_from_db()
    assert note.title == "New Title"


@pytest.mark.django_db
def test_rename_note_trims_whitespace():
    owner = create_account("rename-service-trim-owner")
    note = services.create_note(owner=owner)

    services.rename_note(note=note, title="  Padded  ")

    note.refresh_from_db()
    assert note.title == "Padded"


@pytest.mark.django_db
def test_rename_note_rejects_blank_title():
    owner = create_account("rename-service-blank-owner")
    note = services.create_note(owner=owner)
    original_title = note.title

    with pytest.raises(ValueError):
        services.rename_note(note=note, title="   ")

    note.refresh_from_db()
    assert note.title == original_title


@pytest.mark.django_db
def test_rename_note_preserves_body():
    owner = create_account("rename-service-body-owner")
    note = services.create_note(owner=owner)
    original_body_json = note.body_json
    original_body_plain_text = note.body_plain_text

    services.rename_note(note=note, title="Renamed")

    note.refresh_from_db()
    assert note.body_json == original_body_json
    assert note.body_plain_text == original_body_plain_text


@pytest.mark.django_db
def test_rename_note_preserves_folder():
    owner = create_account("rename-service-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    services.rename_note(note=note, title="Renamed")

    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_rename_note_preserves_pinned_state():
    owner = create_account("rename-service-pin-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    services.rename_note(note=note, title="Renamed")

    note.refresh_from_db()
    assert note.pinned is True


@pytest.mark.django_db
def test_rename_note_does_not_change_version():
    owner = create_account("rename-service-version-owner")
    note = services.create_note(owner=owner)
    original_version = note.version

    services.rename_note(note=note, title="Renamed")

    note.refresh_from_db()
    assert note.version == original_version


@pytest.mark.django_db
def test_rename_note_does_not_change_modified_at():
    # Precedent: assign_note_folder() and set_note_pinned() both save with an
    # explicit update_fields list that excludes modified_at, so folder moves
    # and pin toggles never touch it despite Note.modified_at's auto_now=True.
    # rename_note() follows that same established precedent.
    owner = create_account("rename-service-modified-owner")
    note = services.create_note(owner=owner)
    original_modified_at = note.modified_at

    services.rename_note(note=note, title="Renamed")

    note.refresh_from_db()
    assert note.modified_at == original_modified_at


@pytest.mark.django_db
def test_rename_note_allows_duplicate_titles_across_notes():
    owner = create_account("rename-service-dup-owner")
    note_a = services.create_note(owner=owner)
    note_b = services.create_note(owner=owner)
    Note.objects.filter(pk=note_a.pk).update(title="Original A")

    services.rename_note(note=note_b, title="Original A")

    note_b.refresh_from_db()
    assert note_b.title == "Original A"


# -- notes:note_rename (note-detail/tree context) -------------------------------


@pytest.mark.django_db
def test_note_rename_view_redirects_to_note_detail():
    owner = create_account("rename-view-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Renamed Note"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    note.refresh_from_db()
    assert note.title == "Renamed Note"


@pytest.mark.django_db
def test_note_rename_view_cross_owner_note_returns_404():
    owner = create_account("rename-view-cross-owner")
    other = create_account("rename-view-cross-other")
    other_note = services.create_note(owner=other)
    original_title = other_note.title

    response = authenticated_client(owner).post(
        reverse("notes:note_rename", args=[other_note.id]), {"title": "Hijacked"}
    )

    assert response.status_code == 404
    other_note.refresh_from_db()
    assert other_note.title == original_title


@pytest.mark.django_db
def test_note_rename_view_blank_title_creates_no_change_and_shows_message():
    owner = create_account("rename-view-blank-owner")
    note = services.create_note(owner=owner)
    original_title = note.title

    response = authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "   "}, follow=True
    )

    note.refresh_from_db()
    assert note.title == original_title
    assert b"Could not rename the note" in response.content


@pytest.mark.django_db
def test_note_rename_view_get_is_not_allowed():
    owner = create_account("rename-view-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_rename", args=[note.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_note_rename_view_unauthenticated_post_is_blocked():
    owner = create_account("rename-view-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Should Not Work"}
    )

    assert response.status_code in (302, 403)
    note.refresh_from_db()
    assert note.title != "Should Not Work"


# -- notes:note_rename_home (Home context) --------------------------------------


@pytest.mark.django_db
def test_note_rename_home_view_redirects_to_home():
    owner = create_account("rename-home-view-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "Renamed From Home"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    note.refresh_from_db()
    assert note.title == "Renamed From Home"


@pytest.mark.django_db
def test_note_rename_home_view_cross_owner_note_returns_404():
    owner = create_account("rename-home-view-cross-owner")
    other = create_account("rename-home-view-cross-other")
    other_note = services.create_note(owner=other)
    original_title = other_note.title

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[other_note.id]), {"title": "Hijacked"}
    )

    assert response.status_code == 404
    other_note.refresh_from_db()
    assert other_note.title == original_title


@pytest.mark.django_db
def test_note_rename_home_view_blank_title_creates_no_change_and_shows_message():
    owner = create_account("rename-home-view-blank-owner")
    note = services.create_note(owner=owner)
    original_title = note.title

    response = authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "   "}, follow=True
    )

    note.refresh_from_db()
    assert note.title == original_title
    assert b"Could not rename the note" in response.content


@pytest.mark.django_db
def test_note_rename_home_view_get_is_not_allowed():
    owner = create_account("rename-home-view-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_rename_home", args=[note.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_note_rename_home_view_unauthenticated_post_is_blocked():
    owner = create_account("rename-home-view-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "Should Not Work"}
    )

    assert response.status_code in (302, 403)
    note.refresh_from_db()
    assert note.title != "Should Not Work"


@pytest.mark.django_db
def test_note_rename_does_not_change_folder_pin_or_body():
    owner = create_account("rename-view-side-effects-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.set_note_pinned(note=note, pinned=True)
    original_body_json = note.body_json
    original_version = note.version

    authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "Renamed"}
    )

    note.refresh_from_db()
    assert note.title == "Renamed"
    assert note.folder_id == folder.id
    assert note.pinned is True
    assert note.body_json == original_body_json
    assert note.version == original_version
