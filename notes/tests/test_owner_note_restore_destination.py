"""Owner note-restore destination.

`restore_note_from_trash()` resolves a safe destination exactly like
`restore_note_for_administrator()` (reusing the same internal
`_resolve_recovery_destination()`/`_get_or_create_recovery_folder()`
helpers, unchanged): the original folder is kept only if it is still
active and owner-correct; otherwise the note falls back to the owner's
marker-based Recovered Items folder. An originally unfiled note (no
`folder_id` at all) never enters this resolution at all and simply stays
unfiled. This rules out the
"active note referencing a trashed folder" state -- see the tests
in `test_folder_trash.py` for the direct coverage of that
invariant.

Several scenarios here require genuine, separate-connection concurrency to
exercise real PostgreSQL row-locking and unique-constraint behavior,
following the same pattern established by `test_trash_restore_race.py` and
reused for administrator note restore in `test_admin_note_restore.py`.
"""

import threading
import time

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


# -- destination behavior -----------------------------------------------------------


@pytest.mark.django_db
def test_active_original_folder_reused():
    owner = create_account("owner-restore-active-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_trashed_original_folder_falls_back_to_recovered_items():
    owner = create_account("owner-restore-fallback-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.folder_id != folder.id
    destination = Folder.objects.get(pk=note.folder_id)
    assert destination.is_recovery_folder is True
    assert destination.name == "Recovered Items"


@pytest.mark.django_db
def test_unfiled_note_remains_unfiled():
    owner = create_account("owner-restore-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.folder_id is None
    assert not Folder.objects.filter(owner=owner, is_recovery_folder=True).exists()


@pytest.mark.django_db
def test_renamed_marked_destination_reused():
    owner = create_account("owner-restore-renamed-owner")
    existing = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.rename_folder(folder=existing, name="My Rescued Notes")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.folder_id == existing.id
    destination = Folder.objects.get(pk=existing.id)
    assert destination.name == "My Rescued Notes"


@pytest.mark.django_db
def test_unrelated_unmarked_recovered_items_folder_untouched():
    owner = create_account("owner-restore-unrelated-owner")
    unrelated = services.create_folder(owner=owner, name="Recovered Items")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)

    unrelated.refresh_from_db()
    assert unrelated.is_recovery_folder is False
    assert unrelated.name == "Recovered Items"
    assert not unrelated.notes.exists()

    marked = Folder.objects.get(owner=owner, is_recovery_folder=True)
    assert marked.id != unrelated.id
    assert marked.name == "Recovered Items (2)"


@pytest.mark.django_db
def test_collision_safe_destination_created_when_needed():
    owner = create_account("owner-restore-collision-owner")
    services.create_folder(owner=owner, name="recovered items")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    destination = Folder.objects.get(pk=note.folder_id)
    assert destination.name == "Recovered Items (2)"
    assert destination.is_recovery_folder is True


@pytest.mark.django_db
def test_no_active_restored_note_references_a_trashed_folder():
    owner = create_account("owner-restore-invariant-owner")
    folder_a = services.create_folder(owner=owner, name="Alpha")
    folder_b = services.create_folder(owner=owner, name="Beta")
    note_a = services.create_note(owner=owner)
    note_b = services.create_note(owner=owner)
    services.assign_note_folder(note=note_a, folder=folder_a)
    services.assign_note_folder(note=note_b, folder=folder_b)
    services.move_folder_to_trash(folder=folder_a)
    services.move_folder_to_trash(folder=folder_b)

    services.restore_note_from_trash(note=note_a)
    services.restore_note_from_trash(note=note_b)

    for active_note in Note.objects.filter(owner=owner, trashed_at__isnull=True):
        if active_note.folder_id is not None:
            assert active_note.folder.trashed_at is None


def _flash_messages_html(content: str) -> str:
    """The rendered Django-messages region only (`<ul class="messages">`),
    not the whole page -- the in-app Help panel (embedded on every page)
    legitimately discusses recovery-folder naming in its own copy, so a
    whole-page substring check for message wording is not reliable."""
    start = content.index('<ul class="messages"')
    end = content.index("</ul>", start) + len("</ul>")
    return content[start:end]


# -- messaging ------------------------------------------------------------------------


@pytest.mark.django_db
def test_generic_message_for_active_original_folder():
    owner = create_account("owner-restore-message-active-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Note 3")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    flash = _flash_messages_html(response.content.decode())

    assert "Note restored." in flash
    assert "Recovered Items" not in flash


@pytest.mark.django_db
def test_generic_message_for_originally_unfiled_note():
    owner = create_account("owner-restore-message-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 4")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    flash = _flash_messages_html(response.content.decode())

    assert "Note restored." in flash
    assert "Recovered Items" not in flash


@pytest.mark.django_db
def test_explicit_message_for_fallback_destination():
    owner = create_account("owner-restore-message-fallback-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    content = response.content.decode()

    assert "Note restored to &quot;Recovered Items&quot; because its original" in content
    assert "still in Trash" in content


@pytest.mark.django_db
def test_message_shows_renamed_destination_name():
    owner = create_account("owner-restore-message-renamed-owner")
    existing = Folder.objects.create(owner=owner, name="Recovered Items", is_recovery_folder=True)
    services.rename_folder(folder=existing, name="Rescued Notes")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    content = response.content.decode()

    assert "Rescued Notes" in content
    assert "is_recovery_folder" not in content
    assert "marked folder" not in content.lower()


@pytest.mark.django_db
def test_message_does_not_imply_original_folder_restored():
    owner = create_account("owner-restore-message-no-implication-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    content = response.content.decode()

    assert "still in Trash" in content
    folder.refresh_from_db()
    assert folder.trashed_at is not None


# -- eligibility and permissions -------------------------------------------------------


@pytest.mark.django_db
def test_emptied_note_cannot_owner_restore():
    owner = create_account("owner-restore-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 5")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_from_trash(note=note)


@pytest.mark.django_db
def test_beyond_owner_window_note_cannot_owner_restore():
    from datetime import timedelta

    owner = create_account("owner-restore-too-old-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 6")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    with pytest.raises(services.TrashItemNotRestorableError):
        services.restore_note_from_trash(note=note)


@pytest.mark.django_db
def test_cross_owner_restore_denied():
    owner = create_account("owner-restore-cross-owner-owner")
    other = create_account("owner-restore-cross-owner-other")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 7")
    services.move_note_to_trash(note=note)

    response = authenticated_client(other).post(reverse("notes:note_restore", args=[note.id]))

    assert response.status_code == 404
    note.refresh_from_db()
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_missing_note_restore_handled_safely():
    owner = create_account("owner-restore-missing-owner")

    response = authenticated_client(owner).post(reverse("notes:note_restore", args=[999_999]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_second_restore_is_safe():
    owner = create_account("owner-restore-double-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 8")
    services.move_note_to_trash(note=note)

    client = authenticated_client(owner)
    first = client.post(reverse("notes:note_restore", args=[note.id]))
    second = client.post(reverse("notes:note_restore", args=[note.id]))

    assert first.status_code == 302
    assert second.status_code == 302
    note.refresh_from_db()
    assert note.trashed_at is None


# -- concurrency ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_original_folder_trashed_concurrently(monkeypatch):
    """Genuine cross-transaction race: the owner's restore is paused right
    after locking the note but before resolving the destination; a
    concurrent folder-trash call commits in the meantime; the restore must
    then correctly see the folder as trashed and fall back to Recovered
    Items rather than assigning the note to a folder that is trashed by
    the time the restore actually commits."""
    owner = create_account("owner-restore-folder-race-owner")
    folder = services.create_folder(owner=owner, name="Racing Folder")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Note 9")
    services.move_note_to_trash(note=note)

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original_check = services.note_is_visible_and_self_restorable

    def pausing_check(note_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "folder-trash thread never signaled proceed"
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

    trash_outcome = {}

    def run_trash():
        try:
            fresh_folder = Folder.objects.get(pk=folder.pk)
            services.move_folder_to_trash(folder=fresh_folder)
            trash_outcome["ok"] = True
        except Exception as exc:  # pragma: no cover
            trash_outcome["error"] = exc
        finally:
            connections.close_all()

    trash_thread = _run_in_thread(run_trash)
    time.sleep(0.3)

    proceed.set()
    restore_thread.join(timeout=5)
    trash_thread.join(timeout=5)

    assert "error" not in restore_outcome, restore_outcome.get("error")
    assert "error" not in trash_outcome, trash_outcome.get("error")

    note.refresh_from_db()
    assert note.trashed_at is None
    destination = Folder.objects.get(pk=note.folder_id)
    assert destination.is_recovery_folder is True


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_marked_folder_creation(monkeypatch):
    """Genuine cross-transaction race: two notes for the same owner, both
    with trashed original folders, are restored at nearly the same time;
    neither has an existing marked folder to find. Both must converge on
    exactly one marked Recovered Items folder."""
    owner = create_account("owner-restore-concurrent-creation-owner")
    folder_a = services.create_folder(owner=owner, name="Alpha")
    folder_b = services.create_folder(owner=owner, name="Beta")
    note_a = services.create_note(owner=owner)
    note_b = services.create_note(owner=owner)
    services.assign_note_folder(note=note_a, folder=folder_a)
    services.assign_note_folder(note=note_b, folder=folder_b)
    services.move_folder_to_trash(folder=folder_a)
    services.move_folder_to_trash(folder=folder_b)

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
            services.restore_note_from_trash(note=note_a)
            outcome_a["ok"] = True
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = _run_in_thread(run_a)
    assert lock_acquired.wait(timeout=5), "first restore did not reach naming"

    outcome_b = {}

    def run_b():
        try:
            services.restore_note_from_trash(note=note_b)
            outcome_b["ok"] = True
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
    note_a.refresh_from_db()
    note_b.refresh_from_db()
    assert note_a.folder_id == note_b.folder_id == marked_folders.first().id


@pytest.mark.django_db
def test_marked_destination_trashed_before_restore_falls_back_to_new_one():
    """The destination-resolution query re-checks `trashed_at__isnull=True`
    fresh under lock at call time, so a marked folder that was trashed
    (and thereby demoted) before the restore call runs is
    correctly excluded and a new one is created -- the same revalidation
    guarantee already proven for administrator note restore."""
    owner = create_account("owner-restore-destination-trashed-owner")
    old_destination = Folder.objects.create(
        owner=owner, name="Recovered Items", is_recovery_folder=True
    )
    services.move_folder_to_trash(folder=old_destination)

    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.folder_id != old_destination.id
    new_destination = Folder.objects.get(pk=note.folder_id)
    assert new_destination.is_recovery_folder is True
    assert new_destination.trashed_at is None


# -- regression -------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_new_audit_event_for_owner_note_restore():
    owner = create_account("owner-restore-no-audit-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)

    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE
    ).exists()
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_sibling_trashed_note_unaffected_by_another_notes_restore():
    owner = create_account("owner-restore-sibling-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note_a = services.create_note(owner=owner)
    note_b = services.create_note(owner=owner)
    services.assign_note_folder(note=note_a, folder=folder)
    services.assign_note_folder(note=note_b, folder=folder)
    services.move_folder_to_trash(folder=folder)
    before_trashed_at = Note.objects.get(pk=note_b.pk).trashed_at

    services.restore_note_from_trash(note=note_a)

    note_b.refresh_from_db()
    assert note_b.trashed_at == before_trashed_at
    assert note_b.folder_id == folder.id
