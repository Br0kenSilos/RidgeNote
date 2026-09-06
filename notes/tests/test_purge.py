"""One-shot final purge.

`run_purge_cycle()` computes one fixed cutoff per cycle, processes notes
before folders in independent per-row top-level transactions, and (in live
mode) writes exactly one aggregate audit event after all row work
completes. Eligibility, note purge, folder purge, and dry-run behavior are
covered here. PostgreSQL advisory-lock and row-locking concurrency
scenarios live in `test_purge_race.py`; management-command argument and
exit-code behavior lives in `test_purge_command.py`; audit/privacy
assertions live in `test_purge_audit.py`.
"""

from datetime import timedelta

import pytest
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import documents, services
from notes.models import Folder, Note, Tag
from notes.purge import PurgeAuditWriteError, run_purge_cycle
from notes.services import TRASH_RECOVERABLE_MAX_AGE

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
    _rename_if_still_placeholder(note)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    note.refresh_from_db()
    return note


def trash_folder_days_ago(folder, *, days_ago):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))
    folder.refresh_from_db()
    return folder


# A `timedelta(days=90)` boundary set at test-setup time drifts across the
# cutoff by the time `run_purge_cycle()` later computes its own
# `timezone.now()`-based cutoff, since real wall-clock time passes between
# the two calls -- a several-minute buffer keeps the "just inside"/"just
# outside" boundary tests deterministic regardless of that gap.
BOUNDARY_BUFFER = timedelta(minutes=10)


def _rename_if_still_placeholder(note):
    # Only rename if the caller left the note as a genuinely untouched
    # placeholder -- a caller that already gave it a meaningful title (to
    # assert on later) must not have that title silently overwritten here.
    if note.title == documents.generated_title_for_timestamp(note.created_at):
        services.rename_note(note=note, title="Recoverable Note")


def trash_note_just_inside_recoverable_window(note):
    _rename_if_still_placeholder(note)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - TRASH_RECOVERABLE_MAX_AGE + BOUNDARY_BUFFER
    )
    note.refresh_from_db()
    return note


def trash_note_just_beyond_recoverable_window(note):
    _rename_if_still_placeholder(note)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - TRASH_RECOVERABLE_MAX_AGE - BOUNDARY_BUFFER
    )
    note.refresh_from_db()
    return note


def trash_folder_just_inside_recoverable_window(folder):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(
        trashed_at=timezone.now() - TRASH_RECOVERABLE_MAX_AGE + BOUNDARY_BUFFER
    )
    folder.refresh_from_db()
    return folder


def trash_folder_just_beyond_recoverable_window(folder):
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(
        trashed_at=timezone.now() - TRASH_RECOVERABLE_MAX_AGE - BOUNDARY_BUFFER
    )
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


# -- eligibility --------------------------------------------------------------


@pytest.mark.django_db
def test_note_exactly_at_90_days_is_not_purge_eligible():
    owner = create_account("purge-eligibility-note-90-owner")
    note = services.create_note(owner=owner)
    trash_note_just_inside_recoverable_window(note)

    result = run_live()

    assert result.notes_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_note_just_beyond_90_days_is_purge_eligible():
    owner = create_account("purge-eligibility-note-91-owner")
    note = services.create_note(owner=owner)
    trash_note_just_beyond_recoverable_window(note)

    result = run_live()

    assert result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_folder_exactly_at_90_days_is_not_purge_eligible():
    owner = create_account("purge-eligibility-folder-90-owner")
    folder = services.create_folder(owner=owner, name="Folder 90")
    trash_folder_just_inside_recoverable_window(folder)

    result = run_live()

    assert result.folders_purged == 0
    assert Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_folder_just_beyond_90_days_is_purge_eligible():
    owner = create_account("purge-eligibility-folder-91-owner")
    folder = services.create_folder(owner=owner, name="Folder 91")
    trash_folder_just_beyond_recoverable_window(folder)

    result = run_live()

    assert result.folders_purged == 1
    assert not Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_active_and_younger_rows_are_excluded_from_candidates():
    owner = create_account("purge-eligibility-untouched-owner")
    active_note = services.create_note(owner=owner)
    young_note = services.create_note(owner=owner)
    trash_note_days_ago(young_note, days_ago=10)
    active_folder = services.create_folder(owner=owner, name="Active Folder")
    young_folder = services.create_folder(owner=owner, name="Young Folder")
    trash_folder_days_ago(young_folder, days_ago=10)

    result = run_live()

    assert result.notes_purged == 0
    assert result.folders_purged == 0
    assert Note.objects.filter(pk=active_note.pk).exists()
    assert Note.objects.filter(pk=young_note.pk).exists()
    assert Folder.objects.filter(pk=active_folder.pk).exists()
    assert Folder.objects.filter(pk=young_folder.pk).exists()


@pytest.mark.django_db
def test_emptied_at_does_not_alter_eligibility():
    owner = create_account("purge-eligibility-emptied-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=91)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    result = run_live()

    assert result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_fixed_cutoff_consistent_across_result_and_audit_payload():
    owner = create_account("purge-eligibility-cutoff-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=91)

    result = run_live()

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED)
    assert event.details["cutoff"] == result.cutoff.isoformat()


# -- notes ----------------------------------------------------------------


@pytest.mark.django_db
def test_eligible_note_is_purged():
    owner = create_account("purge-note-eligible-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_live()

    assert not Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_younger_or_active_note_untouched():
    owner = create_account("purge-note-untouched-owner")
    active_note = services.create_note(owner=owner)
    younger_note = services.create_note(owner=owner)
    trash_note_days_ago(younger_note, days_ago=5)

    run_live()

    active_note.refresh_from_db()
    younger_note.refresh_from_db()
    assert active_note.trashed_at is None
    assert younger_note.trashed_at is not None


@pytest.mark.django_db
def test_tag_rows_survive_note_purge():
    owner = create_account("purge-note-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Keepsake", color="blue")
    note.tags.add(tag)
    trash_note_days_ago(note, days_ago=100)

    run_live()

    assert not Note.objects.filter(pk=note.pk).exists()
    assert Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_note_tag_m2m_association_removed_automatically():
    owner = create_account("purge-note-m2m-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="Ephemeral", color="green")
    note.tags.add(tag)
    trash_note_days_ago(note, days_ago=100)

    run_live()

    assert not Note.tags.through.objects.filter(tag_id=tag.pk).exists()


@pytest.mark.django_db
def test_missing_note_candidate_skipped_not_a_failure():
    owner = create_account("purge-note-missing-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    Note.objects.filter(pk=note.pk).delete()

    result = run_live()

    assert result.notes_failed == 0
    assert result.notes_purged == 0


@pytest.mark.django_db
def test_one_note_deletion_failure_does_not_affect_siblings(monkeypatch):
    owner = create_account("purge-note-failure-isolation-owner")
    failing_note = services.create_note(owner=owner)
    trash_note_days_ago(failing_note, days_ago=100)
    healthy_note = services.create_note(owner=owner)
    trash_note_days_ago(healthy_note, days_ago=100)

    original_delete = Note.delete

    def failing_delete(self, *args, **kwargs):
        if self.pk == failing_note.pk:
            raise RuntimeError("simulated note deletion failure")
        return original_delete(self, *args, **kwargs)

    monkeypatch.setattr(Note, "delete", failing_delete)

    result = run_live()

    assert result.notes_failed == 1
    assert result.notes_purged == 1
    assert Note.objects.filter(pk=failing_note.pk).exists()
    assert not Note.objects.filter(pk=healthy_note.pk).exists()


@pytest.mark.django_db
def test_note_batch_size_bounds_a_single_invocation():
    owner = create_account("purge-note-batch-owner")
    notes = [services.create_note(owner=owner) for _ in range(3)]
    for note in notes:
        trash_note_days_ago(note, days_ago=100)

    result = run_live(note_batch_size=2)

    assert result.notes_eligible == 2
    assert result.notes_purged == 2
    assert Note.objects.filter(pk__in=[n.pk for n in notes]).count() == 1


# -- folders ----------------------------------------------------------------


@pytest.mark.django_db
def test_eligible_unreferenced_folder_is_purged():
    owner = create_account("purge-folder-eligible-owner")
    folder = services.create_folder(owner=owner, name="Empty Purge Folder")
    trash_folder_days_ago(folder, days_ago=100)

    run_live()

    assert not Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_folder_referenced_by_active_note_is_blocked():
    owner = create_account("purge-folder-blocked-active-owner")
    folder = services.create_folder(owner=owner, name="Blocked By Active")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_folder_days_ago(folder, days_ago=100)

    result = run_live()

    assert result.folders_blocked == 1
    assert result.folders_purged == 0
    assert Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_folder_referenced_by_younger_recoverable_note_is_blocked():
    owner = create_account("purge-folder-blocked-younger-owner")
    folder = services.create_folder(owner=owner, name="Blocked By Younger")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_note_days_ago(note, days_ago=5)
    trash_folder_days_ago(folder, days_ago=100)

    result = run_live()

    assert result.folders_blocked == 1
    assert Folder.objects.filter(pk=folder.pk).exists()
    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_folder_referenced_by_note_purge_eligible_but_not_yet_purged_is_blocked():
    owner = create_account("purge-folder-blocked-batch-owner")
    folder = services.create_folder(owner=owner, name="Blocked By Batch Limit")
    older_note = services.create_note(owner=owner)
    trash_note_days_ago(older_note, days_ago=105)
    blocking_note = services.create_note(owner=owner)
    services.assign_note_folder(note=blocking_note, folder=folder)
    trash_note_days_ago(blocking_note, days_ago=100)
    trash_folder_days_ago(folder, days_ago=100)

    # The batch admits only the older note (ordered ascending by trashed_at),
    # so the blocking note remains unpurged when the folder phase runs in
    # this same cycle.
    result = run_live(note_batch_size=1)

    assert result.notes_purged == 1
    assert Note.objects.filter(pk=blocking_note.pk).exists()
    assert result.folders_blocked == 1
    assert Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_no_referenced_note_mutated_by_folder_phase():
    owner = create_account("purge-folder-no-mutation-owner")
    folder = services.create_folder(owner=owner, name="No Mutation Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_folder_days_ago(folder, days_ago=100)
    # Trashing the folder cascades `trashed_at` onto its still-active
    # contained notes (existing, unmodified `move_folder_to_trash()`
    # behavior) -- re-fetch the note to capture that cascaded baseline
    # before asserting the folder phase leaves it untouched.
    note.refresh_from_db()

    before_folder_id = note.folder_id
    before_trashed_at = note.trashed_at
    before_emptied_at = note.emptied_at

    run_live()

    note.refresh_from_db()
    assert note.folder_id == before_folder_id
    assert note.trashed_at == before_trashed_at
    assert note.emptied_at == before_emptied_at


@pytest.mark.django_db
def test_one_folder_deletion_failure_does_not_affect_siblings(monkeypatch):
    owner = create_account("purge-folder-failure-isolation-owner")
    failing_folder = services.create_folder(owner=owner, name="Failing Folder")
    trash_folder_days_ago(failing_folder, days_ago=100)
    healthy_folder = services.create_folder(owner=owner, name="Healthy Folder")
    trash_folder_days_ago(healthy_folder, days_ago=100)

    original_delete = Folder.delete

    def failing_delete(self, *args, **kwargs):
        if self.pk == failing_folder.pk:
            raise RuntimeError("simulated folder deletion failure")
        return original_delete(self, *args, **kwargs)

    monkeypatch.setattr(Folder, "delete", failing_delete)

    result = run_live()

    assert result.folders_failed == 1
    assert result.folders_purged == 1
    assert Folder.objects.filter(pk=failing_folder.pk).exists()
    assert not Folder.objects.filter(pk=healthy_folder.pk).exists()


@pytest.mark.django_db
def test_notes_fully_processed_before_folders_unblocks_same_cycle():
    owner = create_account("purge-order-owner")
    folder = services.create_folder(owner=owner, name="Unblocked Same Cycle")
    blocking_note = services.create_note(owner=owner)
    services.assign_note_folder(note=blocking_note, folder=folder)
    trash_note_days_ago(blocking_note, days_ago=100)
    trash_folder_days_ago(folder, days_ago=100)

    result = run_live()

    assert result.notes_purged == 1
    assert result.folders_purged == 1
    assert result.folders_blocked == 0
    assert not Note.objects.filter(pk=blocking_note.pk).exists()
    assert not Folder.objects.filter(pk=folder.pk).exists()


# -- dry-run ------------------------------------------------------------------


@pytest.mark.django_db
def test_dry_run_performs_no_mutation():
    owner = create_account("purge-dry-run-no-mutation-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    folder = services.create_folder(owner=owner, name="Dry Run Folder")
    trash_folder_days_ago(folder, days_ago=100)

    result = run_dry()

    assert result.notes_purged == 0
    assert result.folders_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()
    assert Folder.objects.filter(pk=folder.pk).exists()


@pytest.mark.django_db
def test_dry_run_creates_no_audit_event():
    owner = create_account("purge-dry-run-no-audit-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    run_dry()

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).exists()


@pytest.mark.django_db
def test_dry_run_counts_match_subsequent_live_run():
    owner = create_account("purge-dry-run-matches-live-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)
    folder = services.create_folder(owner=owner, name="Matches Live Folder")
    trash_folder_days_ago(folder, days_ago=100)

    dry_result = run_dry()
    live_result = run_live()

    assert dry_result.notes_eligible == live_result.notes_eligible
    assert dry_result.folders_eligible == live_result.folders_eligible
    assert dry_result.notes_eligible == live_result.notes_purged
    assert dry_result.folders_eligible == live_result.folders_purged


@pytest.mark.django_db
def test_dry_run_uses_same_fixed_cutoff_semantics():
    owner = create_account("purge-dry-run-cutoff-owner")
    boundary_note = services.create_note(owner=owner)
    trash_note_just_inside_recoverable_window(boundary_note)
    eligible_note = services.create_note(owner=owner)
    trash_note_just_beyond_recoverable_window(eligible_note)

    result = run_dry()

    assert result.notes_eligible == 1


# -- aggregate audit-failure contract ----------------------------------------


@pytest.mark.django_db
def test_audit_write_failure_preserves_already_committed_purges(monkeypatch):
    owner = create_account("purge-audit-failure-owner")
    note = services.create_note(owner=owner)
    trash_note_days_ago(note, days_ago=100)

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(
        "notes.purge.account_services.record_audit_event", failing_record_audit_event
    )

    with pytest.raises(PurgeAuditWriteError) as exc_info:
        run_live()

    assert not Note.objects.filter(pk=note.pk).exists()
    assert exc_info.value.result.notes_purged == 1
    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).exists()
