"""Administrator note restore.

`restore_note_for_administrator()` restores a single administrator-recoverable
note: it locks the note row, re-evaluates eligibility, resolves a safe
destination (the original folder if still active and owner-correct,
otherwise the owner's marker-based "Recovered Items" recovery folder,
created lazily with collision-safe naming if needed), clears the lifecycle
fields, and records exactly one audit event -- all inside one outer
transaction, so a failure anywhere rolls the whole restore back.

Several scenarios here require genuine, separate-connection concurrency to
exercise real PostgreSQL row-locking and unique-constraint behavior (a
single connection never blocks on a lock it already holds, and never
receives an IntegrityError from a row it cannot yet see); those tests use
real background threads with `pytest.mark.django_db(transaction=True)`,
following the same pattern established by `test_trash_restore_race.py`.
Constraint-discrimination branches that are otherwise only reachable via a
rare race are exercised deterministically with a controlled fake
`IntegrityError` carrying the real constraint name, isolating the service's
error-handling logic from timing.
"""

import threading
import time
from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import documents, services
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


def trash_and_hide(note, *, days_ago=45):
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))


def trash_and_empty(note):
    # Only rename if the caller left the note as a genuinely untouched
    # placeholder -- callers that already gave it a meaningful title (to
    # assert on later) must not have that title silently overwritten here.
    if note.title == documents.generated_title_for_timestamp(note.created_at):
        services.rename_note(note=note, title="Recoverable Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())


class _FakeConstraintDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeConstraintCause(Exception):
    def __init__(self, constraint_name):
        self.diag = _FakeConstraintDiag(constraint_name)


def _fake_integrity_error(constraint_name):
    exc = IntegrityError("duplicate key value violates unique constraint")
    exc.__cause__ = _FakeConstraintCause(constraint_name)
    return exc


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- projection and privacy ------------------------------------------------------


@pytest.mark.django_db
def test_note_id_present_in_administrator_projection():
    owner = create_account("restore-projection-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Projection Id Note")
    trash_and_empty(note)

    items = services.list_administrator_recoverable_notes()
    matching = next(item for item in items if item["title"] == "Projection Id Note")

    assert matching["id"] == note.id


@pytest.mark.django_db
def test_administrator_projection_excludes_content_fields():
    owner = create_account("restore-projection-content-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Projection Content Note")
    trash_and_empty(note)

    items = services.list_administrator_recoverable_notes()
    matching = next(item for item in items if item["title"] == "Projection Content Note")

    forbidden_keys = {"body_json", "body_plain_text", "tags", "pinned"}
    assert forbidden_keys.isdisjoint(matching.keys())


@pytest.mark.django_db
def test_restore_response_contains_no_content_preview():
    owner = create_account("restore-preview-owner")
    admin = create_admin("restore-preview-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Preview Guard Note")
    Note.objects.filter(pk=note.pk).update(body_plain_text="SECRET_PREVIEW_VALUE_XYZ")
    trash_and_empty(note)
    note.refresh_from_db()

    response = authenticated_client(admin).post(
        reverse("notes:admin_note_restore", args=[note.id]), follow=True
    )

    assert b"SECRET_PREVIEW_VALUE_XYZ" not in response.content


# -- eligibility -------------------------------------------------------------------


@pytest.mark.django_db
def test_administrator_can_restore_age_based_recoverable_note():
    owner = create_account("restore-age-owner")
    admin = create_admin("restore-age-admin")
    note = services.create_note(owner=owner)
    trash_and_hide(note, days_ago=45)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.note_id == note.id
    note.refresh_from_db()
    assert note.trashed_at is None


@pytest.mark.django_db
def test_administrator_can_restore_empty_trash_recoverable_note():
    owner = create_account("restore-emptied-owner")
    admin = create_admin("restore-emptied-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.note_id == note.id
    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.emptied_at is None


@pytest.mark.django_db
def test_active_note_restore_rejected():
    owner = create_account("restore-active-owner")
    admin = create_admin("restore-active-admin")
    note = services.create_note(owner=owner)

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_for_administrator(note_id=note.id, actor=admin)


@pytest.mark.django_db
def test_owner_visible_note_restore_rejected():
    owner = create_account("restore-owner-visible-owner")
    admin = create_admin("restore-owner-visible-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_for_administrator(note_id=note.id, actor=admin)


@pytest.mark.django_db
def test_beyond_90_day_note_restore_rejected():
    owner = create_account("restore-too-old-owner")
    admin = create_admin("restore-too-old-admin")
    note = services.create_note(owner=owner)
    trash_and_hide(note, days_ago=91)

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_for_administrator(note_id=note.id, actor=admin)


@pytest.mark.django_db
def test_missing_note_restore_rejected_safely():
    admin = create_admin("restore-missing-admin")

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_for_administrator(note_id=999_999, actor=admin)


@pytest.mark.django_db(transaction=True)
def test_second_administrator_restore_rejected_after_first_succeeds(monkeypatch):
    """Genuine cross-transaction race: the second administrator's restore
    must block on the note's row lock until the first commits, then observe
    the now-active note and fail safely -- not silently double-restore."""
    owner = create_account("restore-second-admin-owner")
    admin_a = create_admin("restore-second-admin-a")
    admin_b = create_admin("restore-second-admin-b")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services._is_hidden_but_administrator_recoverable

    def pausing_check(*, trashed_at, emptied_at, now):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "second restore never signaled proceed"
        return original_check(trashed_at=trashed_at, emptied_at=emptied_at, now=now)

    monkeypatch.setattr(services, "_is_hidden_but_administrator_recoverable", pausing_check)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.restore_note_for_administrator(
                note_id=note.id, actor=admin_a
            )
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first restore did not reach the locked eligibility check"

    outcome_b = {}

    def run_b():
        try:
            services.restore_note_for_administrator(note_id=note.id, actor=admin_b)
            outcome_b["ok"] = True
        except services.TrashItemNotRestorableError:
            outcome_b["rejected"] = True
        except Exception as exc:  # pragma: no cover
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)

    proceed.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert "error" not in outcome_a, outcome_a.get("error")
    assert outcome_a["result"].note_id == note.id
    assert "error" not in outcome_b, outcome_b.get("error")
    assert outcome_b.get("rejected") is True

    assert (
        AuditEvent.objects.filter(
            event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE,
            target_username_snapshot=owner.username,
        ).count()
        == 1
    )


# -- lifecycle and destination -----------------------------------------------------


@pytest.mark.django_db
def test_trashed_at_cleared():
    owner = create_account("restore-lifecycle-trashed-owner")
    admin = create_admin("restore-lifecycle-trashed-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    note.refresh_from_db()
    assert note.trashed_at is None


@pytest.mark.django_db
def test_emptied_at_cleared():
    owner = create_account("restore-lifecycle-emptied-owner")
    admin = create_admin("restore-lifecycle-emptied-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    note.refresh_from_db()
    assert note.emptied_at is None


@pytest.mark.django_db
def test_safe_original_folder_retained():
    owner = create_account("restore-original-folder-owner")
    admin = create_admin("restore-original-folder-admin")
    folder = services.create_folder(owner=owner, name="Safe Original Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_and_empty(note)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.destination_category == services.DESTINATION_CATEGORY_ORIGINAL_FOLDER
    assert result.fallback_used is False
    note.refresh_from_db()
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_trashed_original_folder_triggers_fallback():
    owner = create_account("restore-fallback-folder-owner")
    admin = create_admin("restore-fallback-folder-admin")
    folder = services.create_folder(owner=owner, name="Fallback Trigger Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    trash_and_empty(note)
    services.move_folder_to_trash(folder=folder)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.destination_category == services.DESTINATION_CATEGORY_RECOVERED_ITEMS
    assert result.fallback_used is True
    note.refresh_from_db()
    assert note.folder_id != folder.id
    destination = Folder.objects.get(pk=note.folder_id)
    assert destination.is_recovery_folder is True
    assert destination.name == "Recovered Items"


@pytest.mark.django_db
def test_restored_note_becomes_owner_visible():
    owner = create_account("restore-owner-visible-listing-owner")
    admin = create_admin("restore-owner-visible-listing-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Owner Visible After Restore")
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    active_titles = [n.title for n in Note.objects.filter(owner=owner, trashed_at__isnull=True)]
    assert "Owner Visible After Restore" in active_titles


@pytest.mark.django_db
def test_note_disappears_from_administrator_recovery_listing():
    owner = create_account("restore-listing-disappear-owner")
    admin = create_admin("restore-listing-disappear-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Disappearing Recovery Note")
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    items = services.list_administrator_recoverable_notes()
    assert all(item["title"] != "Disappearing Recovery Note" for item in items)


# -- recovery destination ------------------------------------------------------------


@pytest.mark.django_db
def test_lazy_marked_folder_created_when_none_exists():
    owner = create_account("restore-lazy-create-owner")
    admin = create_admin("restore-lazy-create-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    assert not Folder.objects.filter(owner=owner, is_recovery_folder=True).exists()

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert Folder.objects.filter(
        owner=owner, is_recovery_folder=True, name="Recovered Items"
    ).exists()


@pytest.mark.django_db
def test_existing_marked_folder_reused():
    owner = create_account("restore-reuse-marked-owner")
    admin = create_admin("restore-reuse-marked-admin")
    existing = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.destination_folder_id == existing.id
    assert Folder.objects.filter(owner=owner, is_recovery_folder=True).count() == 1


@pytest.mark.django_db
def test_renamed_marked_folder_reused_by_marker():
    owner = create_account("restore-renamed-marked-owner")
    admin = create_admin("restore-renamed-marked-admin")
    existing = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.rename_folder(folder=existing, name="My Renamed Recovery Spot")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.destination_folder_id == existing.id
    destination = Folder.objects.get(pk=existing.id)
    assert destination.name == "My Renamed Recovery Spot"


@pytest.mark.django_db
def test_unmarked_recovered_items_folder_untouched():
    owner = create_account("restore-unmarked-untouched-owner")
    admin = create_admin("restore-unmarked-untouched-admin")
    unrelated = services.create_folder(owner=owner, name="Recovered Items")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    unrelated.refresh_from_db()
    assert unrelated.is_recovery_folder is False
    assert unrelated.name == "Recovered Items"
    assert not unrelated.notes.exists()

    marked = Folder.objects.get(owner=owner, is_recovery_folder=True)
    assert marked.id != unrelated.id
    assert marked.name == "Recovered Items (2)"


@pytest.mark.django_db
def test_case_insensitive_collision_handled():
    owner = create_account("restore-case-collision-owner")
    admin = create_admin("restore-case-collision-admin")
    services.create_folder(owner=owner, name="recovered items")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    marked = Folder.objects.get(owner=owner, is_recovery_folder=True)
    assert marked.name == "Recovered Items (2)"


@pytest.mark.django_db
def test_multiple_sequential_suffix_collisions_handled():
    owner = create_account("restore-multi-collision-owner")
    admin = create_admin("restore-multi-collision-admin")
    services.create_folder(owner=owner, name="Recovered Items")
    services.create_folder(owner=owner, name="recovered items (2)")
    services.create_folder(owner=owner, name="Recovered Items (3)")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    marked = Folder.objects.get(owner=owner, is_recovery_folder=True)
    assert marked.name == "Recovered Items (4)"


@pytest.mark.django_db
def test_naming_exhaustion_raises_narrow_exception(monkeypatch):
    owner = create_account("restore-naming-exhaustion-owner")
    monkeypatch.setattr(services, "RECOVERY_FOLDER_NAMING_MAX_ATTEMPTS", 3)
    for attempt in range(1, 4):
        name = services._recovery_folder_name_candidate(attempt=attempt)
        Folder.objects.create(owner=owner, name=name)

    with pytest.raises(services.RecoveryDestinationNamingExhaustedError):
        services._get_or_create_recovery_folder(owner=owner)


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_time_destination_creation_converges_on_one_marked_folder(monkeypatch):
    """Genuine cross-transaction race: two administrators restore two
    different notes for the same owner, both finding no marked recovery
    folder. The second restore is paused right at its first naming
    candidate; the first is allowed to fully commit its "Recovered Items"
    folder before the second resumes, so the second's stale pre-check
    (attempt 1, now occupied by name) retries attempt 2, which then loses on
    the real marker unique constraint and must relookup and reuse the real
    winner -- never crash and never leave two marked folders."""
    owner = create_account("restore-concurrent-destination-owner")
    admin = create_admin("restore-concurrent-destination-admin")
    note_a = services.create_note(owner=owner)
    note_b = services.create_note(owner=owner)
    trash_and_empty(note_a)
    trash_and_empty(note_b)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_candidate = services._recovery_folder_name_candidate

    def pausing_candidate(*, attempt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "first candidate call never signaled proceed"
        return original_candidate(attempt=attempt)

    monkeypatch.setattr(services, "_recovery_folder_name_candidate", pausing_candidate)

    outcome_a = {}

    def run_a():
        try:
            outcome_a["result"] = services.restore_note_for_administrator(
                note_id=note_a.id, actor=admin
            )
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first restore did not reach naming"

    outcome_b = {}

    def run_b():
        try:
            outcome_b["result"] = services.restore_note_for_administrator(
                note_id=note_b.id, actor=admin
            )
        except Exception as exc:  # pragma: no cover
            outcome_b["error"] = exc
        finally:
            connections.close_all()

    thread_b = _run_in_thread(run_b)
    time.sleep(0.3)

    proceed.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert "error" not in outcome_a, outcome_a.get("error")
    assert "error" not in outcome_b, outcome_b.get("error")

    marked_folders = Folder.objects.filter(owner=owner, is_recovery_folder=True)
    assert marked_folders.count() == 1
    assert outcome_a["result"].destination_folder_id == outcome_b["result"].destination_folder_id


@pytest.mark.django_db
def test_destination_concurrently_trashed_is_not_used_after_revalidation():
    """A marked folder that was trashed (and therefore demoted, per the
    unconditional Trash-time demotion) before the administrator's
    destination-resolution query runs is correctly excluded by that query's
    own `is_recovery_folder=True, trashed_at__isnull=True` revalidation
    condition -- the same condition that protects against a genuinely
    concurrent Trash mid-restore, since it is evaluated fresh under lock at
    call time regardless of when the Trash happened."""
    owner = create_account("restore-destination-trashed-owner")
    admin = create_admin("restore-destination-trashed-admin")
    old_destination = Folder.objects.create(
        owner=owner, name="Recovered Items", is_recovery_folder=True
    )
    services.move_folder_to_trash(folder=old_destination)

    note = services.create_note(owner=owner)
    trash_and_empty(note)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert result.destination_folder_id != old_destination.id
    new_destination = Folder.objects.get(pk=result.destination_folder_id)
    assert new_destination.is_recovery_folder is True
    assert new_destination.trashed_at is None


# -- constraint discrimination ------------------------------------------------------


@pytest.mark.django_db
def test_folder_name_constraint_conflict_retries_next_candidate(monkeypatch):
    owner = create_account("restore-name-constraint-owner")
    admin = create_admin("restore-name-constraint-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    original_create = Folder.objects.create
    call_count = {"n": 0}

    def flaky_create(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _fake_integrity_error(services.FOLDER_NAME_CONFLICT_CONSTRAINT)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(Folder.objects, "create", flaky_create)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    destination = Folder.objects.get(pk=result.destination_folder_id)
    assert destination.name == "Recovered Items (2)"
    assert destination.is_recovery_folder is True
    assert call_count["n"] == 2


@pytest.mark.django_db
def test_marker_constraint_conflict_with_no_visible_winner_continues_safely(monkeypatch):
    """Deterministic unit test of the marker-conflict branch's non-primary
    sub-path: the relookup finds nothing (the racing transaction's row is
    not yet visible), so resolution must continue safely to the next naming
    candidate rather than crash or misinterpret the conflict as a name
    collision. The primary sub-path (relookup finds and reuses the real
    winner) is covered by the genuine cross-transaction race test above."""
    owner = create_account("restore-marker-constraint-owner")
    admin = create_admin("restore-marker-constraint-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    original_create = Folder.objects.create
    call_count = {"n": 0}

    def flaky_create(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _fake_integrity_error(services.RECOVERY_MARKER_CONFLICT_CONSTRAINT)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(Folder.objects, "create", flaky_create)

    result = services.restore_note_for_administrator(note_id=note.id, actor=admin)

    destination = Folder.objects.get(pk=result.destination_folder_id)
    assert destination.name == "Recovered Items (2)"
    assert destination.is_recovery_folder is True
    assert Folder.objects.filter(owner=owner, is_recovery_folder=True).count() == 1


@pytest.mark.django_db
def test_unrelated_integrity_error_is_reraised(monkeypatch):
    owner = create_account("restore-unrelated-constraint-owner")
    admin = create_admin("restore-unrelated-constraint-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    def failing_create(*args, **kwargs):
        raise _fake_integrity_error("some_unrelated_constraint")

    monkeypatch.setattr(Folder.objects, "create", failing_create)

    with pytest.raises(IntegrityError):
        services.restore_note_for_administrator(note_id=note.id, actor=admin)

    note.refresh_from_db()
    assert note.trashed_at is not None


# -- audit ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_successful_restore_creates_exactly_one_audit_event():
    owner = create_account("restore-audit-count-owner")
    admin = create_admin("restore-audit-count-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE).count()
        == 1
    )


@pytest.mark.django_db
def test_forced_audit_failure_rolls_back_restore(monkeypatch):
    owner = create_account("restore-audit-rollback-owner")
    admin = create_admin("restore-audit-rollback-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(services.account_services, "record_audit_event", failing_record_audit_event)

    with pytest.raises(RuntimeError):
        services.restore_note_for_administrator(note_id=note.id, actor=admin)

    note.refresh_from_db()
    assert note.trashed_at is not None
    assert note.emptied_at is not None
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE
    ).exists()


@pytest.mark.django_db
def test_no_audit_event_on_failed_restore():
    admin = create_admin("restore-audit-no-event-admin")

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_for_administrator(note_id=999_999, actor=admin)

    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE
    ).exists()


@pytest.mark.django_db
def test_audit_details_contain_required_metadata():
    owner = create_account("restore-audit-details-owner")
    admin = create_admin("restore-audit-details-admin")
    folder = services.create_folder(owner=owner, name="Audit Details Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Audit Details Note")
    services.assign_note_folder(note=note, folder=folder)
    trash_and_empty(note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE)
    assert event.actor_id == admin.id
    assert event.target_user_id == owner.id
    assert event.details["note_id"] == note.id
    assert event.details["title_snapshot"] == "Audit Details Note"
    assert event.details["original_trashed_at"] == original_trashed_at.isoformat()
    assert event.details["emptied_at_cleared"] is True
    assert event.details["destination_category"] == services.DESTINATION_CATEGORY_ORIGINAL_FOLDER
    assert event.details["destination_folder_id"] == folder.id
    assert event.details["fallback_used"] is False


@pytest.mark.django_db
def test_audit_details_exclude_content_fields():
    owner = create_account("restore-audit-content-exclusion-owner")
    admin = create_admin("restore-audit-content-exclusion-admin")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(body_plain_text="SECRET_AUDIT_BODY_XYZ")
    trash_and_empty(note)

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE)
    forbidden_keys = {"body_json", "body_plain_text", "tags", "pinned"}
    assert forbidden_keys.isdisjoint(event.details.keys())
    assert "SECRET_AUDIT_BODY_XYZ" not in str(event.details)


# -- permissions and HTTP behavior -------------------------------------------------


@pytest.mark.django_db
def test_anonymous_denied():
    owner = create_account("restore-http-anon-owner")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    response = Client().post(reverse("notes:admin_note_restore", args=[note.id]))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_inactive_denied():
    owner = create_account("restore-http-inactive-owner")
    inactive_admin = create_admin("restore-http-inactive-admin", is_active=False)
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    response = authenticated_client(inactive_admin).post(
        reverse("notes:admin_note_restore", args=[note.id])
    )

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_active_non_admin_denied():
    owner = create_account("restore-http-non-admin-owner")
    plain_user = create_account("restore-http-non-admin-user")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    response = authenticated_client(plain_user).post(
        reverse("notes:admin_note_restore", args=[note.id])
    )

    assert response.status_code == 403
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_administrator_can_restore_across_owners():
    owner = create_account("restore-http-cross-owner-owner")
    admin = create_admin("restore-http-cross-owner-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    response = authenticated_client(admin).post(reverse("notes:admin_note_restore", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery")
    note.refresh_from_db()
    assert note.trashed_at is None


@pytest.mark.django_db
def test_get_rejected():
    owner = create_account("restore-http-get-owner")
    admin = create_admin("restore-http-get-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    response = authenticated_client(admin).get(reverse("notes:admin_note_restore", args=[note.id]))

    assert response.status_code == 405
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_recovery_table_restore_form_includes_csrf_token():
    owner = create_account("restore-http-csrf-owner")
    admin = create_admin("restore-http-csrf-admin")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Csrf Convention Note")
    trash_and_empty(note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert f'action="/admin/recovery/notes/{note.id}/restore/"' in content
    assert "csrfmiddlewaretoken" in content


@pytest.mark.django_db
def test_stale_double_post_handled_safely():
    owner = create_account("restore-http-double-owner")
    admin = create_admin("restore-http-double-admin")
    note = services.create_note(owner=owner)
    trash_and_empty(note)

    client = authenticated_client(admin)
    first_response = client.post(reverse("notes:admin_note_restore", args=[note.id]))
    second_response = client.post(reverse("notes:admin_note_restore", args=[note.id]))

    assert first_response.status_code == 302
    assert second_response.status_code == 302

    assert (
        AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE).count()
        == 1
    )
