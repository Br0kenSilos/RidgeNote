"""Purge audit and privacy contract.

Exactly one aggregate `AuditEvent` is created per completed live cycle,
with `actor`/`target_user` both `None`, `source` set to the existing
`SOURCE_MANAGEMENT_COMMAND` constant, and `details` containing only the
documented aggregate fields -- never any note/folder/owner identity or
content.
"""

from datetime import timedelta

import pytest
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import services
from notes.models import Folder, Note
from notes.purge import run_purge_cycle

PASSWORD = "LongUniquePassword123!"

EXPECTED_DETAIL_KEYS = {
    "run_id",
    "cutoff",
    "notes_purged",
    "notes_failed",
    "folders_purged",
    "folders_blocked",
    "folders_failed",
    "duration_seconds",
}

FORBIDDEN_DETAIL_KEYS = {
    "note_id",
    "folder_id",
    "owner_id",
    "username",
    "title",
    "folder_name",
    "name",
    "path",
    "content",
}


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


@pytest.mark.django_db
def test_exactly_one_audit_event_per_completed_live_cycle():
    owner = create_account("purge-audit-count-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    folder = services.create_folder(owner=owner, name="Purge Audit Folder")
    trash_folder_days_ago(folder, days_ago=100)

    run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).count() == 1


@pytest.mark.django_db
def test_audit_actor_and_target_user_are_none():
    owner = create_account("purge-audit-actor-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED)
    assert event.actor is None
    assert event.target_user is None


@pytest.mark.django_db
def test_audit_source_is_management_command():
    owner = create_account("purge-audit-source-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED)
    assert event.source == AuditEvent.SOURCE_MANAGEMENT_COMMAND


@pytest.mark.django_db
def test_audit_details_contains_exactly_the_documented_fields():
    owner = create_account("purge-audit-details-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED)
    assert set(event.details.keys()) == EXPECTED_DETAIL_KEYS


@pytest.mark.django_db
def test_audit_details_never_contain_identifying_keys():
    owner = create_account("purge-audit-privacy-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    folder = services.create_folder(owner=owner, name="Privacy Folder")
    trash_folder_days_ago(folder, days_ago=100)

    run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED)
    assert not (FORBIDDEN_DETAIL_KEYS & set(event.details.keys()))
    assert owner.username not in str(event.details)
    assert note.title not in str(event.details)
    assert folder.name not in str(event.details)


@pytest.mark.django_db
def test_no_audit_event_for_dry_run():
    owner = create_account("purge-audit-no-dry-run-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_purge_cycle(dry_run=True, note_batch_size=200, folder_batch_size=200)

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).exists()


@pytest.mark.django_db
def test_no_audit_event_for_lock_contention_skip(monkeypatch):
    import notes.purge as purge_module

    owner = create_account("purge-audit-no-skip-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    monkeypatch.setattr(purge_module, "_try_acquire_purge_lock", lambda: False)

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert result.lock_acquired is False
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).exists()
    assert Note.objects.filter(pk=note.pk).exists()
