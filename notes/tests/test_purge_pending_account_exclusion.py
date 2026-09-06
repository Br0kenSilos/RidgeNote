"""Ordinary item purge pauses during pending account
deletion.

The account-deletion policy promises that all owned notes, folders, tags,
and Trash state remain completely unchanged while account deletion is
pending. `_select_note_candidates()`/`_select_folder_candidates()` exclude any
row owned by a user with `deletion_scheduled_at` set at selection time,
so that promise holds for the ordinary (non-racing) case covered here.
This exclusion is a live query-time filter, not a persisted skip-marker,
so cancelling account deletion makes previously-skipped rows eligible
again automatically, based on their own unchanged original timestamps.
Each per-candidate locked
transaction also re-locks the owning User and re-checks
`deletion_scheduled_at` before ever locking the Note/Folder row, closing
the race where scheduling commits after selection but before the
per-candidate transaction runs -- that specific race is covered by
`test_purge_race.py`, not here. General eligibility,
advisory-lock, and row-locking concurrency behavior are covered by
`test_purge.py`/`test_purge_race.py`; only the pending-account exclusion
itself is covered here.
"""

from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import services
from notes.models import Folder, Note
from notes.purge import run_purge_cycle

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


def trash_note_days_ago(note, *, days_ago):
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    note.refresh_from_db()
    return note


def trash_folder_days_ago(folder, *, days_ago):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    folder.refresh_from_db()
    return folder


def run_live(**overrides):
    kwargs = {"dry_run": False, "note_batch_size": 200, "folder_batch_size": 200}
    kwargs.update(overrides)
    return run_purge_cycle(**kwargs)


def run_dry(**overrides):
    kwargs = {"dry_run": True, "note_batch_size": 200, "folder_batch_size": 200}
    kwargs.update(overrides)
    return run_purge_cycle(**kwargs)


# -- note exclusion -----------------------------------------------------------


@pytest.mark.django_db
def test_pending_owner_eligible_note_excluded_from_dry_run():
    admin = create_admin("pending-note-dry-admin")
    owner = create_account("pending-note-dry-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    result = run_dry()

    assert result.notes_eligible == 0
    assert result.notes_purged == 0


@pytest.mark.django_db
def test_pending_owner_eligible_note_survives_live_purge():
    admin = create_admin("pending-note-live-admin")
    owner = create_account("pending-note-live-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    result = run_live()

    assert result.notes_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_other_owners_eligible_notes_still_purge_normally():
    admin = create_admin("pending-note-other-admin")
    pending_owner = create_account("pending-note-other-pending-owner")
    ordinary_owner = create_account("pending-note-other-ordinary-owner")
    pending_note = services.create_note(owner=pending_owner)
    trash_note_days_ago(pending_note, days_ago=100)
    ordinary_note = services.create_note(owner=ordinary_owner)
    trash_note_days_ago(ordinary_note, days_ago=100)
    account_services.schedule_user_deletion(target=pending_owner, actor=admin)

    result = run_live()

    assert result.notes_purged == 1
    assert Note.objects.filter(pk=pending_note.pk).exists()
    assert not Note.objects.filter(pk=ordinary_note.pk).exists()


# -- folder exclusion ---------------------------------------------------------


@pytest.mark.django_db
def test_pending_owner_eligible_folder_excluded_from_dry_run():
    admin = create_admin("pending-folder-dry-admin")
    owner = create_account("pending-folder-dry-owner")
    folder = services.create_folder(owner=owner, name="Pending Owner Folder")
    trash_folder_days_ago(folder, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    result = run_dry()

    assert result.folders_eligible == 0
    assert result.folders_purged == 0


@pytest.mark.django_db
def test_pending_owner_eligible_folder_survives_live_purge():
    admin = create_admin("pending-folder-live-admin")
    owner = create_account("pending-folder-live-owner")
    folder = services.create_folder(owner=owner, name="Pending Owner Live Folder")
    trash_folder_days_ago(folder, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    result = run_live()

    assert result.folders_purged == 0
    assert Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_folder_blocking_behavior_unaffected_by_pending_account_exclusion():
    """A folder belonging to a *non*-pending owner, still referenced by a
    Note that was cascaded into Trash alongside it (the only lifecycle
    state in which a Trashed Folder can be referenced at all, since
    `assign_note_folder()` rejects assigning any Note into an
    already-Trashed Folder), must continue to be reported as blocked,
    not purged -- the pending-account exclusion must not interfere with
    the existing, unrelated folder-reference safety check."""
    admin = create_admin("pending-folder-blocked-admin")
    owner = create_account("pending-folder-blocked-owner")
    folder = services.create_folder(owner=owner, name="Blocked Folder")
    referencing_note = services.create_note(owner=owner)
    services.assign_note_folder(note=referencing_note, folder=folder)
    trash_folder_days_ago(folder, days_ago=100)
    other_pending_owner = create_account("pending-folder-blocked-other-owner")
    account_services.schedule_user_deletion(target=other_pending_owner, actor=admin)

    result = run_live()

    assert result.folders_purged == 0
    assert result.folders_blocked == 1
    assert Folder.objects.filter(pk=folder.pk).exists()


# -- cancellation resumes ordinary eligibility --------------------------------


@pytest.mark.django_db
def test_cancelling_account_deletion_restores_ordinary_purge_eligibility():
    admin = create_admin("pending-resume-admin")
    owner = create_account("pending-resume-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    original_trashed_at = Note.objects.get(pk=note.pk).trashed_at

    account_services.schedule_user_deletion(target=owner, actor=admin)
    excluded_result = run_live()
    assert excluded_result.notes_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()

    account_services.cancel_user_deletion(target=owner, actor=admin)
    note.refresh_from_db()
    # The item's own original timestamp was never touched by scheduling,
    # exclusion, or cancellation.
    assert note.trashed_at == original_trashed_at

    resumed_result = run_live()
    assert resumed_result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_scheduling_account_deletion_does_not_alter_trashed_at_or_emptied_at():
    admin = create_admin("pending-timestamps-admin")
    owner = create_account("pending-timestamps-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now() - timedelta(days=50))
    note.refresh_from_db()
    original_trashed_at = note.trashed_at
    original_emptied_at = note.emptied_at

    account_services.schedule_user_deletion(target=owner, actor=admin)

    note.refresh_from_db()
    assert note.trashed_at == original_trashed_at
    assert note.emptied_at == original_emptied_at


# -- manual and automatic paths share the same exclusion ----------------------


@pytest.mark.django_db
def test_manual_purge_command_excludes_pending_owner_rows():
    from io import StringIO

    from django.core.management import call_command

    admin = create_admin("pending-command-admin")
    owner = create_account("pending-command-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    stdout = StringIO()
    call_command("purge_expired_trash", "--execute", stdout=stdout)

    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_automatic_scheduler_cycle_excludes_pending_owner_rows():
    """`run_purge_scheduler`'s automatic cycles call the exact same
    `run_purge_cycle()` used by the manual command, so the exclusion
    applies identically without any scheduler-specific code."""
    admin = create_admin("pending-scheduler-admin")
    owner = create_account("pending-scheduler-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    account_services.schedule_user_deletion(target=owner, actor=admin)

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert result.notes_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()
