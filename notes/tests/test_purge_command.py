"""`purge_expired_trash` management command.

The command is a thin argument-parsing and output-formatting wrapper
around `run_purge_cycle()`: exactly one of `--dry-run`/`--execute` is
required, batch-size options must be positive integers, and the printed
summary never contains identifying content.
"""

from datetime import timedelta
from io import StringIO

import pytest
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
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


def trash_note_days_ago(note, *, days_ago):
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    note.refresh_from_db()
    return note


@pytest.mark.django_db
def test_neither_mode_flag_is_rejected_and_deletes_nothing():
    owner = create_account("purge-cmd-neither-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    with pytest.raises(CommandError):
        call_command("purge_expired_trash")

    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_both_mode_flags_rejected():
    with pytest.raises(CommandError):
        call_command("purge_expired_trash", "--dry-run", "--execute")


@pytest.mark.django_db
def test_invalid_note_batch_size_rejected_before_service_runs():
    owner = create_account("purge-cmd-invalid-batch-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    with pytest.raises(CommandError):
        call_command("purge_expired_trash", "--execute", "--note-batch-size", "0")

    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_invalid_folder_batch_size_rejected_before_service_runs():
    with pytest.raises(CommandError):
        call_command("purge_expired_trash", "--dry-run", "--folder-batch-size", "-3")


@pytest.mark.django_db
def test_dry_run_exits_cleanly_and_reports_no_identifying_content():
    owner = create_account("purge-cmd-dry-run-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    stdout = StringIO()

    call_command("purge_expired_trash", "--dry-run", stdout=stdout)

    output = stdout.getvalue()
    assert note.title not in output
    assert owner.username not in output
    assert "point-in-time dry-run result" in output
    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_execute_purges_and_reports_summary():
    owner = create_account("purge-cmd-execute-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    stdout = StringIO()

    call_command("purge_expired_trash", "--execute", stdout=stdout)

    assert not Note.objects.filter(pk=note.pk).exists()
    output = stdout.getvalue()
    assert "notes: eligible=1 purged=1 failed=0" in output
    assert "Purge cycle complete." in output


@pytest.mark.django_db
def test_lock_contention_skip_exits_cleanly(monkeypatch):
    import notes.purge as purge_module

    monkeypatch.setattr(purge_module, "_try_acquire_purge_lock", lambda: False)
    stdout = StringIO()

    call_command("purge_expired_trash", "--execute", stdout=stdout)

    assert "Skipped: another purge cycle already holds the advisory lock." in stdout.getvalue()
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).exists()


@pytest.mark.django_db
def test_audit_write_failure_exits_nonzero_but_reports_row_summary(monkeypatch):
    owner = create_account("purge-cmd-audit-failure-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(
        "notes.purge.account_services.record_audit_event", failing_record_audit_event
    )

    stdout = StringIO()
    with pytest.raises(CommandError):
        call_command("purge_expired_trash", "--execute", stdout=stdout)

    output = stdout.getvalue()
    assert "notes: eligible=1 purged=1 failed=0" in output
    assert not Note.objects.filter(pk=note.pk).exists()
