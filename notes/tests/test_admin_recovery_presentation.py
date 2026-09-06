"""Administrator Recovery section-order
consistency.

Administrator Recovery previously rendered its Notes section before its
Folders section, the opposite of Trash and of the tree's own
folders-before-notes convention (used identically by Home, All Notes, and
Trash). This file proves the corrected order (Folders, then Notes) and
that Trash's own order (already Folders, then Notes) was not disturbed.
Eligibility/restore-behavior policy is already covered by
`test_admin_folder_restore.py` and `test_admin_note_restore.py`; this
file is presentation-order only.
"""

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import documents, services

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


def trash_and_empty_note(note):
    if note.title == documents.generated_title_for_timestamp(note.created_at):
        services.rename_note(note=note, title="Recoverable Note")
    services.move_note_to_trash(note=note)
    note.__class__.objects.filter(pk=note.pk).update(emptied_at=timezone.now())


def trash_and_empty_folder(folder):
    services.move_folder_to_trash(folder=folder)
    folder.__class__.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())


@pytest.mark.django_db
def test_administrator_recovery_renders_folders_before_notes_when_both_present():
    owner = create_account("adminrecovery-order-owner")
    admin = create_admin("adminrecovery-order-admin")
    folder = services.create_folder(owner=owner, name="Recoverable Folder")
    trash_and_empty_folder(folder)
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert content.count("<h2>Folders</h2>") == 1
    assert content.count("<h2>Notes</h2>") == 1
    assert content.index("<h2>Folders</h2>") < content.index("<h2>Notes</h2>")


@pytest.mark.django_db
def test_administrator_recovery_folder_count_and_row_stay_within_folders_section():
    owner = create_account("adminrecovery-folder-scope-owner")
    admin = create_admin("adminrecovery-folder-scope-admin")
    folder = services.create_folder(owner=owner, name="Scoped Recoverable Folder")
    trash_and_empty_folder(folder)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    folders_start = content.index("<h2>Folders</h2>")
    folder_row_position = content.index("Scoped Recoverable Folder")
    restore_count_position = content.index("Restores 0 notes")
    assert folders_start < folder_row_position
    assert folders_start < restore_count_position
    assert "<h2>Notes</h2>" not in content


@pytest.mark.django_db
def test_administrator_recovery_note_count_and_row_stay_within_notes_section():
    owner = create_account("adminrecovery-note-scope-owner")
    admin = create_admin("adminrecovery-note-scope-admin")
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    notes_start = content.index("<h2>Notes</h2>")
    note_row_position = content.index("Recoverable Note")
    assert notes_start < note_row_position
    assert "<h2>Folders</h2>" not in content


@pytest.mark.django_db
def test_administrator_recovery_folder_empty_state_absent_when_only_notes_recoverable():
    owner = create_account("adminrecovery-folder-empty-owner")
    admin = create_admin("adminrecovery-folder-empty-admin")
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "<h2>Folders</h2>" not in content
    assert "<h2>Notes</h2>" in content


@pytest.mark.django_db
def test_administrator_recovery_note_empty_state_absent_when_only_folders_recoverable():
    owner = create_account("adminrecovery-note-empty-owner")
    admin = create_admin("adminrecovery-note-empty-admin")
    folder = services.create_folder(owner=owner, name="Only Folder Recoverable")
    trash_and_empty_folder(folder)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "<h2>Notes</h2>" not in content
    assert "<h2>Folders</h2>" in content


@pytest.mark.django_db
def test_administrator_recovery_nothing_to_recover_empty_state_unaffected():
    admin = create_admin("adminrecovery-nothing-admin")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Nothing to recover" in content
    assert "<h2>Folders</h2>" not in content
    assert "<h2>Notes</h2>" not in content


@pytest.mark.django_db
def test_administrator_recovery_restore_forms_and_routes_unchanged():
    owner = create_account("adminrecovery-forms-owner")
    admin = create_admin("adminrecovery-forms-admin")
    folder = services.create_folder(owner=owner, name="Forms Recoverable Folder")
    trash_and_empty_folder(folder)
    note = services.create_note(owner=owner)
    trash_and_empty_note(note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    folder_restore_action = reverse("notes:admin_folder_restore", args=[folder.id])
    note_restore_action = reverse("notes:admin_note_restore", args=[note.id])
    assert f'data-restore-action="{folder_restore_action}"' in content
    assert f'action="{note_restore_action}"' in content
    assert content.count("csrfmiddlewaretoken") >= 1
    assert content.count("<form") == content.count("</form>")


@pytest.mark.django_db
def test_trash_still_renders_folders_before_notes():
    owner = create_account("adminrecovery-trash-regression-owner")
    folder = services.create_folder(owner=owner, name="Trash Regression Folder")
    services.move_folder_to_trash(folder=folder)
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trash Regression Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert content.count("<h2>Folders</h2>") == 1
    assert content.count("<h2>Notes</h2>") == 1
    assert content.index("<h2>Folders</h2>") < content.index("<h2>Notes</h2>")
