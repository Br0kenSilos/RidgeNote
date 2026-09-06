"""Trash and recovery clarity.

Two narrow, presentation-only changes:

1. `notes_grouped_for_tree()` hides a folder from the tree only when it is
   both the owner's "Recovered Items" marker (`is_recovery_folder=True`)
   and currently holds zero active notes. The database row itself is
   never deleted or recreated -- restore-destination lookup
   (`_get_or_create_recovery_folder()`) still finds it by its database
   flag, completely independent of tree enumeration. Ordinary empty
   folders remain visible exactly as before.
2. Owner Trash and Administrator Recovery note rows now show an explicit
   "From folder -- X" / "Original folder: X" prefix instead of a bare
   folder name, using the literal "Unfiled" string (unchanged) when
   there is no folder.
"""

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


# -- empty Recovered Items visibility ------------------------------------------


@pytest.mark.django_db
def test_empty_marked_recovery_folder_is_hidden_from_tree():
    owner = create_account("clarity-empty-recovery-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Needs Recovery")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    admin = create_admin("clarity-empty-recovery-admin")
    services.restore_note_for_administrator(note_id=note.id, actor=admin)
    note.refresh_from_db()
    recovery_folder = note.folder
    assert recovery_folder is not None
    assert recovery_folder.is_recovery_folder is True

    # Empty the marker folder back out again -- it must stay hidden even
    # though it still exists in the database.
    services.move_note_to_trash(note=note)

    folders, _unfiled = services.notes_grouped_for_tree(owner=owner)
    assert recovery_folder.id not in [f.id for f in folders]
    assert Folder.objects.filter(pk=recovery_folder.id).exists()


@pytest.mark.django_db
def test_marked_recovery_folder_with_an_active_note_remains_visible():
    owner = create_account("clarity-populated-recovery-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Recovered Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    admin = create_admin("clarity-populated-recovery-admin")
    services.restore_note_for_administrator(note_id=note.id, actor=admin)
    note.refresh_from_db()
    recovery_folder = note.folder
    assert recovery_folder is not None
    assert recovery_folder.is_recovery_folder is True

    folders, _unfiled = services.notes_grouped_for_tree(owner=owner)
    assert recovery_folder.id in [f.id for f in folders]


@pytest.mark.django_db
def test_ordinary_empty_folder_remains_visible():
    owner = create_account("clarity-ordinary-empty-owner")
    folder = services.create_folder(owner=owner, name="Ordinary Empty Folder")

    folders, _unfiled = services.notes_grouped_for_tree(owner=owner)
    assert folder.id in [f.id for f in folders]


@pytest.mark.django_db
def test_restore_still_finds_a_hidden_recovery_marker():
    owner = create_account("clarity-restore-finds-hidden-owner")
    first_note = services.create_note(owner=owner)
    services.rename_note(note=first_note, title="First Recovery Note")
    services.move_note_to_trash(note=first_note)
    Note.objects.filter(pk=first_note.pk).update(emptied_at=timezone.now())
    admin = create_admin("clarity-restore-finds-hidden-admin")
    services.restore_note_for_administrator(note_id=first_note.id, actor=admin)
    first_note.refresh_from_db()
    recovery_folder = first_note.folder
    assert recovery_folder.is_recovery_folder is True

    # Empty it out (hidden from the tree now) then send a second note
    # through recovery -- destination resolution must still find and
    # reuse the same hidden marker row, not create a second one.
    services.move_note_to_trash(note=first_note)
    folders, _unfiled = services.notes_grouped_for_tree(owner=owner)
    assert recovery_folder.id not in [f.id for f in folders]

    second_note = services.create_note(owner=owner)
    services.rename_note(note=second_note, title="Second Recovery Note")
    services.move_note_to_trash(note=second_note)
    Note.objects.filter(pk=second_note.pk).update(emptied_at=timezone.now())
    services.restore_note_for_administrator(note_id=second_note.id, actor=admin)
    second_note.refresh_from_db()

    assert second_note.folder_id == recovery_folder.id
    assert Folder.objects.filter(owner=owner, is_recovery_folder=True).count() == 1


@pytest.mark.django_db
def test_hidden_recovery_folder_does_not_affect_other_owners_trees():
    owner_a = create_account("clarity-owner-scope-a")
    owner_b = create_account("clarity-owner-scope-b")
    note = services.create_note(owner=owner_a)
    services.rename_note(note=note, title="Owner A Recovery Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    admin = create_admin("clarity-owner-scope-admin")
    services.restore_note_for_administrator(note_id=note.id, actor=admin)
    note.refresh_from_db()
    services.move_note_to_trash(note=note)

    folder_b = services.create_folder(owner=owner_b, name="Owner B Folder")

    folders_b, _unfiled_b = services.notes_grouped_for_tree(owner=owner_b)
    assert [f.id for f in folders_b] == [folder_b.id]


# -- owner Trash origin wording --------------------------------------------------


@pytest.mark.django_db
def test_owner_trash_shows_from_folder_wording_for_a_filed_note():
    owner = create_account("clarity-trash-filed-owner")
    folder = services.create_folder(owner=owner, name="Project Plans")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Filed Trashed Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "From folder — Project Plans" in content


@pytest.mark.django_db
def test_owner_trash_shows_unfiled_wording_for_an_unfiled_note():
    owner = create_account("clarity-trash-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Unfiled Trashed Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Unfiled" in content
    assert "From folder — Unfiled" not in content


# -- administrator Recovery origin wording ---------------------------------------


@pytest.mark.django_db
def test_admin_recovery_shows_original_folder_wording_for_a_filed_note():
    owner = create_account("clarity-recovery-filed-owner")
    admin = create_admin("clarity-recovery-filed-admin")
    folder = services.create_folder(owner=owner, name="Research Notes")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Filed Recovery Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Original folder: Research Notes" in content


@pytest.mark.django_db
def test_admin_recovery_shows_unfiled_wording_for_an_unfiled_note():
    owner = create_account("clarity-recovery-unfiled-owner")
    admin = create_admin("clarity-recovery-unfiled-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Unfiled Recovery Note Two")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert '<span class="note-list__location">Unfiled</span>' in content
    assert "Original folder: Unfiled" not in content


# -- Final purge metadata unchanged -----------------------------------------------


@pytest.mark.django_db
def test_owner_trash_final_purge_label_still_present_alongside_new_wording():
    owner = create_account("clarity-final-purge-owner")
    folder = services.create_folder(owner=owner, name="Still Timed Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Timed Trashed Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "<dt>Final purge</dt>" in content
    assert "From folder — Still Timed Folder" in content
