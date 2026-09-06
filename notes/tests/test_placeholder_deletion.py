"""untouched placeholder deletion.

`move_note_to_trash()` hard-deletes a note instead of moving it to Trash,
but only when it is still exactly as `create_note()` left it: the
generated title, the canonical empty body, no tags, not pinned, and not
already trashed. This is a synchronous, delete-time check -- no
scheduler, no grace period. The eligibility test runs against a freshly
locked, reloaded row (`select_for_update()`), and a required audit event
is written in the same transaction as the delete, so a failed audit
write rolls the delete back too.
"""

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


# -- eligibility: move_note_to_trash() service behavior -----------------------


@pytest.mark.django_db
def test_fully_eligible_placeholder_is_hard_deleted():
    owner = create_account("placeholder-eligible-owner")
    note = services.create_note(owner=owner)
    note_id = note.id

    result = services.move_note_to_trash(note=note)

    assert result is None
    assert not Note.objects.filter(pk=note_id).exists()


@pytest.mark.django_db
def test_changed_title_disqualifies():
    owner = create_account("placeholder-title-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="A Real Title")

    result = services.move_note_to_trash(note=note)

    assert result is not None
    assert result.trashed_at is not None
    assert Note.objects.filter(pk=note.pk, trashed_at__isnull=False).exists()


@pytest.mark.django_db
def test_changed_body_disqualifies():
    owner = create_account("placeholder-body-owner")
    note = services.create_note(owner=owner)
    services.save_note(
        note=note,
        title="",
        body_json={
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Real content"}]}
            ],
        },
        version=note.version,
    )
    note.refresh_from_db()

    result = services.move_note_to_trash(note=note)

    assert result is not None
    assert result.trashed_at is not None


@pytest.mark.django_db
def test_tag_disqualifies():
    owner = create_account("placeholder-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="urgent", color="rose")
    services.assign_tag_to_note(note=note, tag=tag)

    result = services.move_note_to_trash(note=note)

    assert result is not None
    assert result.trashed_at is not None


@pytest.mark.django_db
def test_pin_disqualifies():
    owner = create_account("placeholder-pin-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)

    result = services.move_note_to_trash(note=note)

    assert result is not None
    assert result.trashed_at is not None


@pytest.mark.django_db
def test_already_trashed_note_is_a_no_op():
    owner = create_account("placeholder-already-trashed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Already Trashed")
    first = services.move_note_to_trash(note=note)
    first_trashed_at = first.trashed_at

    second = services.move_note_to_trash(note=note)

    assert second is not None
    assert second.trashed_at == first_trashed_at


@pytest.mark.django_db
def test_folder_assignment_at_creation_remains_eligible():
    owner = create_account("placeholder-folder-owner")
    folder = services.create_folder(owner=owner, name="Some Folder")
    note = services.create_note(owner=owner, folder=folder)
    note_id = note.id

    result = services.move_note_to_trash(note=note)

    assert result is None
    assert not Note.objects.filter(pk=note_id).exists()


@pytest.mark.django_db
def test_normal_note_still_moves_to_trash():
    owner = create_account("placeholder-normal-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Ordinary Note")

    result = services.move_note_to_trash(note=note)

    assert result is not None
    assert result.trashed_at is not None
    assert Note.objects.filter(pk=note.pk).exists()


# -- audit behavior -------------------------------------------------------------


@pytest.mark.django_db
def test_required_audit_event_is_written_for_a_discarded_placeholder():
    owner = create_account("placeholder-audit-owner")
    note = services.create_note(owner=owner)
    note_id = note.id

    services.move_note_to_trash(note=note)

    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_NOTE_PLACEHOLDER_DISCARDED)
    assert events.count() == 1
    event = events.get()
    assert event.actor_id == owner.id
    assert event.details["note_id"] == note_id


@pytest.mark.django_db
def test_no_audit_event_written_for_ordinary_trash():
    owner = create_account("placeholder-audit-none-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Not A Placeholder")

    services.move_note_to_trash(note=note)

    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_NOTE_PLACEHOLDER_DISCARDED
    ).exists()


@pytest.mark.django_db
def test_audit_failure_rolls_back_the_deletion(monkeypatch):
    owner = create_account("placeholder-audit-failure-owner")
    note = services.create_note(owner=owner)
    note_id = note.id

    def failing_record_audit_event(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(account_services, "record_audit_event", failing_record_audit_event)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        services.move_note_to_trash(note=note)

    # The whole transaction must have rolled back -- the note still exists,
    # untouched, never trashed either.
    surviving = Note.objects.get(pk=note_id)
    assert surviving.trashed_at is None


# -- concurrency: locked-row recheck protects against a race -------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_edit_holding_the_row_lock_is_seen_by_a_blocked_delete():
    """A second thread's `move_note_to_trash()` call must block on the row
    lock while a concurrent transaction holds it, then -- once unblocked --
    must evaluate eligibility against the *post-edit* row, not a stale
    snapshot read before the lock was acquired. This proves the
    eligibility check genuinely happens after `select_for_update()`, using
    real PostgreSQL row locking, not a mocked stand-in."""
    import threading

    from django.db import connections
    from django.db import transaction as db_transaction

    owner = create_account("placeholder-race-owner")
    note = services.create_note(owner=owner)
    note_id = note.id

    lock_acquired = threading.Event()
    proceed_to_commit = threading.Event()

    def hold_lock_then_rename():
        try:
            with db_transaction.atomic():
                Note.objects.select_for_update().get(pk=note_id)
                lock_acquired.set()
                assert proceed_to_commit.wait(timeout=5), "main thread never signaled"
                # Rename while still holding the lock -- the concurrent
                # delete attempt must not see this until the lock releases.
                Note.objects.filter(pk=note_id).update(title="Renamed While Locked")
        finally:
            connections.close_all()

    holder_thread = threading.Thread(target=hold_lock_then_rename)
    holder_thread.start()
    assert lock_acquired.wait(timeout=5), "holder thread never acquired the lock"

    delete_outcome = {}

    def attempt_delete():
        try:
            delete_outcome["result"] = services.move_note_to_trash(
                note=Note.objects.get(pk=note_id)
            )
        finally:
            connections.close_all()

    delete_thread = threading.Thread(target=attempt_delete)
    delete_thread.start()

    # Give the delete thread a real chance to reach (and block on) the row
    # lock before releasing the holder -- if it did NOT block, it would
    # already have finished (and possibly deleted the row) by now.
    import time as _time

    _time.sleep(0.3)
    assert "result" not in delete_outcome, "delete did not block on the held row lock"

    proceed_to_commit.set()
    holder_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert "result" in delete_outcome
    # The delete thread must have seen the rename made while it was
    # blocked -- so it correctly preserved the note instead of discarding
    # it as a placeholder.
    assert delete_outcome["result"] is not None
    assert delete_outcome["result"].trashed_at is not None
    assert Note.objects.filter(pk=note_id).exists()


# -- view call sites: both report the correct outcome --------------------------


@pytest.mark.django_db
def test_note_delete_view_reports_discard_for_a_placeholder():
    owner = create_account("placeholder-view-delete-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), follow=True
    )

    assert not Note.objects.filter(pk=note.id).exists()
    messages = [m.message for m in response.context["messages"]]
    assert any("discarded instead of moved to Trash" in m for m in messages)


@pytest.mark.django_db
def test_note_delete_view_reports_trash_for_a_normal_note():
    owner = create_account("placeholder-view-delete-normal-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Keep Me")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), follow=True
    )

    stored = Note.objects.get(pk=note.id)
    assert stored.trashed_at is not None
    messages = [m.message for m in response.context["messages"]]
    assert any("moved to Trash" in m for m in messages)


@pytest.mark.django_db
def test_note_delete_all_notes_view_reports_discard_for_a_placeholder():
    owner = create_account("placeholder-view-all-notes-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete_all_notes", args=[note.id]), follow=True
    )

    assert not Note.objects.filter(pk=note.id).exists()
    messages = [m.message for m in response.context["messages"]]
    assert any("discarded instead of moved to Trash" in m for m in messages)


@pytest.mark.django_db
def test_note_delete_all_notes_view_reports_trash_for_a_normal_note():
    owner = create_account("placeholder-view-all-notes-normal-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Keep Me Too")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete_all_notes", args=[note.id]), follow=True
    )

    stored = Note.objects.get(pk=note.id)
    assert stored.trashed_at is not None
    messages = [m.message for m in response.context["messages"]]
    assert any("moved to Trash" in m for m in messages)


# -- eligibility helper: direct unit coverage -----------------------------------


@pytest.mark.django_db
def test_is_untouched_placeholder_true_for_fresh_note():
    owner = create_account("placeholder-helper-true-owner")
    note = services.create_note(owner=owner)
    assert services._is_untouched_placeholder(note) is True


@pytest.mark.django_db
def test_is_untouched_placeholder_false_after_title_change():
    owner = create_account("placeholder-helper-false-owner")
    note = services.create_note(owner=owner)
    note.title = "Something else"
    assert services._is_untouched_placeholder(note) is False


@pytest.mark.django_db
def test_is_untouched_placeholder_false_after_body_change():
    owner = create_account("placeholder-helper-body-owner")
    note = services.create_note(owner=owner)
    note.body_json = {"type": "doc", "content": []}
    assert services._is_untouched_placeholder(note) is False


# -- untouched note survives navigate-away ----------
#
# An untouched note created via the active-note overflow's New note route
# survives after "navigate away" -- there is no automatic cleanup
# mechanism anywhere in this codebase for *any*
# creation route: `move_note_to_trash()` is called only from the explicit
# `note_delete`/`note_delete_all_notes` views (grep-confirmed, `notes/
# views.py`), never from a `beforeunload` handler, autosave path, or any
# other navigation-triggered code. This is not a regression --
# it is the established, universal behavior of the whole app.
# The tests below prove the one thing that actually is this module's
# responsibility: the existing, delete-triggered cleanup mechanism must
# behave identically for a note created via `note_create_sibling` as for
# every other creation route.


@pytest.mark.django_db
def test_untouched_note_created_via_active_note_overflow_is_discarded_on_delete():
    owner = create_account("placeholder-overflow-owner")
    current_note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": ""}
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])

    delete_response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[new_id]), follow=True
    )

    assert not Note.objects.filter(pk=new_id).exists()
    messages = [m.message for m in delete_response.context["messages"]]
    assert any("discarded" in m.lower() for m in messages)


@pytest.mark.django_db
def test_renamed_note_created_via_active_note_overflow_is_not_discarded():
    owner = create_account("placeholder-overflow-renamed-owner")
    current_note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": ""}
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])
    new_note = services.note_for_owner_or_404(note_id=new_id, owner=owner)
    services.rename_note(note=new_note, title="A Real Title")

    authenticated_client(owner).post(reverse("notes:note_delete", args=[new_id]))

    stored = Note.objects.get(pk=new_id)
    assert stored.trashed_at is not None


@pytest.mark.django_db
def test_body_edited_note_created_via_active_note_overflow_is_not_discarded():
    owner = create_account("placeholder-overflow-body-owner")
    current_note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": ""}
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])
    new_note = services.note_for_owner_or_404(note_id=new_id, owner=owner)
    services.save_note(
        note=new_note,
        title=new_note.title,
        body_json={
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Real content"}]}
            ],
        },
        version=new_note.version,
    )

    authenticated_client(owner).post(reverse("notes:note_delete", args=[new_id]))

    stored = Note.objects.get(pk=new_id)
    assert stored.trashed_at is not None


@pytest.mark.django_db
def test_deleting_one_overflow_created_note_does_not_affect_a_pre_existing_empty_note():
    owner = create_account("placeholder-overflow-scoped-owner")
    pre_existing = services.create_note(owner=owner)
    current_note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": ""}
    )
    new_id = int(response.url.split("/notes/")[1].split("/")[0])

    authenticated_client(owner).post(reverse("notes:note_delete", args=[new_id]))

    assert not Note.objects.filter(pk=new_id).exists()
    # The unrelated pre-existing empty note is completely untouched, even
    # though it is itself still an eligible placeholder -- cleanup only
    # ever evaluates the exact note being deleted, never sweeps others.
    assert Note.objects.filter(pk=pre_existing.id).exists()


@pytest.mark.django_db
def test_overflow_created_note_discard_behaves_identically_foldered_and_unfiled():
    owner = create_account("placeholder-overflow-parity-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    current_note = services.create_note(owner=owner, folder=folder)

    foldered_response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[current_note.id]), {"folder": folder.id}
    )
    foldered_id = int(foldered_response.url.split("/notes/")[1].split("/")[0])

    unfiled_current = services.create_note(owner=owner)
    unfiled_response = authenticated_client(owner).post(
        reverse("notes:note_create_sibling", args=[unfiled_current.id]), {"folder": ""}
    )
    unfiled_id = int(unfiled_response.url.split("/notes/")[1].split("/")[0])

    authenticated_client(owner).post(reverse("notes:note_delete", args=[foldered_id]))
    authenticated_client(owner).post(reverse("notes:note_delete", args=[unfiled_id]))

    assert not Note.objects.filter(pk=foldered_id).exists()
    assert not Note.objects.filter(pk=unfiled_id).exists()


@pytest.mark.django_db
def test_tree_toolbar_created_untouched_note_discard_remains_unchanged():
    # Regression guard: the pre-existing, always-unfiled `notes:create`
    # route (the tree toolbar's own New Note button) must still discard an
    # untouched note on delete exactly as before -- added a
    # second creation route, it did not change this one.
    owner = create_account("placeholder-tree-toolbar-owner")

    response = authenticated_client(owner).post(reverse("notes:create"))
    new_id = int(response.url.split("/notes/")[1].split("/")[0])

    authenticated_client(owner).post(reverse("notes:note_delete", args=[new_id]))

    assert not Note.objects.filter(pk=new_id).exists()


@pytest.mark.django_db
def test_home_created_untouched_note_discard_remains_unchanged():
    # Home's own dedicated New Note button posts to the same unchanged
    # `notes:create` route -- same regression guard, deleted with no
    # `origin` (Home's own delete-return state), matching Home's real
    # request shape.
    owner = create_account("placeholder-home-owner")

    response = authenticated_client(owner).post(reverse("notes:create"))
    new_id = int(response.url.split("/notes/")[1].split("/")[0])

    delete_response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[new_id]), follow=True
    )

    assert not Note.objects.filter(pk=new_id).exists()
    messages = [m.message for m in delete_response.context["messages"]]
    assert any("discarded" in m.lower() for m in messages)
