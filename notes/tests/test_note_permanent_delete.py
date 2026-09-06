"""Single-Note Permanent Delete from Trash.

`permanently_delete_note_for_owner()` reuses the exact
`emptied_at` transition `empty_trash_for_owner()` already performs in bulk,
scoped to one Note under `select_for_update()`. It introduces no new
lifecycle state: `trashed_at` is preserved, administrator recovery remains
available through the existing horizon, and final-purge timing
(`notes/purge.py`, keyed only on `trashed_at`) is unaffected. Race coverage
follows `test_trash_restore_race.py`'s established real-thread pattern,
since row-level locking only has teeth across genuinely separate database
connections.
"""

import threading
import time
from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import DEFAULT_TAG_COLOR, Note
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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- service layer: successful transition -------------------------------------


@pytest.mark.django_db
def test_permanently_delete_note_sets_emptied_at():
    owner = create_account("perm-delete-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note A")
    services.move_note_to_trash(note=note)

    result = services.permanently_delete_note_for_owner(note=note)

    assert result.emptied_at is not None


@pytest.mark.django_db
def test_permanently_delete_note_preserves_trashed_at():
    owner = create_account("perm-delete-preserve-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 1")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    result = services.permanently_delete_note_for_owner(note=note)

    assert result.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_permanently_delete_note_does_not_change_folder_or_tags():
    owner = create_account("perm-delete-folder-tag-owner")
    folder = services.create_folder(owner=owner, name="Recipes")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 2")
    services.assign_note_folder(note=note, folder=folder)
    tag = services.create_tag_for_owner(owner=owner, name="Important", color=DEFAULT_TAG_COLOR)
    services.assign_tag_to_note(note=note, tag=tag)
    services.move_note_to_trash(note=note)
    note.refresh_from_db()

    services.permanently_delete_note_for_owner(note=note)
    note.refresh_from_db()

    assert note.folder_id == folder.id
    assert list(note.tags.values_list("id", flat=True)) == [tag.id]


@pytest.mark.django_db
def test_permanently_delete_note_does_not_change_title_or_body():
    owner = create_account("perm-delete-content-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Keep This Title")
    original_body = note.body_json
    services.move_note_to_trash(note=note)

    services.permanently_delete_note_for_owner(note=note)
    note.refresh_from_db()

    assert note.title == "Keep This Title"
    assert note.body_json == original_body


@pytest.mark.django_db
def test_permanently_delete_note_returns_synchronized_caller_instance():
    owner = create_account("perm-delete-sync-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 3")
    services.move_note_to_trash(note=note)

    stale_reference = Note.objects.get(pk=note.pk)
    returned = services.permanently_delete_note_for_owner(note=stale_reference)

    assert returned is stale_reference
    assert stale_reference.emptied_at is not None


@pytest.mark.django_db
def test_note_does_not_physically_delete_the_row():
    owner = create_account("perm-delete-no-physical-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 4")
    services.move_note_to_trash(note=note)

    services.permanently_delete_note_for_owner(note=note)

    assert Note.objects.filter(pk=note.pk).exists()


# -- eligibility ----------------------------------------------------------------


@pytest.mark.django_db
def test_active_note_rejected():
    owner = create_account("perm-delete-active-owner")
    note = services.create_note(owner=owner)

    with pytest.raises(services.NoteNotEligibleForManualDeleteError):
        services.permanently_delete_note_for_owner(note=note)


@pytest.mark.django_db
def test_already_emptied_note_rejected():
    owner = create_account("perm-delete-already-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 5")
    services.move_note_to_trash(note=note)
    services.empty_trash_for_owner(owner=owner)
    note.refresh_from_db()
    assert note.emptied_at is not None

    with pytest.raises(services.NoteNotEligibleForManualDeleteError):
        services.permanently_delete_note_for_owner(note=note)


@pytest.mark.django_db
def test_expired_visible_window_note_rejected():
    owner = create_account("perm-delete-expired-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 6")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_VISIBLE_MAX_AGE - timedelta(days=1)
    )
    note.refresh_from_db()

    with pytest.raises(services.NoteNotEligibleForManualDeleteError):
        services.permanently_delete_note_for_owner(note=note)


@pytest.mark.django_db
def test_note_trashed_via_folder_is_eligible_like_any_other_trashed_note():
    owner = create_account("perm-delete-via-folder-owner")
    folder = services.create_folder(owner=owner, name="Trip Notes")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()
    assert note.trashed_via_folder_id == folder.id

    result = services.permanently_delete_note_for_owner(note=note)

    assert result.emptied_at is not None


@pytest.mark.django_db
def test_note_excluded_from_grouped_restore_candidates_once_emptied():
    owner = create_account("perm-delete-grouped-restore-owner")
    folder = services.create_folder(owner=owner, name="Trip Notes 2")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    before = services.count_notes_associated_with_trashed_folder(
        folder=folder, for_administrator=False
    )
    assert before == 1

    services.permanently_delete_note_for_owner(note=note)

    after = services.count_notes_associated_with_trashed_folder(
        folder=folder, for_administrator=False
    )
    assert after == 0


# -- administrator recovery -----------------------------------------------------


@pytest.mark.django_db
def test_permanently_deleted_note_appears_in_administrator_recoverable_listing():
    owner = create_account("perm-delete-admin-visible-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Recoverable Note")
    services.move_note_to_trash(note=note)

    services.permanently_delete_note_for_owner(note=note)

    recoverable = services.list_administrator_recoverable_notes()
    assert any(row["id"] == note.id for row in recoverable)


# -- view: success and messaging -------------------------------------------------


@pytest.mark.django_db
def test_view_success_redirects_to_trash_and_shows_message():
    owner = create_account("perm-delete-view-success-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 7")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_permanent_delete", args=[note.id]), follow=True
    )

    assert response.redirect_chain[0][0] == reverse("notes:trash")
    content = response.content.decode()
    assert "Note permanently deleted." in content
    note.refresh_from_db()
    assert note.emptied_at is not None


@pytest.mark.django_db
def test_view_note_disappears_from_owner_visible_trash_listing():
    owner = create_account("perm-delete-view-disappear-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Gone Soon")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))
    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "Gone Soon" not in response.content.decode()


@pytest.mark.django_db
def test_view_rejects_active_note_with_message_and_no_state_change():
    owner = create_account("perm-delete-view-active-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_permanent_delete", args=[note.id]), follow=True
    )

    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.emptied_at is None
    assert response.redirect_chain[0][0] == reverse("notes:trash")


@pytest.mark.django_db
def test_view_rejects_already_emptied_note_with_clean_message():
    owner = create_account("perm-delete-view-already-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 8")
    services.move_note_to_trash(note=note)
    services.empty_trash_for_owner(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_permanent_delete", args=[note.id]), follow=True
    )

    content = response.content.decode()
    assert "This note is no longer available for self-service permanent delete." in content


@pytest.mark.django_db
def test_view_cross_owner_note_returns_404():
    owner = create_account("perm-delete-cross-owner-a")
    other = create_account("perm-delete-cross-owner-b")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 9")
    services.move_note_to_trash(note=note)

    response = authenticated_client(other).post(
        reverse("notes:note_permanent_delete", args=[note.id])
    )

    assert response.status_code == 404
    note.refresh_from_db()
    assert note.emptied_at is None


@pytest.mark.django_db
def test_view_missing_note_returns_404():
    owner = create_account("perm-delete-missing-owner")

    response = authenticated_client(owner).post(
        reverse("notes:note_permanent_delete", args=[999999])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_view_unauthenticated_is_blocked():
    owner = create_account("perm-delete-unauth-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 10")
    services.move_note_to_trash(note=note)

    response = Client().post(reverse("notes:note_permanent_delete", args=[note.id]))

    note.refresh_from_db()
    assert response.status_code == 302
    assert note.emptied_at is None


@pytest.mark.django_db
def test_view_get_is_not_allowed():
    owner = create_account("perm-delete-get-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 11")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(
        reverse("notes:note_permanent_delete", args=[note.id])
    )

    assert response.status_code == 405
    note.refresh_from_db()
    assert note.emptied_at is None


@pytest.mark.django_db
def test_view_repeated_post_second_call_rejected_no_second_transition():
    owner = create_account("perm-delete-repeat-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 12")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))
    note.refresh_from_db()
    first_emptied_at = note.emptied_at
    assert first_emptied_at is not None

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))
    note.refresh_from_db()

    assert note.emptied_at == first_emptied_at


# -- audit event ------------------------------------------------------------------


@pytest.mark.django_db
def test_view_success_records_audit_event_exactly_once_with_actor_and_details():
    owner = create_account("perm-delete-audit-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Audited Note")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))

    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_NOTE_EMPTIED)
    assert events.count() == 1
    event = events.get()
    assert event.actor_id == owner.id
    assert event.details["note_id"] == note.id
    assert event.details["title"] == "Audited Note"


@pytest.mark.django_db
def test_repeated_post_does_not_record_a_second_audit_event():
    owner = create_account("perm-delete-audit-repeat-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 13")
    services.move_note_to_trash(note=note)

    client = authenticated_client(owner)
    client.post(reverse("notes:note_permanent_delete", args=[note.id]))
    client.post(reverse("notes:note_permanent_delete", args=[note.id]))

    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_NOTE_EMPTIED).count() == 1


@pytest.mark.django_db
def test_rejected_active_note_records_no_audit_event():
    owner = create_account("perm-delete-audit-reject-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_NOTE_EMPTIED).exists()


# -- CSRF -------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_rejects_request_without_csrf_token():
    owner = create_account("perm-delete-csrf-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 14")
    services.move_note_to_trash(note=note)

    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    session = client.session
    from accounts import services as account_services

    session[account_services.SESSION_GENERATION_KEY] = owner.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()

    response = client.post(reverse("notes:note_permanent_delete", args=[note.id]))

    assert response.status_code == 403
    note.refresh_from_db()
    assert note.emptied_at is None


# -- concurrency / races -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_restore_wins_race_against_concurrent_permanent_delete(monkeypatch):
    """Mirrors `test_trash_restore_race.py`'s established real-thread
    pattern. Restore is paused immediately after it acquires its row lock;
    a concurrent, genuinely separate `permanently_delete_note_for_owner()`
    call must block on the same row until restore commits, then correctly
    reject since the note is no longer visible/self-restorable (it is now
    active)."""
    owner = create_account("perm-delete-restore-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Restore Race Note")
    services.move_note_to_trash(note=note)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "permanent-delete thread never signaled proceed"
        return original_check(note_arg)

    monkeypatch.setattr(services, "note_is_visible_and_self_restorable", pausing_check)

    restore_outcome = {}

    def run_restore():
        try:
            services.restore_note_from_trash(note=note)
            restore_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            restore_outcome["error"] = exc
        finally:
            connections.close_all()

    restore_thread = _run_in_thread(run_restore)

    assert lock_acquired.wait(timeout=5), "restore did not reach the locked eligibility check"

    delete_outcome = {}

    def run_permanent_delete():
        try:
            services.permanently_delete_note_for_owner(note=note)
            delete_outcome["ok"] = True
        except services.NoteNotEligibleForManualDeleteError:
            delete_outcome["rejected"] = True
        except Exception as exc:  # pragma: no cover
            delete_outcome["error"] = exc
        finally:
            connections.close_all()

    delete_thread = _run_in_thread(run_permanent_delete)

    time.sleep(0.3)
    proceed.set()
    restore_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert restore_outcome.get("ok") is True
    assert "error" not in delete_outcome, delete_outcome.get("error")
    assert delete_outcome.get("rejected") is True

    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.emptied_at is None


@pytest.mark.django_db(transaction=True)
def test_permanent_delete_wins_race_against_concurrent_empty_trash(monkeypatch):
    """The reverse pairing: `permanently_delete_note_for_owner()` acquires
    the row lock first; a concurrent `empty_trash_for_owner()` bulk update
    must block on the locked row, then simply skip it once it no longer
    matches the visible-trash filter (`emptied_at` already set) -- no
    double transition, no user-visible error, and a valid administrator-
    recoverable final state either way."""
    owner = create_account("perm-delete-empty-trash-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Empty Trash Race Note")
    services.move_note_to_trash(note=note)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "empty trash thread never signaled proceed"
        return original_check(note_arg)

    monkeypatch.setattr(services, "note_is_visible_and_self_restorable", pausing_check)

    delete_outcome = {}

    def run_permanent_delete():
        try:
            services.permanently_delete_note_for_owner(note=note)
            delete_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            delete_outcome["error"] = exc
        finally:
            connections.close_all()

    delete_thread = _run_in_thread(run_permanent_delete)

    assert lock_acquired.wait(timeout=5), (
        "permanent delete did not reach the locked eligibility check"
    )

    empty_trash_outcome = {}

    def run_empty_trash():
        try:
            empty_trash_outcome["counts"] = services.empty_trash_for_owner(owner=owner)
        except Exception as exc:  # pragma: no cover
            empty_trash_outcome["error"] = exc
        finally:
            connections.close_all()

    empty_trash_thread = _run_in_thread(run_empty_trash)

    time.sleep(0.3)
    proceed.set()
    delete_thread.join(timeout=5)
    empty_trash_thread.join(timeout=5)

    assert "error" not in delete_outcome, delete_outcome.get("error")
    assert delete_outcome.get("ok") is True
    assert "error" not in empty_trash_outcome, empty_trash_outcome.get("error")

    note.refresh_from_db()
    assert note.trashed_at is not None
    assert note.emptied_at is not None


@pytest.mark.django_db
def test_manually_emptied_note_not_purged_before_the_normal_90_day_horizon():
    """`emptied_at` must never accelerate final-purge eligibility -- purge
    eligibility is keyed only on `trashed_at`. A manually-emptied Note
    trashed just inside the 90-day recoverable window must survive a live
    purge cycle exactly like a naturally-emptied one would."""
    owner = create_account("perm-delete-purge-timing-not-yet-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 15")
    services.move_note_to_trash(note=note)
    services.permanently_delete_note_for_owner(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_RECOVERABLE_MAX_AGE + timedelta(days=1)
    )

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert result.notes_purged == 0
    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_manually_emptied_note_is_purged_at_the_normal_90_day_horizon():
    """The other half of the same guarantee: once genuinely past the
    normal 90-day horizon (measured from `trashed_at`, unaffected by when
    `emptied_at` was set), a manually-emptied Note purges exactly like any
    other -- this action does not create a permanently-un-purgeable Note
    either."""
    owner = create_account("perm-delete-purge-timing-eligible-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Note 16")
    services.move_note_to_trash(note=note)
    services.permanently_delete_note_for_owner(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_RECOVERABLE_MAX_AGE - timedelta(days=1)
    )

    result = run_purge_cycle(dry_run=False, note_batch_size=200, folder_batch_size=200)

    assert result.notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()
