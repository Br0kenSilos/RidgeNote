import json

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Folder, Note

PASSWORD = "LongUniquePassword123!"

TRASHED_ITEM_MESSAGE = "This item is in Trash and can no longer be modified."


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


# -- autosave -------------------------------------------------------------------


@pytest.mark.django_db
def test_autosave_rejects_trashed_note_and_leaves_content_unchanged():
    owner = create_account("freeze-autosave-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Original Title")
    original_version = note.version
    original_body_plain_text = note.body_plain_text
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Hacked Title",
                "body_json": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}
                    ],
                },
                "version": original_version,
            }
        ),
        content_type="application/json",
    )
    note.refresh_from_db()
    payload = json.loads(response.content)

    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["error"] == "trashed"
    assert note.title == "Original Title"
    assert note.body_plain_text == original_body_plain_text
    assert note.version == original_version


@pytest.mark.django_db
def test_autosave_rejects_emptied_administrator_only_note():
    owner = create_account("freeze-autosave-emptied-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    original_version = note.version

    response = authenticated_client(owner).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Hacked Title",
                "body_json": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}
                    ],
                },
                "version": note.version,
            }
        ),
        content_type="application/json",
    )
    note.refresh_from_db()

    assert response.status_code == 409
    assert note.title != "Hacked Title"
    assert note.version == original_version


@pytest.mark.django_db
def test_autosave_still_succeeds_for_active_note():
    owner = create_account("freeze-autosave-active-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Active Save",
                "body_json": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}
                    ],
                },
                "version": note.version,
            }
        ),
        content_type="application/json",
    )
    note.refresh_from_db()

    assert response.status_code == 200
    assert note.title == "Active Save"


@pytest.mark.django_db
def test_autosave_cross_owner_behavior_unchanged():
    owner = create_account("freeze-autosave-cross-owner")
    other = create_account("freeze-autosave-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:autosave", args=[note.id]),
        data=json.dumps(
            {
                "title": "Should Not Apply",
                "body_json": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}
                    ],
                },
                "version": note.version,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 404


# -- rename -----------------------------------------------------------------------


@pytest.mark.django_db
def test_note_rename_rejects_trashed_note():
    owner = create_account("freeze-rename-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Original")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "New Title"}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.title == "Original"


@pytest.mark.django_db
def test_note_rename_home_rejects_trashed_note():
    owner = create_account("freeze-rename-home-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Original")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_rename_home", args=[note.id]), {"title": "New Title"}
    )
    note.refresh_from_db()

    assert note.title == "Original"


@pytest.mark.django_db
def test_folder_rename_rejects_trashed_folder():
    owner = create_account("freeze-folder-rename-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Original Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_rename", args=[note.id, folder.id]), {"name": "New Folder Name"}
    )
    folder.refresh_from_db()

    assert response.status_code == 302
    assert folder.name == "Original Folder"


@pytest.mark.django_db
def test_folder_rename_home_rejects_trashed_folder():
    owner = create_account("freeze-folder-rename-home-owner")
    folder = services.create_folder(owner=owner, name="Original Folder")
    services.move_folder_to_trash(folder=folder)

    authenticated_client(owner).post(
        reverse("notes:folder_rename_home", args=[folder.id]), {"name": "New Folder Name"}
    )
    folder.refresh_from_db()

    assert folder.name == "Original Folder"


@pytest.mark.django_db
def test_note_rename_still_works_for_active_note():
    owner = create_account("freeze-rename-active-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Renamed"}
    )
    note.refresh_from_db()

    assert note.title == "Renamed"


@pytest.mark.django_db
def test_note_rename_cross_owner_is_blocked():
    owner = create_account("freeze-rename-cross-owner")
    other = create_account("freeze-rename-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Hijacked"}
    )

    assert response.status_code == 404


# -- move -------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_move_rejects_trashed_note():
    owner = create_account("freeze-move-owner")
    folder = services.create_folder(owner=owner, name="Destination")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 2")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": folder.id}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_home_rejects_trashed_note():
    owner = create_account("freeze-move-home-owner")
    folder = services.create_folder(owner=owner, name="Destination")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 3")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": folder.id}
    )
    note.refresh_from_db()

    assert note.folder_id is None


@pytest.mark.django_db
def test_note_move_still_works_for_active_note():
    owner = create_account("freeze-move-active-owner")
    folder = services.create_folder(owner=owner, name="Destination")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": folder.id}
    )
    note.refresh_from_db()

    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_note_move_cross_owner_is_blocked():
    owner = create_account("freeze-move-cross-owner")
    other = create_account("freeze-move-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_move", args=[note.id]), {"folder": ""}
    )

    assert response.status_code == 404


# -- tags -------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_tag_assign_rejects_trashed_note():
    owner = create_account("freeze-tag-assign-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 4")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]), {"name": "urgent", "color": "rose"}
    )

    assert response.status_code == 302
    assert note.tags.count() == 0


@pytest.mark.django_db
def test_note_tag_remove_rejects_trashed_note():
    owner = create_account("freeze-tag-remove-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="urgent", color="rose")
    services.assign_tag_to_note(note=note, tag=tag)
    services.rename_note(note=note, title="Note 5")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_tag_remove", args=[note.id]), {"tag_id": tag.id}
    )

    assert list(note.tags.values_list("id", flat=True)) == [tag.id]


@pytest.mark.django_db
def test_note_tag_assign_still_works_for_active_note():
    owner = create_account("freeze-tag-assign-active-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_tag_assign", args=[note.id]), {"name": "urgent", "color": "rose"}
    )

    assert note.tags.count() == 1


@pytest.mark.django_db
def test_note_tag_assign_cross_owner_is_blocked():
    owner = create_account("freeze-tag-cross-owner")
    other = create_account("freeze-tag-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_tag_assign", args=[note.id]), {"name": "urgent", "color": "rose"}
    )

    assert response.status_code == 404


# -- pin --------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_pin_rejects_trashed_note():
    owner = create_account("freeze-pin-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 6")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )
    note.refresh_from_db()

    assert note.pinned is False


@pytest.mark.django_db
def test_note_unpin_rejects_trashed_note():
    owner = create_account("freeze-unpin-owner")
    note = services.create_note(owner=owner)
    services.set_note_pinned(note=note, pinned=True)
    services.rename_note(note=note, title="Note 7")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "false"}
    )
    note.refresh_from_db()

    assert note.pinned is True


@pytest.mark.django_db
def test_note_pin_still_works_for_active_note():
    owner = create_account("freeze-pin-active-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )
    note.refresh_from_db()

    assert note.pinned is True


@pytest.mark.django_db
def test_note_pin_cross_owner_is_blocked():
    owner = create_account("freeze-pin-cross-owner")
    other = create_account("freeze-pin-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )

    assert response.status_code == 404


# -- duplicate --------------------------------------------------------------------


@pytest.mark.django_db
def test_note_duplicate_rejects_trashed_note_and_creates_no_row():
    owner = create_account("freeze-duplicate-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 8")
    services.move_note_to_trash(note=note)
    note_count_before = Note.objects.count()

    response = authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 302
    assert Note.objects.count() == note_count_before


@pytest.mark.django_db
def test_note_duplicate_still_works_for_active_note():
    owner = create_account("freeze-duplicate-active-owner")
    note = services.create_note(owner=owner)
    note_count_before = Note.objects.count()

    authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))

    assert Note.objects.count() == note_count_before + 1


@pytest.mark.django_db
def test_note_duplicate_cross_owner_is_blocked():
    owner = create_account("freeze-duplicate-cross-owner")
    other = create_account("freeze-duplicate-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(reverse("notes:note_duplicate", args=[note.id]))

    assert response.status_code == 404


# -- service-layer direct-call safety (defense in depth) -------------------------


@pytest.mark.django_db
def test_service_layer_rejects_direct_mutation_calls_on_trashed_note():
    owner = create_account("freeze-service-note-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 9")
    services.move_note_to_trash(note=note)

    with pytest.raises(services.TrashedItemMutationError):
        services.rename_note(note=note, title="Should Fail")
    with pytest.raises(services.TrashedItemMutationError):
        services.assign_note_folder(note=note, folder=None)
    with pytest.raises(services.TrashedItemMutationError):
        services.set_note_pinned(note=note, pinned=True)
    with pytest.raises(services.TrashedItemMutationError):
        services.duplicate_note(note=note)


@pytest.mark.django_db
def test_service_layer_rejects_direct_mutation_calls_on_trashed_folder():
    owner = create_account("freeze-service-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    with pytest.raises(services.TrashedItemMutationError):
        services.rename_folder(folder=folder, name="Should Fail")


@pytest.mark.django_db
def test_service_layer_rejects_direct_tag_mutation_calls_on_trashed_note():
    owner = create_account("freeze-service-tag-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="urgent", color="rose")
    services.rename_note(note=note, title="Note 10")
    services.move_note_to_trash(note=note)

    with pytest.raises(services.TrashedItemMutationError):
        services.assign_tag_to_note(note=note, tag=tag)
    with pytest.raises(services.TrashedItemMutationError):
        services.remove_tag_from_note(note=note, tag=tag)


# -- lifecycle integrity ------------------------------------------------------------


@pytest.mark.django_db
def test_blocked_mutations_do_not_alter_trashed_at_or_emptied_at():
    owner = create_account("freeze-lifecycle-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 11")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()
    original_emptied_at = note.emptied_at

    authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Nope"}
    )
    authenticated_client(owner).post(
        reverse("notes:note_pin_set", args=[note.id]), {"pinned": "true"}
    )
    authenticated_client(owner).post(reverse("notes:note_duplicate", args=[note.id]))
    note.refresh_from_db()

    assert note.trashed_at == original_trashed_at
    assert note.emptied_at == original_emptied_at


@pytest.mark.django_db
def test_blocked_mutation_does_not_restore_or_reactivate_item():
    owner = create_account("freeze-no-restore-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 12")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Nope"}
    )
    note.refresh_from_db()

    assert note.trashed_at is not None


@pytest.mark.django_db
def test_visible_stage_trashed_note_is_immutable():
    owner = create_account("freeze-visible-stage-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Visible Stage Title")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Nope"}
    )
    note.refresh_from_db()

    assert note.title == "Visible Stage Title"


@pytest.mark.django_db
def test_hidden_stage_trashed_note_is_immutable():
    from datetime import timedelta

    owner = create_account("freeze-hidden-stage-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Hidden Stage Title")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    authenticated_client(owner).post(
        reverse("notes:note_rename", args=[note.id]), {"title": "Nope"}
    )
    note.refresh_from_db()

    assert note.title == "Hidden Stage Title"


@pytest.mark.django_db
def test_hidden_stage_trashed_folder_is_immutable():
    from datetime import timedelta

    owner = create_account("freeze-hidden-stage-folder-owner")
    folder = services.create_folder(owner=owner, name="Hidden Stage Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    authenticated_client(owner).post(
        reverse("notes:folder_rename_home", args=[folder.id]), {"name": "Nope"}
    )
    folder.refresh_from_db()

    assert folder.name == "Hidden Stage Folder"
