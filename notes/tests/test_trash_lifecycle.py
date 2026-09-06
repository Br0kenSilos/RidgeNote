from datetime import timedelta
from unittest import mock

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.db.models import RESTRICT
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


# -- model / migration ---------------------------------------------------------


@pytest.mark.django_db
def test_note_emptied_at_field_is_nullable_and_defaults_to_none():
    owner = create_account("model-emptied-note-owner")
    note = services.create_note(owner=owner)

    assert note.emptied_at is None


@pytest.mark.django_db
def test_folder_emptied_at_field_is_nullable_and_defaults_to_none():
    owner = create_account("model-emptied-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    assert folder.emptied_at is None


@pytest.mark.django_db
def test_note_emptied_at_can_be_set_without_altering_trashed_at():
    owner = create_account("model-emptied-note-set-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at
    now = timezone.now()

    Note.objects.filter(pk=note.pk).update(emptied_at=now)
    note.refresh_from_db()

    assert note.emptied_at == now
    assert note.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_folder_emptied_at_can_be_set_without_altering_trashed_at():
    owner = create_account("model-emptied-folder-set-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    folder.refresh_from_db()
    original_trashed_at = folder.trashed_at
    now = timezone.now()

    Folder.objects.filter(pk=folder.pk).update(emptied_at=now)
    folder.refresh_from_db()

    assert folder.emptied_at == now
    assert folder.trashed_at == original_trashed_at


def test_note_folder_on_delete_is_still_restrict():
    assert Note._meta.get_field("folder").remote_field.on_delete is RESTRICT


# -- lifecycle boundaries: notes ------------------------------------------------


@pytest.mark.django_db
def test_note_just_inside_30_days_remains_visible_and_restorable():
    owner = create_account("lifecycle-note-inside30-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=29, hours=23))
    note.refresh_from_db()

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert note in services.list_trashed_notes_for_owner(owner=owner)
        restored = services.restore_note_from_trash(note=note)

    assert restored.trashed_at is None


@pytest.mark.django_db
def test_note_exactly_30_days_remains_visible_and_restorable():
    owner = create_account("lifecycle-note-exact30-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=30))
    note.refresh_from_db()

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert note in services.list_trashed_notes_for_owner(owner=owner)
        restored = services.restore_note_from_trash(note=note)

    assert restored.trashed_at is None


@pytest.mark.django_db
def test_note_just_beyond_30_days_becomes_hidden_and_restore_rejected():
    owner = create_account("lifecycle-note-beyond30-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=30, seconds=1))
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert note not in services.list_trashed_notes_for_owner(owner=owner)
        with pytest.raises(services.TrashItemNotRestorableError):
            services.restore_note_from_trash(note=note)

    note.refresh_from_db()
    assert note.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_note_exactly_90_days_triggers_hidden_recoverable_notice():
    owner = create_account("lifecycle-note-exact90-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=90))

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert services.owner_has_hidden_recoverable_items(owner=owner) is True


@pytest.mark.django_db
def test_note_just_beyond_90_days_does_not_trigger_notice():
    owner = create_account("lifecycle-note-beyond90-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(trashed_at=now - timedelta(days=90, seconds=1))

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert services.owner_has_hidden_recoverable_items(owner=owner) is False


# -- lifecycle boundaries: folders ----------------------------------------------


@pytest.mark.django_db
def test_folder_just_inside_30_days_remains_visible_and_restorable():
    owner = create_account("lifecycle-folder-inside30-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()
    Folder.objects.filter(pk=folder.pk).update(trashed_at=now - timedelta(days=29, hours=23))
    folder.refresh_from_db()

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert folder in services.list_trashed_folders_for_owner(owner=owner)
        result = services.restore_folder_from_trash(folder=folder)

    assert result.folder.trashed_at is None


@pytest.mark.django_db
def test_folder_exactly_30_days_remains_visible_and_restorable():
    owner = create_account("lifecycle-folder-exact30-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()
    Folder.objects.filter(pk=folder.pk).update(trashed_at=now - timedelta(days=30))
    folder.refresh_from_db()

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert folder in services.list_trashed_folders_for_owner(owner=owner)
        result = services.restore_folder_from_trash(folder=folder)

    assert result.folder.trashed_at is None


@pytest.mark.django_db
def test_folder_just_beyond_30_days_becomes_hidden_and_restore_rejected():
    owner = create_account("lifecycle-folder-beyond30-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()
    Folder.objects.filter(pk=folder.pk).update(trashed_at=now - timedelta(days=30, seconds=1))
    folder.refresh_from_db()
    original_trashed_at = folder.trashed_at

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert folder not in services.list_trashed_folders_for_owner(owner=owner)
        with pytest.raises(services.TrashItemNotRestorableError):
            services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_folder_exactly_90_days_triggers_hidden_recoverable_notice():
    owner = create_account("lifecycle-folder-exact90-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()
    Folder.objects.filter(pk=folder.pk).update(trashed_at=now - timedelta(days=90))

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert services.owner_has_hidden_recoverable_items(owner=owner) is True


@pytest.mark.django_db
def test_folder_just_beyond_90_days_does_not_trigger_notice():
    owner = create_account("lifecycle-folder-beyond90-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()
    Folder.objects.filter(pk=folder.pk).update(trashed_at=now - timedelta(days=90, seconds=1))

    with mock.patch("notes.services.timezone.now", return_value=now):
        assert services.owner_has_hidden_recoverable_items(owner=owner) is False


# -- Trash listing enforcement --------------------------------------------------


@pytest.mark.django_db
def test_list_trashed_notes_excludes_emptied_notes():
    owner = create_account("listing-note-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    assert note not in services.list_trashed_notes_for_owner(owner=owner)


@pytest.mark.django_db
def test_list_trashed_folders_excludes_emptied_folders():
    owner = create_account("listing-folder-emptied-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    assert folder not in services.list_trashed_folders_for_owner(owner=owner)


# -- Restore-route enforcement ---------------------------------------------------


@pytest.mark.django_db
def test_note_restore_view_rejects_emptied_note_and_changes_no_data():
    owner = create_account("restore-reject-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 3")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    original_trashed_at = note.trashed_at

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )
    note.refresh_from_db()

    assert response.status_code == 200
    assert note.trashed_at == original_trashed_at
    assert b"no longer available for self-service restore" in response.content


@pytest.mark.django_db
def test_note_restore_view_rejects_aged_beyond_30_days_note():
    owner = create_account("restore-reject-aged-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 4")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    response = authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_folder_restore_view_rejects_emptied_folder():
    owner = create_account("folder-restore-reject-emptied-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    folder.refresh_from_db()
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )
    folder.refresh_from_db()

    assert response.status_code == 200
    assert folder.trashed_at is not None
    assert b"no longer available for self-service restore" in response.content


@pytest.mark.django_db
def test_folder_restore_view_rejects_aged_beyond_30_days_folder():
    owner = create_account("folder-restore-reject-aged-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    response = authenticated_client(owner).post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 302
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_note_restore_cross_owner_remains_blocked_even_when_ineligible():
    owner = create_account("restore-cross-ineligible-owner")
    other = create_account("restore-cross-ineligible-other")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 5")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(other).post(reverse("notes:note_restore", args=[note.id]))

    assert response.status_code == 404


# -- Empty Trash: availability ----------------------------------------------------


@pytest.mark.django_db
def test_trash_page_shows_empty_trash_only_when_eligible_item_exists():
    owner = create_account("empty-availability-owner")

    # Targets the actual Empty Trash control (an `<a ...>Empty Trash</a>`
    # link), not the bare phrase -- the in-app Help panel (embedded on
    # every page) explains the Empty Trash feature in its own prose,
    # which contains the same words outside of any such link.
    response = authenticated_client(owner).get(reverse("notes:trash"))
    assert ">Empty Trash</a>" not in response.content.decode()

    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 6")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    assert ">Empty Trash</a>" in response.content.decode()


@pytest.mark.django_db
def test_trash_page_hides_empty_trash_when_only_hidden_items_exist():
    owner = create_account("empty-availability-hidden-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 7")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    response = authenticated_client(owner).get(reverse("notes:trash"))

    # See test_trash_page_shows_empty_trash_only_when_eligible_item_exists
    # for why this targets the actual control, not the bare phrase.
    assert ">Empty Trash</a>" not in response.content.decode()


# -- Empty Trash: GET confirmation ------------------------------------------------


@pytest.mark.django_db
def test_trash_empty_get_has_no_side_effects():
    owner = create_account("empty-get-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 8")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert response.status_code == 200
    assert note.emptied_at is None


@pytest.mark.django_db
def test_trash_empty_get_counts_only_eligible_items():
    owner = create_account("empty-get-counts-owner")
    eligible_note = services.create_note(owner=owner)
    services.rename_note(note=eligible_note, title="Eligible Note 9")
    services.move_note_to_trash(note=eligible_note)
    hidden_note = services.create_note(owner=owner)
    services.rename_note(note=hidden_note, title="Hidden Note 10")
    services.move_note_to_trash(note=hidden_note)
    Note.objects.filter(pk=hidden_note.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    response = authenticated_client(owner).get(reverse("notes:trash_empty"))
    content = response.content.decode()

    assert "1 note" in content
    assert "0 folder" in content


# -- Empty Trash: POST behavior ----------------------------------------------------


@pytest.mark.django_db
def test_trash_empty_post_sets_shared_emptied_at_on_eligible_notes_and_folders():
    owner = create_account("empty-post-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 11")
    services.move_note_to_trash(note=note)
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert note.emptied_at is not None
    assert folder.emptied_at is not None
    assert note.emptied_at == folder.emptied_at


@pytest.mark.django_db
def test_trash_empty_post_preserves_original_trashed_at():
    owner = create_account("empty-post-preserve-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 12")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert note.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_trash_empty_post_does_not_alter_already_hidden_items():
    owner = create_account("empty-post-hidden-unaffected-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 13")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=31))

    authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert note.emptied_at is None


@pytest.mark.django_db
def test_trash_empty_post_does_not_alter_active_items():
    owner = create_account("empty-post-active-unaffected-owner")
    active_note = services.create_note(owner=owner)

    authenticated_client(owner).post(reverse("notes:trash_empty"))
    active_note.refresh_from_db()

    assert active_note.trashed_at is None
    assert active_note.emptied_at is None


@pytest.mark.django_db
def test_trash_empty_post_does_not_alter_other_owners_items():
    owner = create_account("empty-post-scope-owner")
    other = create_account("empty-post-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Other Note 14")
    services.move_note_to_trash(note=other_note)

    authenticated_client(owner).post(reverse("notes:trash_empty"))
    other_note.refresh_from_db()

    assert other_note.emptied_at is None


@pytest.mark.django_db
def test_trash_empty_post_never_deletes_rows():
    owner = create_account("empty-post-no-delete-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 15")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:trash_empty"))

    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_trash_empty_post_is_idempotent_on_repeat_submission():
    owner = create_account("empty-post-idempotent-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 16")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()
    first_emptied_at = note.emptied_at

    response = authenticated_client(owner).post(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.emptied_at == first_emptied_at


@pytest.mark.django_db
def test_trash_empty_post_unauthenticated_is_blocked():
    owner = create_account("empty-post-unauth-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 17")
    services.move_note_to_trash(note=note)

    response = Client().post(reverse("notes:trash_empty"))
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.emptied_at is None


@pytest.mark.django_db
def test_trash_page_has_per_item_permanent_delete_control():
    # A per-Note "Delete permanently" control
    # exists, distinct from and alongside Empty Trash's own bulk action.
    owner = create_account("empty-per-item-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 18")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Delete permanently" in content
    assert reverse("notes:note_permanent_delete", args=[note.id]) in content


# -- Empty Trash: audit event ------------------------------------------------------


@pytest.mark.django_db
def test_trash_empty_post_records_audit_event_with_counts_only():
    owner = create_account("empty-audit-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Secret Title")
    services.move_note_to_trash(note=note)
    folder = services.create_folder(owner=owner, name="Secret Folder")
    services.move_folder_to_trash(folder=folder)

    authenticated_client(owner).post(reverse("notes:trash_empty"))

    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_NOTES_EMPTY_TRASH)
    assert event.actor_id == owner.id
    assert event.details == {"note_count": 1, "folder_count": 1}
    assert "Secret Title" not in str(event.details)
    assert "Secret Folder" not in str(event.details)


@pytest.mark.django_db
def test_trash_empty_post_with_no_eligible_items_does_not_record_audit_event():
    owner = create_account("empty-audit-noop-owner")

    authenticated_client(owner).post(reverse("notes:trash_empty"))

    assert not AuditEvent.objects.filter(event_type=AuditEvent.EVENT_NOTES_EMPTY_TRASH).exists()


# -- hidden recovery notice ---------------------------------------------------------


@pytest.mark.django_db
def test_trash_page_shows_hidden_notice_for_emptied_item():
    owner = create_account("notice-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 19")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "may still be recoverable by a" in response.content.decode()


@pytest.mark.django_db
def test_trash_page_shows_hidden_notice_for_item_older_than_30_but_not_90_days():
    owner = create_account("notice-aged-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 20")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "may still be recoverable by a" in response.content.decode()


@pytest.mark.django_db
def test_trash_page_does_not_show_hidden_notice_for_only_visible_items():
    owner = create_account("notice-visible-only-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 21")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "may still be recoverable by a" not in response.content.decode()


@pytest.mark.django_db
def test_trash_page_does_not_show_hidden_notice_for_items_older_than_90_days():
    owner = create_account("notice-purged-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 22")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=91))

    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "may still be recoverable by a" not in response.content.decode()


@pytest.mark.django_db
def test_trash_page_hidden_notice_is_owner_scoped():
    owner = create_account("notice-scope-owner")
    other = create_account("notice-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Other Note 23")
    services.move_note_to_trash(note=other_note)
    Note.objects.filter(pk=other_note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(owner).get(reverse("notes:trash"))

    assert "may still be recoverable by a" not in response.content.decode()


@pytest.mark.django_db
def test_trash_page_hidden_notice_does_not_leak_metadata():
    owner = create_account("notice-no-leak-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Very Secret Title")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Secret Folder Name")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Very Secret Title" not in content
    assert "Secret Folder Name" not in content
