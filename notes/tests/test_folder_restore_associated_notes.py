"""Folder restore with associated notes.

`Note.trashed_via_folder` is set only inside `move_folder_to_trash()`'s own
cascade, on the same notes it newly moves to Trash -- never inferred from
`folder_id`/`trashed_at` after the fact. `restore_folder_from_trash()`
(owner) and `restore_folder_for_administrator()` restore every note
carrying the folder's own marker that is still independently eligible under
the caller's own lifecycle stage, atomically with the folder itself, and
detach the marker from every marked note regardless of outcome. A note is
never restored merely because it currently references the folder.

Several scenarios here require genuine, separate-connection concurrency,
following the same pattern established by `test_trash_restore_race.py` and
`test_admin_folder_restore.py`.
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


def _run_in_thread(target):
    thread = threading.Thread(target=target)
    thread.start()
    return thread


# -- marker lifecycle: set on trash --------------------------------------------


@pytest.mark.django_db
def test_folder_cascade_marks_only_newly_trashed_active_notes():
    owner = create_account("marker-cascade-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_via_folder_id == folder.id


@pytest.mark.django_db
def test_pre_trashed_notes_remain_unmarked_by_a_later_folder_trash():
    owner = create_account("marker-pretrashed-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Pre-trashed")
    services.move_note_to_trash(note=note)

    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_notes_outside_the_folder_are_never_marked():
    owner = create_account("marker-outside-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    unfiled_note = services.create_note(owner=owner)

    services.move_folder_to_trash(folder=folder)
    unfiled_note.refresh_from_db()

    assert unfiled_note.trashed_via_folder_id is None


# -- marker lifecycle: cleared on every path that ends the association --------


@pytest.mark.django_db
def test_marker_cleared_on_individual_owner_restore():
    owner = create_account("marker-clear-owner-restore-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    assert Note.objects.get(pk=note.pk).trashed_via_folder_id == folder.id

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_marker_cleared_on_individual_administrator_restore():
    owner = create_account("marker-clear-admin-restore-owner")
    admin = create_admin("marker-clear-admin-restore-admin")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    assert Note.objects.get(pk=note.pk).trashed_via_folder_id == folder.id

    services.restore_note_for_administrator(note_id=note.id, actor=admin)
    note.refresh_from_db()

    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_marker_cleared_when_restored_together_with_its_folder():
    owner = create_account("marker-clear-together-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_marker_cleared_even_when_left_behind_ineligible_at_folder_restore_time():
    # "Otherwise detached from the original folder-trash association":
    # once the folder is restored, a marked note that was NOT restored
    # alongside it (because it is administrator-only, not owner-visible)
    # is no longer a candidate for any future restore of this same
    # folder -- the association is closed either way.
    owner = create_account("marker-clear-left-behind-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_at is not None
    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_later_independent_trash_does_not_retain_a_stale_association():
    owner = create_account("marker-stale-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    # Touched, so the second trash below moves it to Trash normally rather
    # than discarding it as an untouched placeholder.
    services.rename_note(note=note, title="Stale Association Note")
    services.move_folder_to_trash(folder=folder)
    services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()
    assert note.trashed_via_folder_id is None

    services.move_note_to_trash(note=note)
    note.refresh_from_db()

    assert note.trashed_at is not None
    assert note.trashed_via_folder_id is None


# -- owner restore: eligible marked notes restore, unrelated ones do not ------


@pytest.mark.django_db
def test_owner_restore_leaves_administrator_only_marked_note_trashed():
    owner = create_account("owner-boundary-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    # Beyond the owner-visible window, but still within the
    # administrator-recoverable one.
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    result = services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert result.restored_note_ids == []
    assert note.trashed_at is not None
    assert services.owner_has_hidden_recoverable_items(owner=owner) is True


@pytest.mark.django_db
def test_owner_restore_leaves_purge_only_marked_note_trashed():
    owner = create_account("owner-purge-boundary-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=91))

    result = services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert result.restored_note_ids == []
    assert note.trashed_at is not None
    assert note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_legacy_folder_with_no_marked_notes_restores_folder_only():
    # Simulates a folder trashed before this field existed: the folder
    # carries no association data for any note at all, so restore
    # degrades automatically to exactly today's existing behavior.
    owner = create_account("legacy-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_via_folder=None)

    result = services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert result.folder.trashed_at is None
    assert result.restored_note_ids == []
    assert note.trashed_at is not None


# -- zero / one / multiple associated note counts ------------------------------


@pytest.mark.django_db
def test_count_notes_associated_with_trashed_folder_zero():
    owner = create_account("count-zero-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    assert (
        services.count_notes_associated_with_trashed_folder(folder=folder, for_administrator=False)
        == 0
    )


@pytest.mark.django_db
def test_count_notes_associated_with_trashed_folder_one():
    owner = create_account("count-one-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    assert (
        services.count_notes_associated_with_trashed_folder(folder=folder, for_administrator=False)
        == 1
    )


@pytest.mark.django_db
def test_count_notes_associated_with_trashed_folder_multiple():
    owner = create_account("count-multi-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    for _ in range(3):
        note = services.create_note(owner=owner)
        services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    assert (
        services.count_notes_associated_with_trashed_folder(folder=folder, for_administrator=False)
        == 3
    )


@pytest.mark.django_db
def test_count_excludes_unrelated_notes_referencing_the_folder():
    owner = create_account("count-unrelated-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    pre_trashed = services.create_note(owner=owner)
    services.assign_note_folder(note=pre_trashed, folder=folder)
    services.rename_note(note=pre_trashed, title="Pre-trashed")
    services.move_note_to_trash(note=pre_trashed)
    services.move_folder_to_trash(folder=folder)

    assert (
        services.count_notes_associated_with_trashed_folder(folder=folder, for_administrator=False)
        == 0
    )


@pytest.mark.django_db
def test_owner_trash_page_shows_associated_note_count_on_the_restore_trigger():
    owner = create_account("count-trigger-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert 'data-restore-count="1"' in content


# -- transaction rollback ------------------------------------------------------


@pytest.mark.django_db
def test_owner_restore_rolls_back_folder_and_notes_together_on_failure(monkeypatch):
    owner = create_account("rollback-owner-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    def failing_restore_notes(*, folder, for_administrator):
        raise RuntimeError("simulated failure mid-restore")

    monkeypatch.setattr(services, "_restore_notes_associated_with_folder", failing_restore_notes)

    with pytest.raises(RuntimeError):
        services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    note.refresh_from_db()
    assert folder.trashed_at is not None
    assert note.trashed_at is not None
    assert note.trashed_via_folder_id == folder.id


@pytest.mark.django_db
def test_administrator_restore_rolls_back_folder_and_notes_together_on_failure(monkeypatch):
    owner = create_account("rollback-admin-owner")
    admin = create_admin("rollback-admin-admin")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(services.account_services, "record_audit_event", failing_record_audit_event)

    with pytest.raises(RuntimeError):
        services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    folder.refresh_from_db()
    note.refresh_from_db()
    assert folder.trashed_at is not None
    assert folder.emptied_at is not None
    assert note.trashed_at is not None
    assert note.emptied_at is not None
    assert note.trashed_via_folder_id == folder.id


# -- audit: administrator restore includes restored-note detail ---------------


@pytest.mark.django_db
def test_administrator_audit_event_includes_restored_note_ids_and_count():
    owner = create_account("audit-count-owner")
    admin = create_admin("audit-count-admin")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE)
    assert event.details["restored_note_ids"] == [note.id]
    assert event.details["restored_note_count"] == 1


@pytest.mark.django_db
def test_administrator_audit_event_reports_zero_when_nothing_is_marked():
    owner = create_account("audit-zero-owner")
    admin = create_admin("audit-zero-admin")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    services.restore_folder_for_administrator(folder_id=folder.id, actor=admin)

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE)
    assert event.details["restored_note_ids"] == []
    assert event.details["restored_note_count"] == 0


# -- confirmation dialog trigger presence and data attributes -----------------


@pytest.mark.django_db
def test_restore_confirm_dialog_markup_present_on_trash_and_admin_recovery():
    owner = create_account("dialog-present-owner")
    admin = create_admin("dialog-present-admin")

    trash_content = authenticated_client(owner).get(reverse("notes:trash")).content.decode()
    admin_content = (
        authenticated_client(admin).get(reverse("notes:admin_recovery")).content.decode()
    )

    for content in (trash_content, admin_content):
        assert '<dialog id="restore-confirm-dialog"' in content
        assert "data-restore-confirm-form" in content
        assert "data-restore-confirm-title" in content
        assert "data-restore-confirm-consequence" in content
        assert "data-restore-confirm-cancel" in content
        assert 'data-restore-confirm-field="owner"' in content


@pytest.mark.django_db
def test_owner_folder_restore_trigger_attributes():
    owner = create_account("owner-trigger-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    action = reverse("notes:folder_restore", args=[folder.id])
    assert f'data-restore-action="{action}"' in content
    assert 'data-restore-kind="folder"' in content
    assert 'data-restore-name="Projects"' in content
    assert 'data-restore-count="1"' in content


@pytest.mark.django_db
def test_administrator_folder_restore_trigger_attributes_include_selected_owner():
    owner = create_account("admin-trigger-owner")
    admin = create_admin("admin-trigger-admin")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"), {"owner": owner.id})
    content = response.content.decode()

    action = reverse("notes:admin_folder_restore", args=[folder.id])
    assert f'data-restore-action="{action}"' in content
    assert f'data-restore-owner="{owner.id}"' in content


@pytest.mark.django_db
def test_note_restore_trigger_is_unaffected_no_confirmation_dialog():
    # Individual note restore remains a real, direct POST form with no
    # confirmation step -- only folder rows gained a dialog trigger.
    owner = create_account("note-restore-unaffected-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Unaffected Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    action = reverse("notes:note_restore", args=[note.id])
    assert f'<form method="post" action="{action}">' in content


# -- messaging: actual restored count, not the advisory pre-POST count --------


@pytest.mark.django_db
def test_owner_folder_restore_message_reports_zero_notes():
    owner = create_account("message-zero-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )
    messages = [m.message for m in response.context["messages"]]

    assert any(m == "Folder restored." for m in messages)


@pytest.mark.django_db
def test_owner_folder_restore_message_reports_multiple_notes():
    owner = create_account("message-multi-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    for _ in range(2):
        note = services.create_note(owner=owner)
        services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )
    messages = [m.message for m in response.context["messages"]]

    assert any("2 associated notes were also restored" in m for m in messages)


@pytest.mark.django_db
def test_owner_message_uses_actual_locked_count_not_the_stale_advisory_count():
    # The count shown before POST is advisory; the message must reflect
    # what the locked transaction actually restored, even if that differs
    # from what was true when the page was rendered.
    owner = create_account("message-stale-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    # Touched, so the re-trash below moves it to Trash normally rather
    # than discarding it as an untouched placeholder.
    services.rename_note(note=note, title="Stale Count Note")
    services.move_folder_to_trash(folder=folder)
    # Restore the note individually after the (hypothetical) page render
    # but before the folder restore POST -- the marker is now cleared.
    services.restore_note_from_trash(note=note)
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )
    note.refresh_from_db()
    messages = [m.message for m in response.context["messages"]]

    assert note.trashed_at is not None
    assert any(m == "Folder restored." for m in messages)


# -- concurrency: note restore/edit race against folder restore ---------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_individual_note_restore_and_folder_restore_do_not_double_restore(monkeypatch):
    """Genuine cross-transaction race: an owner restores a marked note
    individually while, at nearly the same moment, the same folder is
    being restored (which would otherwise also restore that note). The
    note-restore side is paused *after* it has already acquired its real
    row lock (patching `note_is_visible_and_self_restorable`, called
    immediately after `select_for_update()` inside `restore_note_from_
    trash()`), so the folder restore's own `select_for_update()` over the
    same row must genuinely block on PostgreSQL until the note restore
    commits -- then, per `SELECT ... FOR UPDATE` semantics, re-evaluate
    its own `trashed_at__isnull=False` filter against the now-committed
    row and correctly exclude it, rather than seeing stale data. Neither
    side may error, and the note must end up restored exactly once."""
    owner = create_account("concurrent-note-folder-owner")
    folder = services.create_folder(owner=owner, name="Concurrent Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "note restore never signaled proceed"
        return original_check(note_arg)

    monkeypatch.setattr(services, "note_is_visible_and_self_restorable", pausing_check)

    outcome_note = {}
    outcome_folder = {}

    def run_note_restore():
        try:
            outcome_note["result"] = services.restore_note_from_trash(note=note)
        except Exception as exc:  # pragma: no cover
            outcome_note["error"] = exc
        finally:
            connections.close_all()

    note_thread = _run_in_thread(run_note_restore)
    assert lock_acquired.wait(timeout=5), "note restore did not reach the locked eligibility check"

    def run_folder_restore():
        try:
            outcome_folder["result"] = services.restore_folder_from_trash(folder=folder)
        except Exception as exc:  # pragma: no cover
            outcome_folder["error"] = exc
        finally:
            connections.close_all()

    folder_thread = _run_in_thread(run_folder_restore)
    time.sleep(0.3)
    proceed.set()
    note_thread.join(timeout=5)
    folder_thread.join(timeout=5)

    assert "error" not in outcome_note, outcome_note.get("error")
    assert "error" not in outcome_folder, outcome_folder.get("error")

    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.trashed_via_folder_id is None
    # The note was restored by the individual-restore side, which locked
    # it first -- the folder restore's own batch, locked afterward, must
    # have re-seen the now-committed active row and excluded it.
    assert outcome_folder["result"].restored_note_ids == []
