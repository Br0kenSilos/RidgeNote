"""Recovered Items marker foundation.

Focused coverage for `Folder.is_recovery_folder`: default value, the partial
per-owner uniqueness constraint, unconditional demotion at Trash time via the
real `move_folder_to_trash()` service, and absence of the marker from every
owner-facing and administrator-facing surface. This module covers marker
identity infrastructure only -- no recovery-folder creation helper or naming
helper is exercised here, so these tests
exercise the marker directly (via `Folder.objects.create(...)`), not through
any destination-resolution logic.
"""

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Folder

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


# -- field default -----------------------------------------------------------


@pytest.mark.django_db
def test_new_folder_defaults_to_unmarked():
    owner = create_account("marker-default-owner")
    folder = services.create_folder(owner=owner, name="Ordinary Folder")

    assert folder.is_recovery_folder is False


# -- cardinality and constraint -----------------------------------------------


@pytest.mark.django_db
def test_owner_may_create_one_marked_folder():
    owner = create_account("marker-single-owner")
    marked = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)

    marked.refresh_from_db()
    assert marked.is_recovery_folder is True


@pytest.mark.django_db
def test_same_owner_cannot_create_two_marked_folders():
    owner = create_account("marker-duplicate-owner")
    Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        Folder.objects.create(owner=owner, name="Recovered Items (2)", is_recovery_folder=True)


@pytest.mark.django_db
def test_different_owners_may_each_have_one_marked_folder():
    owner_a = create_account("marker-owner-a")
    owner_b = create_account("marker-owner-b")

    folder_a = Folder.objects.create(owner=owner_a, name="Recovered Items", is_recovery_folder=True)
    folder_b = Folder.objects.create(owner=owner_b, name="Recovered Items", is_recovery_folder=True)

    folder_a.refresh_from_db()
    folder_b.refresh_from_db()
    assert folder_a.is_recovery_folder is True
    assert folder_b.is_recovery_folder is True


# -- Trash-time demotion -------------------------------------------------------


@pytest.mark.django_db
def test_trashing_marked_folder_clears_marker_via_real_service():
    owner = create_account("marker-trash-owner")
    folder = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)

    services.move_folder_to_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.is_recovery_folder is False


@pytest.mark.django_db
def test_trashing_ordinary_folder_leaves_marker_false():
    owner = create_account("marker-ordinary-trash-owner")
    folder = services.create_folder(owner=owner, name="Ordinary Folder")
    assert folder.is_recovery_folder is False

    services.move_folder_to_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.is_recovery_folder is False


@pytest.mark.django_db
def test_demoted_folder_restores_as_ordinary_unmarked_folder():
    owner = create_account("marker-restore-owner")
    folder = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.move_folder_to_trash(folder=folder)

    services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is None
    assert folder.is_recovery_folder is False


@pytest.mark.django_db
def test_a_replacement_may_be_created_after_the_original_is_demoted():
    """Confirms the constraint permits this exact sequence: an old
    marked-but-trashed folder demoted at
    Trash time, followed by a new marked replacement, followed by the owner
    freely restoring the old (now-ordinary) folder -- with no conflict."""
    owner = create_account("marker-replacement-owner")
    original = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.move_folder_to_trash(folder=original)

    replacement = Folder.objects.create(
        owner=owner, name="Recovered Items (2)", is_recovery_folder=True
    )

    services.restore_folder_from_trash(folder=original)

    original.refresh_from_db()
    replacement.refresh_from_db()
    assert original.trashed_at is None
    assert original.is_recovery_folder is False
    assert replacement.is_recovery_folder is True


@pytest.mark.django_db
def test_renaming_marked_folder_preserves_marker():
    owner = create_account("marker-rename-owner")
    folder = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)

    services.rename_folder(folder=folder, name="Renamed Recovery Folder")

    folder.refresh_from_db()
    assert folder.name == "Renamed Recovery Folder"
    assert folder.is_recovery_folder is True


@pytest.mark.django_db
def test_trashing_marked_folder_still_cascades_trashed_at_to_active_notes():
    owner = create_account("marker-cascade-owner")
    folder = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    services.move_folder_to_trash(folder=folder)

    note.refresh_from_db()
    folder.refresh_from_db()
    assert note.trashed_at is not None
    assert folder.is_recovery_folder is False


# -- visibility: the marker must not leak anywhere ----------------------------


@pytest.mark.django_db
def test_marker_absent_from_owner_trash_response():
    owner = create_account("marker-owner-trash-visibility-owner")
    folder = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    # The internal `is_recovery_folder` marker/field name is the actual
    # invariant this module protects (consistently, as the sole check,
    # in the two sibling visibility tests below) -- not the ordinary
    # English word "recovery" itself. The In-App Help
    # legitimately includes a "Trash and Recovery" Help section, rendered globally
    # via the shared application shell on every authenticated page
    # including this one; a blanket word-ban here would fail on that
    # unrelated, intentional, ordinary-user-facing content rather than on
    # any actual leak of administrator-only recovery-folder identity.
    assert "is_recovery_folder" not in content


@pytest.mark.django_db
def test_marker_absent_from_administrator_recovery_projection():
    owner = create_account("marker-admin-projection-owner")
    admin = create_admin("marker-admin-projection-admin")
    folder = Folder.objects.create(owner=owner, name="Marker Admin Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    items = services.list_administrator_recoverable_folders()
    matching = next(item for item in items if item["title"] == "Marker Admin Folder")

    assert "is_recovery_folder" not in matching

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()
    assert "is_recovery_folder" not in content
