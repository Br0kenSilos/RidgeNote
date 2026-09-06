import re

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.models.deletion import RestrictedError
from django.test import Client
from django.test.utils import CaptureQueriesContext
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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _flash_messages_html(content: str) -> str:
    """Isolates the Django-messages `<ul class="messages">` region, if
    rendered, rather than searching the whole page -- the in-app Help
    panel (rendered on every page) contains prose that can otherwise
    collide with message-text assertions."""
    marker = 'class="messages"'
    if marker not in content:
        return ""
    start = content.index(marker)
    start = content.rindex("<ul", 0, start)
    end = content.index("</ul>", start) + len("</ul>")
    return content[start:end]


def _move_select_html(content: str, *, id_prefix: str = "overflow") -> str:
    """Isolates one note-move `<select>`'s own markup (its destination
    options), bounded by its closing tag, rather than searching the
    whole page for folder-name text that may also appear in unrelated
    global content such as the Help panel."""
    marker = f'id="{id_prefix}-move-folder"'
    start = content.rindex("<select", 0, content.index(marker))
    end = content.index("</select>", start) + len("</select>")
    return content[start:end]


def _content_excluding_help_panel(content: str) -> str:
    """Strips the globally-rendered in-app Help panel (`#help-panel`, one
    per page) out of `content` before running whole-page structural
    counts, so Help's own documentation prose -- which legitimately
    mentions the same words/labels as real UI controls -- cannot be
    miscounted as one of those controls."""
    marker = 'id="help-panel"'
    if marker not in content:
        return content
    start = content.rindex("<dialog", 0, content.index(marker))
    end = content.index("</dialog>", start) + len("</dialog>")
    return content[:start] + content[end:]


class _FakeDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeDriverError(Exception):
    def __init__(self, constraint_name):
        super().__init__("fake driver error")
        self.diag = _FakeDiag(constraint_name)


class _AlwaysEmptyQuerySet:
    def exclude(self, **kwargs):
        return self

    def exists(self):
        return False


def _details_open_at_marker(content: str, marker: str, occurrence: int = 0) -> bool:
    idx = content.index(marker)
    for _ in range(occurrence):
        idx = content.index(marker, idx + 1)
    details_start = content.rfind("<details", 0, idx)
    tag_end = content.index(">", details_start)
    return "open" in content[details_start:tag_end]


# -- Folder creation ----------------------------------------------------


@pytest.mark.django_db
def test_create_folder_trims_name():
    owner = create_account("trim-owner")

    folder = services.create_folder(owner=owner, name="  Projects  ")

    assert folder.name == "Projects"


@pytest.mark.django_db
def test_create_folder_rejects_blank_name():
    owner = create_account("blank-owner")

    with pytest.raises(ValueError):
        services.create_folder(owner=owner, name="   ")

    assert not Folder.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_create_folder_rejects_case_insensitive_duplicate_for_same_owner():
    owner = create_account("dup-case-owner")
    services.create_folder(owner=owner, name="Work")

    with pytest.raises(services.FolderNameConflictError):
        services.create_folder(owner=owner, name="work")

    assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_create_folder_allows_same_name_for_different_owners():
    owner_a = create_account("owner-a")
    owner_b = create_account("owner-b")
    services.create_folder(owner=owner_a, name="Shared Name")

    folder_b = services.create_folder(owner=owner_b, name="Shared Name")

    assert folder_b.name == "Shared Name"


@pytest.mark.django_db
def test_folder_queryset_orders_case_insensitively():
    owner = create_account("order-owner")
    services.create_folder(owner=owner, name="cherry")
    services.create_folder(owner=owner, name="Apple")
    services.create_folder(owner=owner, name="banana")

    names = list(services.list_folders_for_owner(owner=owner).values_list("name", flat=True))

    assert names == ["Apple", "banana", "cherry"]


# -- Race-path integrity handling ----------------------------------------


def test_integrity_error_is_remapped_only_for_named_constraint():
    exc = IntegrityError("duplicate key")
    exc.__cause__ = _FakeDriverError("notes_folder_owner_name_ci_uq")

    assert services._integrity_error_is_folder_name_conflict(exc) is True


def test_unrelated_integrity_error_is_not_remapped():
    exc = IntegrityError("fk violation")
    exc.__cause__ = _FakeDriverError("some_other_constraint")

    assert services._integrity_error_is_folder_name_conflict(exc) is False


@pytest.mark.django_db
def test_create_folder_race_path_still_raises_folder_name_conflict_error(monkeypatch):
    owner = create_account("race-owner")
    Folder.objects.create(owner=owner, name="Existing")

    monkeypatch.setattr(
        services.Folder.objects,
        "filter",
        lambda **kwargs: _AlwaysEmptyQuerySet(),
    )

    with pytest.raises(services.FolderNameConflictError):
        services.create_folder(owner=owner, name="existing")


@pytest.mark.django_db
def test_create_folder_reraises_unrelated_integrity_error(monkeypatch):
    owner = create_account("unrelated-owner")

    def _raise_unrelated(*args, **kwargs):
        exc = IntegrityError("some other constraint violated")
        exc.__cause__ = _FakeDriverError("some_other_constraint")
        raise exc

    monkeypatch.setattr(services.Folder.objects, "create", _raise_unrelated)

    with pytest.raises(IntegrityError):
        services.create_folder(owner=owner, name="Anything")


# -- Folder rename (service) ---------------------------------------------


@pytest.mark.django_db
def test_rename_folder_trims_name():
    owner = create_account("rename-trim-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    renamed = services.rename_folder(folder=folder, name="  Archive  ")

    assert renamed.name == "Archive"
    folder.refresh_from_db()
    assert folder.name == "Archive"


@pytest.mark.django_db
def test_rename_folder_rejects_blank_name():
    owner = create_account("rename-blank-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    with pytest.raises(ValueError):
        services.rename_folder(folder=folder, name="   ")

    folder.refresh_from_db()
    assert folder.name == "Projects"


@pytest.mark.django_db
def test_rename_folder_unchanged_name_is_a_successful_no_op():
    owner = create_account("rename-noop-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    renamed = services.rename_folder(folder=folder, name="Projects")

    assert renamed.name == "Projects"
    folder.refresh_from_db()
    assert folder.name == "Projects"


@pytest.mark.django_db
def test_rename_folder_allows_case_only_self_rename():
    owner = create_account("rename-case-owner")
    folder = services.create_folder(owner=owner, name="projects")

    renamed = services.rename_folder(folder=folder, name="Projects")

    assert renamed.name == "Projects"
    folder.refresh_from_db()
    assert folder.name == "Projects"


@pytest.mark.django_db
def test_rename_folder_rejects_case_insensitive_duplicate_with_another_folder():
    owner = create_account("rename-dup-owner")
    services.create_folder(owner=owner, name="Work")
    other_folder = services.create_folder(owner=owner, name="Personal")

    with pytest.raises(services.FolderNameConflictError):
        services.rename_folder(folder=other_folder, name="work")

    other_folder.refresh_from_db()
    assert other_folder.name == "Personal"


@pytest.mark.django_db
def test_rename_folder_allows_same_name_across_different_owners():
    owner_a = create_account("rename-owner-a")
    owner_b = create_account("rename-owner-b")
    services.create_folder(owner=owner_a, name="Shared Name")
    folder_b = services.create_folder(owner=owner_b, name="Other Name")

    renamed = services.rename_folder(folder=folder_b, name="Shared Name")

    assert renamed.name == "Shared Name"


@pytest.mark.django_db
def test_rename_folder_race_path_still_raises_folder_name_conflict_error(monkeypatch):
    owner = create_account("rename-race-owner")
    folder = services.create_folder(owner=owner, name="Existing")
    other_folder = services.create_folder(owner=owner, name="Other")

    monkeypatch.setattr(
        services.Folder.objects,
        "filter",
        lambda **kwargs: _AlwaysEmptyQuerySet(),
    )

    with pytest.raises(services.FolderNameConflictError):
        services.rename_folder(folder=other_folder, name=folder.name)


@pytest.mark.django_db
def test_rename_folder_reraises_unrelated_integrity_error(monkeypatch):
    owner = create_account("rename-unrelated-owner")
    folder = services.create_folder(owner=owner, name="Original")

    def _raise_unrelated(*args, **kwargs):
        exc = IntegrityError("some other constraint violated")
        exc.__cause__ = _FakeDriverError("some_other_constraint")
        raise exc

    monkeypatch.setattr(folder, "save", _raise_unrelated)

    with pytest.raises(IntegrityError):
        services.rename_folder(folder=folder, name="New Name")


@pytest.mark.django_db
def test_renamed_folders_re_sort_case_insensitively():
    owner = create_account("rename-sort-owner")
    folder_a = services.create_folder(owner=owner, name="Apple")
    folder_z = services.create_folder(owner=owner, name="Zebra")

    services.rename_folder(folder=folder_z, name="Aardvark")

    names = list(services.list_folders_for_owner(owner=owner).values_list("name", flat=True))
    assert names == ["Aardvark", "Apple"]
    assert folder_a.name == "Apple"


# -- RESTRICT deletion behavior -------------------------------------------


@pytest.mark.django_db
def test_folder_delete_blocked_while_notes_still_reference_it():
    owner = create_account("restrict-owner")
    folder = services.create_folder(owner=owner, name="Work")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    with pytest.raises(RestrictedError):
        folder.delete()

    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_deleting_owner_cascades_folders_and_notes_without_error():
    owner = create_account("cascade-owner")
    folder = services.create_folder(owner=owner, name="Work")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    owner.delete()

    assert not Folder.objects.filter(pk=folder.pk).exists()
    assert not services.Note.objects.filter(pk=note.pk).exists()


# -- Moving notes ----------------------------------------------------------


@pytest.mark.django_db
def test_assign_note_folder_moves_note_into_folder():
    owner = create_account("move-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    services.assign_note_folder(note=note, folder=folder)

    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_assign_note_folder_moves_between_folders():
    owner = create_account("between-owner")
    note = services.create_note(owner=owner)
    folder_a = services.create_folder(owner=owner, name="A")
    folder_b = services.create_folder(owner=owner, name="B")
    services.assign_note_folder(note=note, folder=folder_a)

    services.assign_note_folder(note=note, folder=folder_b)

    note.refresh_from_db()
    assert note.folder_id == folder_b.id


@pytest.mark.django_db
def test_assign_note_folder_moves_back_to_unfiled():
    owner = create_account("unfiled-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    services.assign_note_folder(note=note, folder=None)

    note.refresh_from_db()
    assert note.folder_id is None


# -- Active note may never reference a trashed folder --


@pytest.mark.django_db
def test_assign_note_folder_rejects_an_already_trashed_destination():
    owner = create_account("move-trashed-dest-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Trashed Destination")
    services.move_folder_to_trash(folder=folder)

    with pytest.raises(services.TrashedItemMutationError):
        services.assign_note_folder(note=note, folder=folder)

    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_assign_note_folder_rejects_a_folder_owned_by_another_user():
    owner = create_account("move-cross-owner-owner")
    other_owner = create_account("move-cross-owner-other")
    note = services.create_note(owner=owner)
    other_folder = services.create_folder(owner=other_owner, name="Not Mine")

    with pytest.raises(services.TrashedItemMutationError):
        services.assign_note_folder(note=note, folder=other_folder)

    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_create_note_rejects_an_already_trashed_folder():
    owner = create_account("create-trashed-owner")
    folder = services.create_folder(owner=owner, name="Trashed")
    services.move_folder_to_trash(folder=folder)
    before_count = Note.objects.filter(owner=owner).count()

    with pytest.raises(services.TrashedItemMutationError):
        services.create_note(owner=owner, folder=folder)

    assert Note.objects.filter(owner=owner).count() == before_count


@pytest.mark.django_db
def test_create_note_rejects_a_folder_owned_by_another_user():
    owner = create_account("create-cross-owner-owner")
    other_owner = create_account("create-cross-owner-other")
    other_folder = services.create_folder(owner=other_owner, name="Not Mine")
    before_count = Note.objects.filter(owner=owner).count()

    with pytest.raises(services.TrashedItemMutationError):
        services.create_note(owner=owner, folder=other_folder)

    assert Note.objects.filter(owner=owner).count() == before_count


@pytest.mark.django_db
def test_duplicate_note_rejects_when_its_folder_has_become_trashed():
    owner = create_account("duplicate-trashed-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    # Construct the stale-folder scenario legitimately at the service
    # level: the folder is trashed out from under the note's own
    # in-memory reference without going through move_folder_to_trash's
    # own note cascade, so `note.folder` still points at a now-trashed
    # row exactly as a genuine race would leave it.
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now())
    note.refresh_from_db()
    before_count = Note.objects.filter(owner=owner).count()

    with pytest.raises(services.TrashedItemMutationError):
        services.duplicate_note(note=note)

    assert Note.objects.filter(owner=owner).count() == before_count


@pytest.mark.django_db
def test_moving_note_does_not_update_modified_at_or_version():
    owner = create_account("meta-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    original_modified_at = note.modified_at
    original_version = note.version

    services.assign_note_folder(note=note, folder=folder)

    note.refresh_from_db()
    assert note.modified_at == original_modified_at
    assert note.version == original_version


# -- note_move view --------------------------------------------------------


@pytest.mark.django_db
def test_note_move_view_malformed_folder_value_leaves_note_unchanged():
    owner = create_account("malformed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": "not-a-number"}
    )

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_view_cross_owner_folder_value_leaves_note_unchanged_and_shows_message():
    owner = create_account("cross-folder-owner")
    other = create_account("cross-folder-other")
    note = services.create_note(owner=owner)
    other_folder = services.create_folder(owner=other, name="Other's Folder")

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": other_folder.id}, follow=True
    )

    note.refresh_from_db()
    assert note.folder_id is None
    assert b"Could not move the note" in response.content


@pytest.mark.django_db
def test_note_move_view_cross_owner_note_returns_404():
    owner = create_account("move-owner")
    other = create_account("move-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_move", args=[note.id]), {"folder": ""}
    )

    assert response.status_code == 404
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_view_unauthenticated_post_is_blocked():
    owner = create_account("move-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:note_move", args=[note.id]), {"folder": ""})

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_view_moves_note_to_owned_folder_and_back_to_unfiled():
    owner = create_account("view-move-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    client = authenticated_client(owner)

    response = client.post(reverse("notes:note_move", args=[note.id]), {"folder": folder.id})
    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    note.refresh_from_db()
    assert note.folder_id == folder.id

    response = client.post(reverse("notes:note_move", args=[note.id]), {"folder": ""})
    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id is None


# -- folder_create view -----------------------------------------------------


@pytest.mark.django_db
def test_folder_create_view_redirects_to_owned_note():
    owner = create_account("folder-create-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Projects"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    assert Folder.objects.filter(owner=owner, name="Projects").exists()


@pytest.mark.django_db
def test_folder_create_view_duplicate_name_creates_nothing_and_shows_message():
    owner = create_account("dup-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "projects"},
        follow=True,
    )

    assert Folder.objects.filter(owner=owner).count() == 1
    assert b"already exists" in response.content


@pytest.mark.django_db
def test_folder_create_view_blank_name_creates_nothing_and_shows_message():
    owner = create_account("blank-view-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "   "},
        follow=True,
    )

    assert not Folder.objects.filter(owner=owner).exists()
    assert b"Folder name is required" in response.content


@pytest.mark.django_db
def test_folder_create_view_unauthenticated_post_is_blocked():
    owner = create_account("unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:folder_create", args=[note.id]), {"name": "Projects"})

    assert response.status_code == 302
    assert not Folder.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_folder_create_view_cross_owner_note_returns_404():
    owner = create_account("folder-owner-note")
    other = create_account("folder-other-note")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Projects"}
    )

    assert response.status_code == 404
    assert not Folder.objects.filter(name="Projects").exists()


# -- folder_rename views -----------------------------------------------------


@pytest.mark.django_db
def test_folder_rename_view_redirects_to_validated_note():
    owner = create_account("rename-view-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, folder.id]), {"name": "Archive"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    folder.refresh_from_db()
    assert folder.name == "Archive"


@pytest.mark.django_db
def test_folder_rename_home_view_redirects_to_home():
    owner = create_account("rename-home-view-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename_home", args=[folder.id]), {"name": "Archive"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    folder.refresh_from_db()
    assert folder.name == "Archive"


@pytest.mark.django_db
def test_folder_rename_view_duplicate_name_creates_no_change_and_shows_message():
    owner = create_account("rename-view-dup-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")
    other_folder = services.create_folder(owner=owner, name="Personal")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, other_folder.id]),
        {"name": "projects"},
        follow=True,
    )

    other_folder.refresh_from_db()
    assert other_folder.name == "Personal"
    assert b"already exists" in response.content


@pytest.mark.django_db
def test_folder_rename_view_blank_name_creates_no_change_and_shows_message():
    owner = create_account("rename-view-blank-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, folder.id]),
        {"name": "   "},
        follow=True,
    )

    folder.refresh_from_db()
    assert folder.name == "Projects"
    assert b"Folder name is required" in response.content


@pytest.mark.django_db
def test_folder_rename_view_unchanged_name_succeeds_without_message():
    owner = create_account("rename-view-noop-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, folder.id]),
        {"name": "Projects"},
        follow=True,
    )

    folder.refresh_from_db()
    assert folder.name == "Projects"
    flash = _flash_messages_html(response.content.decode())
    assert "already exists" not in flash
    assert "Folder name is required" not in flash


@pytest.mark.django_db
def test_folder_rename_view_cross_owner_folder_returns_404():
    owner = create_account("rename-cross-owner")
    other = create_account("rename-cross-other")
    note = services.create_note(owner=owner)
    other_folder = services.create_folder(owner=other, name="Other's Folder")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, other_folder.id]), {"name": "Mine"}
    )

    assert response.status_code == 404
    other_folder.refresh_from_db()
    assert other_folder.name == "Other's Folder"


@pytest.mark.django_db
def test_folder_rename_view_cross_owner_note_returns_404():
    owner = create_account("rename-cross-note-owner")
    other = create_account("rename-cross-note-other")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(other).post(
        reverse("notes:folder_rename", args=[note.id, folder.id]), {"name": "Archive"}
    )

    assert response.status_code == 404
    folder.refresh_from_db()
    assert folder.name == "Projects"


@pytest.mark.django_db
def test_folder_rename_home_view_cross_owner_folder_returns_404():
    owner = create_account("rename-home-cross-owner")
    other = create_account("rename-home-cross-other")
    other_folder = services.create_folder(owner=other, name="Other's Folder")

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename_home", args=[other_folder.id]), {"name": "Mine"}
    )

    assert response.status_code == 404
    other_folder.refresh_from_db()
    assert other_folder.name == "Other's Folder"


@pytest.mark.django_db
def test_folder_rename_view_unauthenticated_post_is_blocked():
    owner = create_account("rename-unauth-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = Client().post(
        reverse("notes:folder_rename", args=[note.id, folder.id]), {"name": "Archive"}
    )

    assert response.status_code == 302
    folder.refresh_from_db()
    assert folder.name == "Projects"


@pytest.mark.django_db
def test_folder_rename_home_view_unauthenticated_post_is_blocked():
    owner = create_account("rename-home-unauth-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = Client().post(
        reverse("notes:folder_rename_home", args=[folder.id]), {"name": "Archive"}
    )

    assert response.status_code == 302
    folder.refresh_from_db()
    assert folder.name == "Projects"


# -- Tree/drawer rendering ---------------------------------------------------


@pytest.mark.django_db
def test_note_detail_renders_tree_and_drawer_with_distinct_ids():
    owner = create_account("render-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'data-tree-scope="tree"' in content
    assert 'data-tree-scope="drawer"' in content
    assert 'id="tree-new-folder-name"' in content
    assert 'id="drawer-new-folder-name"' in content
    assert 'for="tree-new-folder-name"' in content
    assert 'for="drawer-new-folder-name"' in content
    assert 'id="tree-move-folder"' in content
    assert 'id="drawer-move-folder"' in content
    assert 'for="tree-move-folder"' in content
    assert 'for="drawer-move-folder"' in content


@pytest.mark.django_db
def test_note_detail_marks_current_note_with_aria_current_in_each_copy():
    owner = create_account("aria-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('aria-current="page"') == 2


@pytest.mark.django_db
def test_note_detail_opens_current_folder():
    owner = create_account("open-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Work")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert _details_open_at_marker(content, ">Work</span>", occurrence=0)
    assert _details_open_at_marker(content, ">Work</span>", occurrence=1)


@pytest.mark.django_db
def test_note_detail_no_folders_and_no_unfiled_notes_render_placeholders():
    owner = create_account("empty-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    assert "No folders yet." in content

    folder = services.create_folder(owner=owner, name="Empty Folder")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    assert "No unfiled notes." in content
    assert "No folders yet." not in content


@pytest.mark.django_db
def test_note_detail_empty_folder_renders_placeholder():
    owner = create_account("empty-folder-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Untouched Folder")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert "No notes in this folder." in content


# -- Query efficiency ---------------------------------------------------


@pytest.mark.django_db
def test_note_detail_tree_query_count_does_not_grow_with_folder_count():
    owner = create_account("query-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Solo")
    client = authenticated_client(owner)

    with CaptureQueriesContext(connection) as baseline_ctx:
        client.get(reverse("notes:detail", args=[note.id]))

    for i in range(5):
        folder = services.create_folder(owner=owner, name=f"Folder {i}")
        for _ in range(3):
            extra_note = services.create_note(owner=owner)
            services.assign_note_folder(note=extra_note, folder=folder)

    with CaptureQueriesContext(connection) as scaled_ctx:
        client.get(reverse("notes:detail", args=[note.id]))

    assert len(scaled_ctx.captured_queries) == len(baseline_ctx.captured_queries)


# -- Messages, disclosures, viewport -------------------------------------


@pytest.mark.django_db
def test_folder_create_duplicate_name_shows_popover_local_error_not_page_message():
    owner = create_account("dup-severity-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "projects"},
    )
    content = response.content.decode()

    # No redirect -- the current page is re-rendered in place with the
    # bound error, not a Django message plus a redirect. 3, not 2: the
    # wide tree copy, the narrow drawer copy, and
    # the active-note overflow's own New folder disclosure.
    assert response.status_code == 409
    assert content.count('class="tree-nav__popover-field-error"') == 3
    assert content.count("A folder with that name already exists.") == 3
    assert 'aria-invalid="true"' in content
    # The error must not also appear in the page-level message region.
    assert '<ul class="messages"' not in content


@pytest.mark.django_db
def test_folder_create_blank_name_shows_popover_local_error_not_page_message():
    owner = create_account("blank-severity-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "   "},
    )
    content = response.content.decode()

    # 3, not 2: wide tree, narrow drawer, and the active-note overflow's
    # own New folder disclosure.
    assert response.status_code == 400
    assert content.count('class="tree-nav__popover-field-error"') == 3
    assert content.count("Folder name is required.") == 3
    assert '<ul class="messages"' not in content


@pytest.mark.django_db
def test_folder_create_validation_failure_preserves_attempted_name_and_focus_state():
    owner = create_account("preserve-name-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "Projects"},
    )
    content = response.content.decode()

    new_folder_input_value = re.compile(
        r'id="(?:tree|drawer)-new-folder-name"[^>]*value="Projects"'
    )
    assert len(new_folder_input_value.findall(content)) == 2
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=1)


@pytest.mark.django_db
def test_folder_create_repeated_invalid_submissions_remain_stable():
    owner = create_account("repeated-invalid-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    client = authenticated_client(owner)
    for _ in range(3):
        response = client.post(
            reverse("notes:folder_create", args=[note.id]),
            {"name": "projects"},
        )
        content = response.content.decode()
        assert response.status_code == 409
        # 3, not 2: wide tree, narrow drawer, and the active-note
        # overflow's own New folder disclosure.
        assert content.count("A folder with that name already exists.") == 3
        assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_folder_create_async_duplicate_name_returns_structured_field_error():
    owner = create_account("async-dup-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "projects"},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 409
    assert response["Content-Type"].startswith("application/json")
    payload = response.json()
    assert payload == {
        "ok": False,
        "error": "A folder with that name already exists.",
        "name": "projects",
    }
    assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_folder_create_async_blank_name_returns_structured_field_error():
    owner = create_account("async-blank-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "   "},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload == {"ok": False, "error": "Folder name is required.", "name": "   "}
    assert not Folder.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_folder_create_async_invalid_submission_has_no_redirect_or_html():
    owner = create_account("async-no-redirect-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "projects"},
        HTTP_ACCEPT="application/json",
    )

    # No page render at all -- the response is pure JSON, so there is no
    # server-rendered `<details open>` for either tree copy, and no
    # possibility of the hidden alternate copy being opened or positioned.
    assert response.status_code == 409
    assert "Location" not in response
    assert b"<details" not in response.content
    assert b"messages" not in response.content


@pytest.mark.django_db
def test_folder_create_async_repeated_invalid_submissions_remain_stable():
    owner = create_account("async-repeated-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    client = authenticated_client(owner)
    for _ in range(3):
        response = client.post(
            reverse("notes:folder_create", args=[note.id]),
            {"name": "projects"},
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 409
        payload = response.json()
        assert payload["ok"] is False
        assert payload["error"] == "A folder with that name already exists."
        assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_folder_create_async_success_returns_existing_redirect_target():
    owner = create_account("async-success-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "Projects"},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"ok": True, "redirect_url": reverse("notes:detail", args=[note.id])}
    # The established service call/semantics are unchanged -- same folder
    # actually gets created, same as the non-JS path.
    assert Folder.objects.filter(owner=owner, name="Projects").count() == 1


@pytest.mark.django_db
def test_home_folder_create_async_success_returns_existing_redirect_target():
    owner = create_account("async-home-success-owner")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"),
        {"name": "Projects"},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"ok": True, "redirect_url": reverse("home")}
    assert Folder.objects.filter(owner=owner, name="Projects").count() == 1


@pytest.mark.django_db
def test_home_folder_create_async_duplicate_name_returns_structured_field_error():
    owner = create_account("async-home-dup-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"),
        {"name": "projects"},
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload == {
        "ok": False,
        "error": "A folder with that name already exists.",
        "name": "projects",
    }
    assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_folder_create_non_javascript_fallback_still_renders_in_place_safely():
    """A plain (non-JS) POST -- no Accept: application/json -- keeps using
    the existing safe render-in-place/redirect behavior, unaffected by the
    new async branch."""
    owner = create_account("noscript-fallback-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]),
        {"name": "projects"},
    )
    content = response.content.decode()

    assert response.status_code == 409
    assert not response["Content-Type"].startswith("application/json")
    assert "Location" not in response
    # 3, not 2: wide tree, narrow drawer, and the active-note overflow's
    # own New folder disclosure.
    assert content.count('class="tree-nav__popover-field-error"') == 3
    assert content.count("A folder with that name already exists.") == 3


@pytest.mark.django_db
def test_note_detail_renders_new_folder_and_move_disclosures_in_both_tree_copies():
    owner = create_account("disclosure-owner")
    note = services.create_note(owner=owner)
    note.title = "An Extremely Long Note Title That Must Not Appear In The Trigger"
    note.save(update_fields=["title"])

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    new_folder_class = (
        'class="tree-nav__action tree-nav__action--new-folder row-action-menu" '
        'data-menu-align="left"'
    )
    move_class = (
        'class="tree-nav__action tree-nav__action--move row-action-menu" data-menu-align="left"'
    )
    assert content.count(new_folder_class) == 2
    assert content.count(move_class) == 2
    assert content.count('aria-label="New folder"') == 2
    assert content.count('aria-label="Move note"') == 2
    assert "Move this note to" in content


@pytest.mark.django_db
def test_note_detail_disclosures_are_collapsed_by_default():
    owner = create_account("collapsed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)
    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=1)
    assert not _details_open_at_marker(content, 'aria-label="Move note"', occurrence=0)
    assert not _details_open_at_marker(content, 'aria-label="Move note"', occurrence=1)


@pytest.mark.django_db
def test_note_detail_new_folder_and_rename_inputs_have_no_native_required_attribute():
    owner = create_account("no-required-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('id="tree-new-folder-name"') == 1
    assert content.count('id="drawer-new-folder-name"') == 1
    assert content.count("-rename-name") >= 2
    assert " required" not in content
    assert content.count('maxlength="255"') >= 4


@pytest.mark.django_db
def test_home_new_folder_input_has_no_native_required_attribute():
    owner = create_account("home-no-required-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert content.count('id="drawer-new-folder-name"') == 1
    assert content.count("-rename-name") >= 1
    assert " required" not in content
    assert content.count('maxlength="255"') >= 2


@pytest.mark.django_db
def test_note_detail_renders_exactly_one_viewport_per_tree_copy():
    owner = create_account("viewport-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('class="tree-nav__viewport"') == 2


@pytest.mark.django_db
def test_note_detail_drawer_tree_precedes_drawer_account_section():
    owner = create_account("order-owner-drawer")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    tree_index = content.index('class="workspace-drawer__tree"')
    account_index = content.index('class="workspace-drawer__account"')

    assert tree_index < account_index


@pytest.mark.django_db
def test_note_detail_folder_and_note_rows_expose_stable_identifiers():
    owner = create_account("identifier-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count(f'data-folder-id="{folder.id}"') == 2
    assert content.count(f'data-note-id="{note.id}"') == 2
    assert content.count('data-folder-target="unfiled"') == 2


@pytest.mark.django_db
def test_note_detail_renders_no_duplicate_element_ids():
    owner = create_account("no-dup-id-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
    # The header's brand mark carries the page's one semantic
    # ridgenoteMarkTitle id; the drawer's own copy of the mark is
    # decorative and must not repeat it.
    assert content.count('id="ridgenoteMarkTitle"') == 1


# -- Shared narrow navigation on Home -------------------


@pytest.mark.django_db
def test_home_renders_shared_drawer_with_owner_scoped_folders_and_notes():
    owner = create_account("home-drawer-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    filed_note = services.create_note(owner=owner)
    services.assign_note_folder(note=filed_note, folder=folder)
    unfiled_note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'id="workspace-drawer"' in content
    assert 'data-tree-scope="drawer"' in content
    assert "Projects" in content
    assert f'data-note-id="{filed_note.id}"' in content
    assert f'data-note-id="{unfiled_note.id}"' in content
    assert 'data-folder-target="unfiled"' in content


@pytest.mark.django_db
def test_home_renders_no_move_note_disclosure():
    owner = create_account("home-no-move-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'aria-label="Move note"' not in content
    assert 'aria-label="New folder"' in content


@pytest.mark.django_db
def test_home_renders_no_aria_current_or_selected_highlight():
    owner = create_account("home-no-current-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'aria-current="page"' not in content
    assert "tree-nav__row--current" not in content


@pytest.mark.django_db
def test_home_folder_create_creates_folder_and_redirects_to_home():
    owner = create_account("home-create-owner")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"), {"name": "Projects"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert Folder.objects.filter(owner=owner, name="Projects").exists()


@pytest.mark.django_db
def test_home_folder_create_duplicate_name_shows_popover_local_error_not_page_message():
    # Home renders `_tree_nav.html` twice (the
    # wide tree rail plus the narrow drawer), so a shared
    # error marker legitimately appears twice, not once.
    owner = create_account("home-dup-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"), {"name": "projects"}
    )
    content = response.content.decode()

    assert response.status_code == 409
    assert Folder.objects.filter(owner=owner).count() == 1
    assert content.count('class="tree-nav__popover-field-error"') == 2
    assert "A folder with that name already exists." in content
    assert '<ul class="messages"' not in content


@pytest.mark.django_db
def test_home_folder_create_blank_name_shows_popover_local_error_not_page_message():
    # See the duplicate-name variant above re: the count of 2.
    owner = create_account("home-blank-owner")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"), {"name": "   "}
    )
    content = response.content.decode()

    assert response.status_code == 400
    assert not Folder.objects.filter(owner=owner).exists()
    assert content.count('class="tree-nav__popover-field-error"') == 2
    assert "Folder name is required." in content
    assert '<ul class="messages"' not in content


@pytest.mark.django_db
def test_home_folder_create_validation_failure_preserves_attempted_name_and_reopens():
    owner = create_account("home-preserve-name-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create_home"), {"name": "Projects"}
    )
    content = response.content.decode()

    new_folder_input_value = re.compile(r'id="drawer-new-folder-name"[^>]*value="Projects"')
    assert len(new_folder_input_value.findall(content)) == 1
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)


@pytest.mark.django_db
def test_home_folder_create_unauthenticated_post_is_blocked():
    response = Client().post(reverse("notes:folder_create_home"), {"name": "Projects"})

    assert response.status_code == 302
    assert not Folder.objects.filter(name="Projects").exists()


@pytest.mark.django_db
def test_home_renders_exactly_one_drawer_and_backdrop():
    owner = create_account("home-single-drawer-owner")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert content.count('id="workspace-drawer"') == 1
    assert content.count('class="workspace-drawer-backdrop"') == 1
    assert content.count("workspace-drawer-trigger") == 1


@pytest.mark.django_db
def test_home_renders_no_duplicate_element_ids():
    owner = create_account("home-no-dup-id-owner")
    services.create_folder(owner=owner, name="Projects")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
    # The header's brand mark carries the page's one semantic
    # ridgenoteMarkTitle id; the drawer's own copy of the mark is
    # decorative and must not repeat it.
    assert content.count('id="ridgenoteMarkTitle"') == 1


@pytest.mark.django_db
def test_home_does_not_render_other_owners_folders_or_notes():
    owner = create_account("home-owner-scope-owner")
    other = create_account("home-owner-scope-other")
    services.create_folder(owner=other, name="Other Folder")
    other_note = services.create_note(owner=other)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Other Folder" not in content
    assert f'data-note-id="{other_note.id}"' not in content


@pytest.mark.django_db
def test_note_detail_still_renders_move_note_and_aria_current_after_shared_drawer_extraction():
    owner = create_account("detail-unchanged-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('aria-label="Move note"') == 2
    assert content.count('aria-current="page"') == 2
    assert content.count('id="workspace-drawer"') == 1
    assert content.count('class="workspace-drawer-backdrop"') == 1


# -- Folder rename in shared tree -----------------------


@pytest.mark.django_db
def test_note_detail_renders_rename_disclosure_in_both_tree_copies():
    owner = create_account("rename-tree-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # The note (Unfiled, since it has no folder
    # here) also renders its own Rename disclosure with this exact
    # class/summary -- the empty "Projects" folder's own Rename disclosure
    # (2) plus the Unfiled note's own Rename disclosure (2), four total.
    assert content.count('class="tree-nav__action tree-nav__action--rename-folder"') == 4
    assert content.count('<summary class="tree-nav__row-menu-item">Rename</summary>') == 4


@pytest.mark.django_db
def test_note_detail_rename_disclosure_is_collapsed_by_default():
    owner = create_account("rename-collapsed-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert not _details_open_at_marker(content, ">Rename</summary>", occurrence=0)
    assert not _details_open_at_marker(content, ">Rename</summary>", occurrence=1)


@pytest.mark.django_db
def test_note_detail_rename_form_pre_filled_and_uses_note_context_route():
    owner = create_account("rename-prefill-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    expected_action = reverse("notes:folder_rename", args=[note.id, folder.id])
    home_action = reverse("notes:folder_rename_home", args=[folder.id])
    assert content.count(f'action="{expected_action}"') == 2
    assert content.count('value="Projects"') == 2
    assert f'action="{home_action}"' not in content


@pytest.mark.django_db
def test_home_renders_rename_disclosure_with_home_context_route():
    # Home renders `_tree_nav.html` twice (wide
    # rail + narrow drawer), so these markers legitimately appear twice.
    owner = create_account("rename-home-tree-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    expected_action = reverse("notes:folder_rename_home", args=[folder.id])
    assert content.count('class="tree-nav__action tree-nav__action--rename-folder"') == 2
    assert content.count('<summary class="tree-nav__row-menu-item">Rename</summary>') == 2
    assert f'action="{expected_action}"' in content
    assert 'value="Projects"' in content


@pytest.mark.django_db
def test_note_detail_rename_disclosure_ids_remain_unique_across_tree_and_drawer():
    owner = create_account("rename-unique-id-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))
    assert any(_id.endswith("-rename-name") for _id in ids)


@pytest.mark.django_db
def test_home_rename_disclosure_introduces_no_duplicate_ids():
    owner = create_account("rename-home-unique-id-owner")
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


# -- Tree controls and folder actions -------------------


@pytest.mark.django_db
def test_folder_menu_trigger_renders_once_per_folder_expanded_and_collapsed():
    owner = create_account("folder-menu-trigger-owner")
    open_folder_note = services.create_note(owner=owner)
    open_folder = services.create_folder(owner=owner, name="Open Folder")
    services.assign_note_folder(note=open_folder_note, folder=open_folder)
    services.create_folder(owner=owner, name="Collapsed Folder")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[open_folder_note.id]))
    content = response.content.decode()

    assert content.count('aria-label="Actions for Open Folder"') == 2
    assert content.count('aria-label="Actions for Collapsed Folder"') == 2
    assert not _details_open_at_marker(
        content, 'aria-label="Actions for Open Folder"', occurrence=0
    )
    assert not _details_open_at_marker(
        content, 'aria-label="Actions for Collapsed Folder"', occurrence=0
    )


@pytest.mark.django_db
def test_folder_menu_contains_exactly_three_actions_and_no_persistent_buttons_remain():
    owner = create_account("folder-menu-contents-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("New note in this folder") == 2
    # The folder's own note, nested inside it,
    # also renders its own compact Rename disclosure with this exact
    # summary -- the folder's own Rename (2) plus the note's own Rename
    # (2), four total.
    assert content.count('<summary class="tree-nav__row-menu-item">Rename</summary>') == 4
    # The shared `#delete-confirm-dialog`'s own
    # submit button is also labeled "Move to Trash" (1, once per page).
    # The note row-menu's own trigger is labeled
    # "Move to Trash" too, so this note (filed in this
    # folder, so its own row menu renders alongside the folder's)
    # contributes 2 more -- 2 folder-row triggers + 2 note-row triggers
    # (wide + narrow drawer each) + 1 dialog button.
    assert content.count("Move to Trash") == 5
    assert "tree-nav__action--delete-folder" not in content


@pytest.mark.django_db
def test_folder_disclosure_and_menu_trigger_are_separate_elements():
    owner = create_account("folder-disclosure-separate-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('class="tree-nav__folder-disclosure"') == 2
    assert content.count('class="tree-nav__row-menu tree-nav__folder-menu row-action-menu"') == 2


@pytest.mark.django_db
def test_folder_note_create_view_creates_note_in_folder_and_redirects_like_new_note():
    owner = create_account("folder-note-create-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    starting_total = Note.objects.filter(owner=owner).count()

    response = authenticated_client(owner).post(
        reverse("notes:folder_note_create", args=[note.id, folder.id])
    )

    assert response.status_code == 302
    assert Note.objects.filter(owner=owner).count() == starting_total + 1
    created_note = Note.objects.filter(owner=owner).exclude(pk=note.id).get()
    assert created_note.folder_id == folder.id
    assert response.url == f"{reverse('notes:detail', args=[created_note.id])}?new=1"


@pytest.mark.django_db
def test_folder_note_create_home_view_creates_note_in_folder_and_redirects():
    owner = create_account("folder-note-create-home-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_note_create_home", args=[folder.id])
    )

    assert response.status_code == 302
    created_note = Note.objects.get(owner=owner)
    assert created_note.folder_id == folder.id
    assert response.url == f"{reverse('notes:detail', args=[created_note.id])}?new=1"


@pytest.mark.django_db
def test_folder_note_create_rejects_another_owners_folder():
    owner = create_account("folder-note-create-cross-owner")
    other_owner = create_account("folder-note-create-other-owner")
    note = services.create_note(owner=owner)
    other_folder = services.create_folder(owner=other_owner, name="Not Yours")
    starting_total = Note.objects.count()

    response = authenticated_client(owner).post(
        reverse("notes:folder_note_create", args=[note.id, other_folder.id])
    )

    assert response.status_code == 404
    assert Note.objects.count() == starting_total


@pytest.mark.django_db
def test_folder_note_create_rejects_trashed_folder_safely():
    owner = create_account("folder-note-create-trashed-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    starting_total = Note.objects.filter(owner=owner).count()

    response = authenticated_client(owner).post(
        reverse("notes:folder_note_create", args=[note.id, folder.id])
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    assert Note.objects.filter(owner=owner).count() == starting_total


@pytest.mark.django_db
def test_folder_note_create_home_rejects_trashed_folder_safely():
    owner = create_account("folder-note-create-home-trashed-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    starting_total = Note.objects.filter(owner=owner).count()

    response = authenticated_client(owner).post(
        reverse("notes:folder_note_create_home", args=[folder.id])
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert Note.objects.filter(owner=owner).count() == starting_total


@pytest.mark.django_db
def test_folder_note_create_unauthenticated_post_is_blocked():
    owner = create_account("folder-note-create-unauth-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    starting_total = Note.objects.filter(owner=owner).count()

    response = Client().post(reverse("notes:folder_note_create", args=[note.id, folder.id]))

    assert response.status_code in (302, 401, 403)
    assert Note.objects.filter(owner=owner).count() == starting_total


@pytest.mark.django_db
def test_folder_note_create_rejects_get_request():
    owner = create_account("folder-note-create-get-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(
        reverse("notes:folder_note_create", args=[note.id, folder.id])
    )

    assert response.status_code == 405


@pytest.mark.django_db
def test_folder_menu_rename_still_uses_existing_route_and_prefill():
    owner = create_account("folder-menu-rename-route-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    expected_action = reverse("notes:folder_rename", args=[note.id, folder.id])
    assert content.count(f'action="{expected_action}"') == 2
    assert content.count('value="Projects"') == 2


@pytest.mark.django_db
def test_folder_menu_move_to_trash_link_targets_existing_confirmation_route():
    owner = create_account("folder-menu-trash-route-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # The Move to Trash link is a
    # dialog-opening button (`data-delete-trigger`) carrying its target
    # route as a `data-delete-action` attribute instead of an `href`.
    expected_action = reverse("notes:folder_delete", args=[note.id, folder.id])
    assert content.count(f'data-delete-action="{expected_action}"') == 2
    # The folder row's own danger-styled Move to
    # Trash item renders twice (wide tree + narrow drawer). The
    # note nested inside this same folder also renders its own
    # Move to Trash with the same danger class, also twice --
    # four total, not the folder's two alone.
    assert content.count('class="tree-nav__row-menu-item tree-nav__row-menu-item--danger"') == 4

    # The route itself is POST-only now -- GET is rejected outright, and
    # the folder is not trashed until the dialog's own POST is submitted.
    confirm_response = authenticated_client(owner).get(expected_action)
    assert confirm_response.status_code == 405
    folder.refresh_from_db()
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_menu_move_to_trash_still_moves_folder_to_trash_on_post():
    owner = create_account("folder-menu-trash-post-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[note.id, folder.id])
    )

    assert response.status_code == 302
    folder.refresh_from_db()
    assert folder.trashed_at is not None


# -- Toolbar, action rail, note-menu ----
# -- containment ---------------------------------------------------------


@pytest.mark.django_db
def test_creation_toolbar_groups_three_controls_with_accessible_names():
    owner = create_account("creation-toolbar-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Two tree copies (wide + drawer) each render one toolbar.
    assert content.count('class="tree-nav__creation-toolbar"') == 2
    assert content.count('role="group"') == 2
    assert content.count('aria-label="Create"') == 2
    assert content.count('aria-label="New note"') == 2
    assert content.count('aria-label="New folder"') == 2
    assert content.count('aria-label="Move note"') == 2

    # All three triggers, across both tree copies, share the same sizing
    # primitive (equal square dimensions, per the corrective requirement).
    assert content.count('class="icon-button tree-nav__action-trigger"') == 6


@pytest.mark.django_db
def test_creation_toolbar_uses_approved_lucide_icons():
    owner = create_account("creation-toolbar-icons-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # New note: upstream Lucide "file-plus" -- a signature fragment of its
    # unmodified path data (the folded-corner tab plus the "+").
    assert content.count('d="M14 2v5a1 1 0 0 0 1 1h5"') == 2
    assert content.count('d="M9 15h6"') == 2
    assert content.count('d="M12 18v-6"') == 2
    # New folder: upstream Lucide "folder-plus".
    assert content.count('d="M12 10v6"') == 2
    assert content.count('d="M9 13h6"') == 2
    # Move note: upstream Lucide "folder-input".
    assert content.count('d="M2 13h10"') == 2
    assert content.count('d="m9 16 3-3-3-3"') == 2
    # All three creation icons use the Lucide stroke-based rendering
    # convention (fill="none" on the svg root, stroke="currentColor"), not
    # the earlier hand-drawn icons' mixed fill/stroke conventions.
    assert content.count('viewBox="0 0 24 24" fill="none" stroke="currentColor"') >= 6


@pytest.mark.django_db
def test_folder_and_note_rows_use_fixed_action_rail_hooks():
    owner = create_account("action-rail-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Both folder and note action triggers share the same .tree-nav__row-menu
    # hook that CSS pins to the visible right edge (position: sticky) with an
    # opaque background, and both title areas share .tree-nav__label /
    # .tree-nav__note-link, which CSS truncates with an ellipsis instead of
    # growing the row -- the fixed-width action / flexible-title split
    # this depends on.
    assert content.count('class="tree-nav__row-menu tree-nav__folder-menu row-action-menu"') == 2
    assert content.count('class="tree-nav__row-menu row-action-menu"') == 2
    assert content.count('class="tree-nav__folder-disclosure"') == 2
    assert content.count('class="tree-nav__note-link"') == 2


@pytest.mark.django_db
def test_folder_menu_trigger_present_regardless_of_folder_name_length():
    owner = create_account("long-folder-name-owner")
    long_name = "An Extremely Long Folder Name That Must Not Hide The Menu Trigger"
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name=long_name)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count(f'aria-label="Actions for {long_name}"') == 2
    assert content.count('class="tree-nav__folder-disclosure"') == 2


@pytest.mark.django_db
def test_note_row_menu_present_regardless_of_note_title_length():
    owner = create_account("long-note-title-owner")
    note = services.create_note(owner=owner)
    long_title = "An Extremely Long Note Title That Must Not Hide The Menu Trigger Either"
    note.title = long_title
    note.save(update_fields=["title"])

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count(f'aria-label="Actions for {long_title}"') == 2
    assert content.count('class="tree-nav__note-link"') == 2


@pytest.mark.django_db
def test_new_folder_autofocus_hook_is_preserved():
    owner = create_account("corrective-preserve-autofocus-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    new_folder_class = (
        'class="tree-nav__action tree-nav__action--new-folder row-action-menu" '
        'data-menu-align="left"'
    )
    assert content.count(new_folder_class) == 2
    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)


@pytest.mark.django_db
def test_folder_menu_contents_and_disclosure_separation_are_preserved():
    owner = create_account("corrective-preserve-menu-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count("New note in this folder") == 2
    # See the identical note above at
    # `test_folder_menu_contains_exactly_three_actions_and_no_persistent_buttons_remain`
    # -- folder Rename (2) plus the nested note's own Rename (2).
    assert content.count('<summary class="tree-nav__row-menu-item">Rename</summary>') == 4
    # See the identical note above at
    # `test_folder_menu_contains_exactly_three_actions_and_no_persistent_buttons_remain`
    # -- 2 folder-row triggers + 2 note-row triggers + 1 dialog button.
    assert content.count("Move to Trash") == 5
    assert content.count('class="tree-nav__folder-disclosure"') == 2
    assert content.count('class="tree-nav__row-menu tree-nav__folder-menu row-action-menu"') == 2


def _wide_tree_section(content: str) -> str:
    return content[: content.index('id="workspace-drawer"')]


def _drawer_section(content: str) -> str:
    return content[content.index('id="workspace-drawer"') :]


@pytest.mark.django_db
def test_creation_toolbar_present_in_narrow_drawer_and_not_duplicated():
    owner = create_account("drawer-toolbar-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)

    # Exactly one toolbar in each tree copy -- restored to the drawer, and
    # never duplicated within either copy.
    assert wide.count('class="tree-nav__creation-toolbar"') == 1
    assert drawer.count('class="tree-nav__creation-toolbar"') == 1
    assert drawer.count('aria-label="New note"') == 1
    assert drawer.count('aria-label="New folder"') == 1
    assert drawer.count('aria-label="Move note"') == 1


@pytest.mark.django_db
def test_home_creation_toolbar_present_in_drawer_without_move_note():
    owner = create_account("home-drawer-toolbar-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    drawer = _drawer_section(content)

    assert drawer.count('class="tree-nav__creation-toolbar"') == 1
    assert drawer.count('aria-label="New note"') == 1
    assert drawer.count('aria-label="New folder"') == 1
    # Home's drawer tree copy has no current note context, so Move note is
    # contextually absent there, exactly as on the wide tree.
    assert drawer.count('aria-label="Move note"') == 0


@pytest.mark.django_db
def test_creation_toolbar_controls_have_deterministic_tooltips():
    owner = create_account("creation-tooltip-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Two tree copies (wide + drawer), one tooltip attribute per control --
    # exactly one `data-tooltip` per trigger, never more (no duplicates).
    assert content.count('data-tooltip="New note"') == 2
    assert content.count('data-tooltip="New folder"') == 2
    assert content.count('data-tooltip="Move note"') == 2
    # Native `title` is absent from these three controls:
    # the delayed native browser tooltip does not compete with the
    # deterministic custom one.
    assert 'title="New note"' not in content
    assert 'title="New folder"' not in content
    assert 'title="Move note"' not in content
    # The accessible name (aria-label) is present independently of the
    # tooltip attribute on the very same element.
    assert content.count('aria-label="New note"') == 2
    assert content.count('aria-label="New folder"') == 2
    assert content.count('aria-label="Move note"') == 2


@pytest.mark.django_db
def test_home_drawer_creation_toolbar_has_tooltips_for_contextually_present_controls():
    owner = create_account("home-drawer-tooltip-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    drawer = _drawer_section(content)

    assert drawer.count('data-tooltip="New note"') == 1
    assert drawer.count('data-tooltip="New folder"') == 1
    # Move note is contextually absent on Home, so no orphaned tooltip for it.
    assert drawer.count('data-tooltip="Move note"') == 0


@pytest.mark.django_db
def test_move_note_is_a_floating_popover_reusing_the_shared_menu_foundation():
    owner = create_account("move-popover-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    # The in-app Help panel (rendered on every page) documents Move as one
    # of its own topics, so it is excluded before counting actual Move
    # controls below -- none of the other counts in this test appear in
    # Help's prose, so excluding it is safe for the whole test.
    content = _content_excluding_help_panel(response.content.decode())

    # Move note's <details> now also carries .row-action-menu, the same
    # class tree-context-menu.ts wires for floating/portal/Escape/outside
    # click/cross-menu-close behavior as the folder and note row menus, plus
    # data-menu-align="left" so it anchors to the trigger's left edge rather
    # than right-aligning like a row menu (which would shift it far left of
    # this small toolbar trigger).
    assert (
        content.count(
            'class="tree-nav__action tree-nav__action--move row-action-menu" data-menu-align="left"'
        )
        == 2
    )
    # Its content lives in a .tree-nav__row-menu-panel, the same bounded,
    # anchored panel styling shared with folder/note menus (plus the
    # left-align modifier) -- not the expanding-inline-block
    # .tree-nav__action-form.
    move_panel_class = (
        'class="tree-nav__row-menu-panel tree-nav__row-menu-panel--align-left tree-nav__move-panel"'  # noqa: E501
    )
    assert content.count(move_panel_class) == 2
    # Shared by both Move note and New Folder's popovers -- two of each,
    # across the wide tree and narrow drawer copies, four total.
    assert content.count('class="tree-nav__popover-form"') == 4
    # Explicit Cancel and Move controls are both present. New Folder also
    # has its own Cancel button, so both counts double to four.
    # +1 more for the active-note overflow's own
    # nested New Folder disclosure, which also has a Cancel button
    # (present once per page, not doubled by tree copy, since the
    # overflow itself isn't duplicated) -- five total.
    # +2 more for the active-note overflow's own New note and
    # Move disclosures, which also have Cancel buttons, matching the
    # New Folder pattern exactly -- seven total.
    assert content.count("data-row-menu-cancel") == 7
    # +1 for the shared `#delete-confirm-dialog`'s
    # own Cancel button, present once per page regardless of tree copy
    # count. +1 more for the shared
    # `#restore-confirm-dialog`'s own Cancel button, same reasoning.
    # +1 more for the active-note overflow's New
    # Folder Cancel button -- seven total. +2 more
    # for the active-note overflow's New note and Move Cancel buttons --
    # nine total.
    assert content.count(">Cancel<") == 9
    # ">Move<" appears three times per tree copy here: the popover's own
    # submit button, plus the single existing note's own row-menu Move
    # disclosure -- its summary and its own submit
    # button (an unrelated, pre-existing control) -- six total, plus
    # the active-note overflow Move disclosure
    # (its summary and its own submit button, two more) -- eight total.
    assert content.count(">Move<") == 8
    # Existing backend route/behavior is completely unchanged. The single
    # existing note's own row-menu "Move to" form happens to target this
    # same note (and thus the same URL), so it appears four times here too
    # (twice from the popover, twice from that unrelated row-menu form),
    # plus one more from the overflow Move disclosure -- five total.
    expected_action = reverse("notes:note_move", args=[note.id])
    assert content.count(f'action="{expected_action}"') == 5
    assert content.count('id="tree-move-folder"') == 1
    assert content.count('id="drawer-move-folder"') == 1


@pytest.mark.django_db
def test_move_note_popover_still_moves_note_on_post():
    owner = create_account("move-popover-post-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": folder.id}
    )

    assert response.status_code == 302
    note.refresh_from_db()
    assert note.folder_id == folder.id


# -- New Folder popover ----------
# -- conversion, Move popover left-align, compact controls ------------------


@pytest.mark.django_db
def test_new_folder_is_a_floating_popover_reusing_the_shared_menu_foundation():
    owner = create_account("new-folder-popover-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Same shared foundation as Move note: .row-action-menu (float/portal/
    # Escape/outside-click/Cancel wiring) and data-menu-align="left".
    new_folder_class = (
        'class="tree-nav__action tree-nav__action--new-folder row-action-menu" '
        'data-menu-align="left"'
    )
    new_folder_panel_class = (
        'class="tree-nav__row-menu-panel tree-nav__row-menu-panel--align-left '
        'tree-nav__new-folder-panel"'
    )
    assert content.count(new_folder_class) == 2
    assert content.count(new_folder_panel_class) == 2
    assert content.count('class="tree-nav__popover-form"') == 4
    # +1 more for the active-note overflow's own
    # nested New Folder disclosure's Cancel button (present once per
    # page, not doubled by tree copy) -- five total.
    # +2 more for the active-note overflow's New note and Move
    # Cancel buttons -- seven total.
    assert content.count("data-row-menu-cancel") == 7
    # +1 for the shared `#delete-confirm-dialog`'s
    # own Cancel button, present once per page regardless of tree copy
    # count. +1 more for the shared
    # `#restore-confirm-dialog`'s own Cancel button, same reasoning.
    # +1 more for the active-note overflow's New
    # Folder Cancel button -- seven total. +2 more
    # for the active-note overflow's New note and Move Cancel buttons --
    # nine total.
    assert content.count(">Cancel<") == 9
    # 3, not 2: wide tree, narrow drawer, and the
    # active-note overflow's own New folder disclosure, which reuses this
    # exact "Create folder" label and the same underlying route.
    assert content.count(">Create folder<") == 3
    # Existing backend route/behavior is completely unchanged.
    expected_action = reverse("notes:folder_create", args=[note.id])
    assert content.count(f'action="{expected_action}"') == 3
    assert content.count('id="tree-new-folder-name"') == 1
    assert content.count('id="drawer-new-folder-name"') == 1


@pytest.mark.django_db
def test_new_folder_form_is_marked_for_async_enhancement_with_hidden_error_by_default():
    owner = create_account("async-marker-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # 3, not 2: wide tree, narrow drawer, and the
    # active-note overflow's own New folder disclosure, which reuses the
    # exact same async-enhancement marker and field-error element.
    assert content.count("data-new-folder-form") == 3
    # No error on an ordinary page load: the field-error element is present
    # (so the async script can find and toggle it without a reload) but
    # inert/hidden.
    assert content.count('class="tree-nav__popover-field-error"') == 3
    assert content.count('id="tree-new-folder-error" role="alert" hidden') == 1
    assert content.count('id="drawer-new-folder-error" role="alert" hidden') == 1
    assert 'aria-invalid="true"' not in content


@pytest.mark.django_db
def test_new_folder_popover_closed_by_default_on_ordinary_page_load():
    owner = create_account("new-folder-closed-default-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)
    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=1)


@pytest.mark.django_db
def test_new_folder_popover_reopens_after_duplicate_name_validation_error():
    owner = create_account("new-folder-dup-reopen-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "projects"}, follow=True
    )
    content = response.content.decode()

    assert "already exists" in content
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=1)
    # No folder was actually created by the failed attempt.
    assert Folder.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_new_folder_popover_reopens_after_blank_name_validation_error():
    owner = create_account("new-folder-blank-reopen-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "   "}, follow=True
    )
    content = response.content.decode()

    assert "required" in content
    assert _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)


@pytest.mark.django_db
def test_new_folder_popover_stays_closed_after_successful_creation():
    owner = create_account("new-folder-success-closed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Projects"}, follow=True
    )
    content = response.content.decode()

    assert not _details_open_at_marker(content, 'aria-label="New folder"', occurrence=0)
    assert Folder.objects.filter(owner=owner, name="Projects").count() == 1


@pytest.mark.django_db
def test_new_folder_popover_still_creates_folder_and_redirects_like_before():
    owner = create_account("new-folder-still-creates-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:folder_create", args=[note.id]), {"name": "Projects"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    assert Folder.objects.filter(owner=owner, name="Projects").count() == 1


@pytest.mark.django_db
def test_no_duplicate_popovers_per_tree_copy():
    owner = create_account("no-duplicate-popovers-owner")
    note = services.create_note(owner=owner)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    wide = _wide_tree_section(content)
    drawer = _drawer_section(content)

    assert wide.count('aria-label="New folder"') == 1
    assert wide.count('aria-label="Move note"') == 1
    assert drawer.count('aria-label="New folder"') == 1
    assert drawer.count('aria-label="Move note"') == 1


@pytest.mark.django_db
def test_move_and_new_folder_popovers_use_compact_control_classes():
    owner = create_account("compact-popover-controls-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    # Both popovers' action rows use the same compact-sizing hook.
    # +1 more for the active-note overflow's own
    # New Folder disclosure, which reuses this same actions-row
    # wrapper for its Cancel button -- five total.
    # +2 more for the active-note overflow's New note and Move
    # disclosures, which also reuse this same wrapper for their own
    # Cancel buttons -- seven total.
    assert content.count('class="tree-nav__popover-form-actions"') == 7
