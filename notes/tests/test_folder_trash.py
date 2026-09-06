from datetime import timedelta

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


def _move_select_html(content: str, *, id_prefix: str = "overflow") -> str:
    """Isolates one note-move `<select>`'s own markup (its destination
    options), bounded by its closing tag, rather than searching the
    whole page for folder-name text that may also appear in unrelated
    global content such as the Help panel."""
    marker = f'id="{id_prefix}-move-folder"'
    start = content.rindex("<select", 0, content.index(marker))
    end = content.index("</select>", start) + len("</select>")
    return content[start:end]


# -- model / migration --------------------------------------------------------


@pytest.mark.django_db
def test_folder_trashed_at_field_is_nullable_and_defaults_to_none():
    owner = create_account("model-folder-trash-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_trashed_at_can_be_set_directly():
    owner = create_account("model-folder-trash-set-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    now = timezone.now()

    Folder.objects.filter(pk=folder.pk).update(trashed_at=now)
    folder.refresh_from_db()

    assert folder.trashed_at is not None


# -- folder-name uniqueness / restore conflicts --------------------------------


@pytest.mark.django_db
def test_active_folder_uniqueness_still_enforced_case_insensitively():
    owner = create_account("active-uniq-owner")
    services.create_folder(owner=owner, name="Projects")

    with pytest.raises(services.FolderNameConflictError):
        services.create_folder(owner=owner, name="projects")


@pytest.mark.django_db
def test_trashed_folder_does_not_block_creating_active_folder_with_same_name():
    owner = create_account("trashed-noblock-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    new_folder = services.create_folder(owner=owner, name="Projects")

    assert new_folder.id != folder.id
    assert new_folder.trashed_at is None


@pytest.mark.django_db
def test_restore_trashed_folder_conflicts_cleanly_with_active_same_name_folder():
    owner = create_account("restore-conflict-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    services.create_folder(owner=owner, name="Projects")

    with pytest.raises(services.FolderNameConflictError):
        services.restore_folder_from_trash(folder=folder)

    folder.refresh_from_db()
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_restore_trashed_folder_view_shows_clean_conflict_message_not_500():
    owner = create_account("restore-conflict-view-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)
    services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )

    assert response.status_code == 200
    folder.refresh_from_db()
    assert folder.trashed_at is not None
    assert b"already exists" in response.content


# -- move_folder_to_trash / restore_folder_from_trash (service) ---------------


@pytest.mark.django_db
def test_move_folder_to_trash_sets_folder_trashed_at():
    owner = create_account("svc-folder-delete-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    trashed = services.move_folder_to_trash(folder=folder)

    assert trashed.trashed_at is not None


@pytest.mark.django_db
def test_move_folder_to_trash_trashes_currently_active_directly_contained_notes():
    owner = create_account("svc-folder-cascade-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_at is not None


@pytest.mark.django_db
def test_move_folder_to_trash_does_not_alter_already_trashed_notes():
    owner = create_account("svc-folder-already-trashed-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=1))
    note.refresh_from_db()
    original_trashed_at = note.trashed_at

    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_at == original_trashed_at


@pytest.mark.django_db
def test_move_folder_to_trash_does_not_affect_notes_outside_the_folder():
    owner = create_account("svc-folder-scope-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    other_folder = services.create_folder(owner=owner, name="Personal")
    unfiled_note = services.create_note(owner=owner)
    other_folder_note = services.create_note(owner=owner)
    services.assign_note_folder(note=other_folder_note, folder=other_folder)

    services.move_folder_to_trash(folder=folder)
    unfiled_note.refresh_from_db()
    other_folder_note.refresh_from_db()

    assert unfiled_note.trashed_at is None
    assert other_folder_note.trashed_at is None


@pytest.mark.django_db
def test_move_folder_to_trash_rolls_back_folder_and_notes_together_on_failure(monkeypatch):
    # The folder's own lifecycle update and its
    # associated-note cascade must commit together or not at all --
    # these were previously two separate, independently autocommitting
    # statements.
    owner = create_account("svc-folder-trash-rollback-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    def failing_trash_notes(*, folder, trashed_at):
        raise RuntimeError("simulated failure mid-trash")

    monkeypatch.setattr(services, "_trash_notes_associated_with_folder", failing_trash_notes)

    with pytest.raises(RuntimeError):
        services.move_folder_to_trash(folder=folder)

    folder.refresh_from_db()
    note.refresh_from_db()
    assert folder.trashed_at is None
    assert note.trashed_at is None


@pytest.mark.django_db
def test_restore_folder_from_trash_clears_trashed_at():
    owner = create_account("svc-folder-restore-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    result = services.restore_folder_from_trash(folder=folder)

    assert result.folder.trashed_at is None


@pytest.mark.django_db
def test_restore_folder_from_trash_restores_marked_eligible_notes():
    # A note trashed by this exact folder's own
    # cascade carries its `trashed_via_folder` marker, so it is eligible
    # to restore together with the folder.
    owner = create_account("svc-folder-restore-notes-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    result = services.restore_folder_from_trash(folder=folder)
    note.refresh_from_db()

    assert note.trashed_at is None
    assert note.trashed_via_folder_id is None
    assert result.restored_note_ids == [note.id]


@pytest.mark.django_db
def test_list_trashed_folders_for_owner_returns_only_that_owners_trashed_folders():
    owner = create_account("svc-folder-list-owner")
    other = create_account("svc-folder-list-other")
    trashed_folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=trashed_folder)
    active_folder = services.create_folder(owner=owner, name="Personal")
    other_trashed_folder = services.create_folder(owner=other, name="Other Projects")
    services.move_folder_to_trash(folder=other_trashed_folder)

    results = services.list_trashed_folders_for_owner(owner=owner)

    assert trashed_folder in results
    assert active_folder not in results
    assert other_trashed_folder not in results


# -- folder_delete view (note-detail context) ---------------------------------


@pytest.mark.django_db
def test_folder_delete_get_is_rejected_without_trashing():
    # Confirmation happens via an in-context dialog (`delete-confirm.ts`)
    # -- this route is POST-only.
    owner = create_account("folder-delete-confirm-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(
        reverse("notes:folder_delete", args=[note.id, folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 405
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_delete_post_sets_folder_trashed_at_and_returns_to_hosting_note():
    # `folder_delete` is reached only from the
    # note-detail-hosted tree, where the URL's own `note_id` segment is
    # the note currently being viewed (not necessarily related to the
    # deleted folder) -- success returns there instead of
    # unconditionally landing on Trash.
    owner = create_account("folder-delete-post-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[note.id, folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[note.id])
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_folder_delete_post_trashes_directly_contained_active_notes():
    owner = create_account("folder-delete-cascade-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")
    contained_note = services.create_note(owner=owner)
    services.assign_note_folder(note=contained_note, folder=folder)

    authenticated_client(owner).post(reverse("notes:folder_delete", args=[note.id, folder.id]))
    contained_note.refresh_from_db()

    assert contained_note.trashed_at is not None


@pytest.mark.django_db
def test_folder_delete_is_owner_scoped():
    owner = create_account("folder-delete-scope-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    authenticated_client(owner).post(reverse("notes:folder_delete", args=[note.id, folder.id]))
    folder.refresh_from_db()

    assert folder.owner_id == owner.id
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_folder_delete_cross_owner_is_blocked():
    owner = create_account("folder-delete-cross-owner")
    other = create_account("folder-delete-cross-other")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(other).post(
        reverse("notes:folder_delete", args=[note.id, folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 404
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_delete_unauthenticated_post_is_blocked():
    owner = create_account("folder-delete-unauth-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = Client().post(reverse("notes:folder_delete", args=[note.id, folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 302
    assert folder.trashed_at is None


# -- folder_delete_home view (Home context) -----------------------------------


@pytest.mark.django_db
def test_folder_delete_home_post_sets_folder_trashed_at_and_redirects_home():
    # With no `origin` state supplied (a direct/
    # legacy POST), the fixed safe fallback is Home, never Trash.
    owner = create_account("folder-delete-home-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_folder_delete_home_cross_owner_is_blocked():
    owner = create_account("folder-delete-home-cross-owner")
    other = create_account("folder-delete-home-cross-other")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(other).post(
        reverse("notes:folder_delete_home", args=[folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 404
    assert folder.trashed_at is None


# -- active-surface exclusions --------------------------------------------------


@pytest.mark.django_db
def test_tree_navigation_excludes_trashed_folders():
    owner = create_account("exclude-tree-folder-owner")
    services.create_folder(owner=owner, name="Active Folder")
    trashed_folder = services.create_folder(owner=owner, name="Trashed Folder")
    services.move_folder_to_trash(folder=trashed_folder)

    folders, _unfiled_notes = services.notes_grouped_for_tree(owner=owner)
    folder_names = {f.name for f in folders}

    assert "Active Folder" in folder_names
    assert "Trashed Folder" not in folder_names


@pytest.mark.django_db
def test_move_form_folder_selector_excludes_trashed_folders():
    from notes.forms import NoteMoveForm

    owner = create_account("exclude-move-folder-owner")
    active_folder = services.create_folder(owner=owner, name="Active Folder")
    trashed_folder = services.create_folder(owner=owner, name="Trashed Folder")
    services.move_folder_to_trash(folder=trashed_folder)

    form = NoteMoveForm(owner=owner)
    queryset_ids = set(form.fields["folder"].queryset.values_list("id", flat=True))

    assert active_folder.id in queryset_ids
    assert trashed_folder.id not in queryset_ids


@pytest.mark.django_db
def test_list_folders_for_owner_excludes_trashed_folders():
    owner = create_account("exclude-list-folder-owner")
    services.create_folder(owner=owner, name="Active Folder")
    trashed_folder = services.create_folder(owner=owner, name="Trashed Folder")
    services.move_folder_to_trash(folder=trashed_folder)

    names = list(services.list_folders_for_owner(owner=owner).values_list("name", flat=True))

    assert "Active Folder" in names
    assert "Trashed Folder" not in names


# -- Trash page: folders section -----------------------------------------------


@pytest.mark.django_db
def test_trash_page_lists_only_owners_trashed_folders():
    owner = create_account("trash-page-folder-owner")
    other = create_account("trash-page-folder-other")
    trashed_folder = services.create_folder(owner=owner, name="My Trashed Folder")
    services.move_folder_to_trash(folder=trashed_folder)
    other_trashed_folder = services.create_folder(owner=other, name="Someone Elses Folder")
    services.move_folder_to_trash(folder=other_trashed_folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "My Trashed Folder" in content
    assert "Someone Elses Folder" not in content


@pytest.mark.django_db
def test_trash_page_does_not_list_active_folders():
    owner = create_account("trash-page-active-folder-owner")
    services.create_folder(owner=owner, name="Still Active Folder")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    # Trash carries the same tree rail Home/note-
    # detail already have, so the active folder legitimately appears
    # there (as it does on every other tree-having page) -- this
    # assertion is specifically about the Trash *listing* itself, not
    # the whole page.
    trash_panel_start = content.index('class="panel panel--trash trash-panel"')
    trash_panel_end = content.index('id="workspace-drawer"')
    trash_panel = content[trash_panel_start:trash_panel_end]
    assert "Still Active Folder" not in trash_panel


@pytest.mark.django_db
def test_trash_page_folder_section_has_no_permanent_delete_control():
    # Grouped restore (folder + its associated
    # notes) is an intentional feature, reached via the same plain
    # "Restore" trigger and a confirmation dialog -- only permanent
    # delete remains genuinely absent from this page.
    owner = create_account("trash-page-folder-no-purge-owner")
    trashed_folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=trashed_folder)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Permanently delete" not in content
    assert "Permanent delete" not in content


# -- folder_restore view --------------------------------------------------------


@pytest.mark.django_db
def test_folder_restore_clears_only_folder_trashed_at():
    owner = create_account("folder-restore-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_restore_restores_marked_eligible_notes():
    # See the identical service-level test above.
    owner = create_account("folder-restore-notes-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_restore", args=[folder.id]), follow=True
    )
    note.refresh_from_db()

    assert note.trashed_at is None
    messages = [m.message for m in response.context["messages"]]
    assert any("1 associated note was also restored" in m for m in messages)


@pytest.mark.django_db
def test_folder_restore_does_not_restore_unrelated_notes_in_the_same_folder():
    # The required principle: a note is never restored merely because it
    # currently references the folder -- only marked notes actually
    # trashed by *this* folder's own cascade.
    owner = create_account("folder-restore-unrelated-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    pre_trashed_note = services.create_note(owner=owner)
    services.assign_note_folder(note=pre_trashed_note, folder=folder)
    services.rename_note(note=pre_trashed_note, title="Already Trashed First")
    services.move_note_to_trash(note=pre_trashed_note)
    services.move_folder_to_trash(folder=folder)

    authenticated_client(owner).post(reverse("notes:folder_restore", args=[folder.id]))
    pre_trashed_note.refresh_from_db()

    assert pre_trashed_note.trashed_at is not None
    assert pre_trashed_note.trashed_via_folder_id is None


@pytest.mark.django_db
def test_folder_restore_is_owner_scoped():
    owner = create_account("folder-restore-scope-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    authenticated_client(owner).post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert folder.owner_id == owner.id
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_restore_cross_owner_is_blocked():
    owner = create_account("folder-restore-cross-owner")
    other = create_account("folder-restore-cross-other")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(other).post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 404
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_folder_restore_unauthenticated_post_is_blocked():
    owner = create_account("folder-restore-unauth-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = Client().post(reverse("notes:folder_restore", args=[folder.id]))
    folder.refresh_from_db()

    assert response.status_code == 302
    assert folder.trashed_at is not None


# -- note restored while its folder remains trashed ----------------------------


@pytest.mark.django_db
def test_note_restored_while_folder_trashed_moves_to_recovered_items():
    """Owner restore never leaves a newly
    restored active note referencing a still-trashed folder; it falls back
    to the owner's marker-based Recovered Items folder instead."""
    owner = create_account("note-restore-folder-trashed-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.trashed_at is None
    assert note.folder_id != folder.id
    destination = Folder.objects.get(pk=note.folder_id)
    assert destination.is_recovery_folder is True
    assert destination.name == "Recovered Items"


@pytest.mark.django_db
def test_note_restored_while_folder_trashed_appears_under_recovered_items_in_tree():
    """Since the note relocates to the active
    Recovered Items folder rather than staying tied to the hidden trashed
    folder, it is correctly surfaced in tree navigation under its new
    folder, not excluded from it."""
    owner = create_account("note-restore-tree-visibility-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Restored While Folder Trashed")
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    assert note not in unfiled_notes
    recovered_items = next(f for f in folders if f.id == note.folder_id)
    assert recovered_items.name == "Recovered Items"
    assert note in list(recovered_items.notes.all())


@pytest.mark.django_db
def test_note_restored_while_folder_trashed_still_appears_on_home():
    """Remains findable on Home because it was
    correctly relocated to the active Recovered Items folder rather than
    because Home tolerates a hidden-folder reference."""
    owner = create_account("note-restore-home-visibility-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Findable After Folder Trashed")
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    services.restore_note_from_trash(note=note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Findable After Folder Trashed" in content


# -- Move form safety when the note's current folder is trashed ---------------


@pytest.mark.django_db
def test_note_restored_while_folder_trashed_no_longer_references_it():
    """Direct duplicate check (kept separate since
    this file's "Move form safety" tests below depend on this exact
    invariant): a restored note's folder reference is never a trashed
    folder."""
    owner = create_account("move-safety-association-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)

    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    assert note.trashed_at is None
    assert note.folder_id != folder.id
    assert note.folder.trashed_at is None


@pytest.mark.django_db
def test_note_detail_move_form_reflects_recovered_items_after_restore_from_trashed_folder():
    """The move form's current selection
    correctly reflects the note's actual new destination (Recovered Items),
    a normal active folder, not stale data pointing at the trashed original
    folder. The old `keep-current-folder` sentinel value existed only to
    represent a hidden/trashed current folder that could not otherwise
    appear as a selectable option -- that state does not arise via
    restore, so the form shows a plain, normally selected active
    option instead."""
    owner = create_account("move-safety-render-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert 'value="" selected' not in content
    assert "keep-current-folder" not in content
    assert f'value="{note.folder_id}" selected' in content
    assert "Recovered Items" in content
    move_select = _move_select_html(content)
    assert "Projects" not in move_select


@pytest.mark.django_db
def test_home_move_form_reflects_recovered_items_after_restore_from_trashed_folder():
    """Home's move form current selection
    correctly reflects Recovered Items, a normal active folder, as the
    note's actual new destination (see the sibling detail-page test above
    for why the old `keep-current-folder` sentinel does not apply)."""
    owner = create_account("move-safety-home-render-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Move Safety Home Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    services.restore_note_from_trash(note=note)
    note.refresh_from_db()

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert 'value="" selected' not in content
    assert "keep-current-folder" not in content
    # Home has no duplicate row-menu Move
    # form, leaving 2 (the tree's wide-rail Move popover plus the narrow
    # drawer's tree copy). Home's Recent Notes row has its own, separate Move
    # popover (reusing the identical shared partial/route, not a
    # duplicate within any one row) -- a third, legitimate occurrence.
    assert content.count(f'value="{note.folder_id}" selected') == 3
    assert "Recovered Items" in content


@pytest.mark.django_db
def test_submitting_move_form_without_change_does_not_unfile_note_with_trashed_folder():
    """Owner restore never produces an active
    note referencing a trashed folder (see the tests above), so this
    defensive move-form scenario is constructed directly, bypassing
    restore, since `assign_note_folder()` itself never validates the
    target folder's own trash state (a separate, unaffected, still-valid
    edge case) and a note can therefore still end up active while its
    folder is trashed by other means. This test's own subject -- the move
    form must never silently unfile such a note on a no-op submission --
    remains fully valid and must keep working regardless of how the state
    was reached."""
    owner = create_account("move-safety-noop-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": "keep-current-folder"}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_submitting_home_move_form_without_change_does_not_unfile_note_with_trashed_folder():
    """State constructed directly, bypassing
    restore, for the same reason given in the sibling test above."""
    owner = create_account("move-safety-home-noop-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()

    response = authenticated_client(owner).post(
        reverse("notes:note_move_home", args=[note.id]), {"folder": "keep-current-folder"}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_can_deliberately_move_note_with_trashed_folder_to_an_active_folder():
    """State constructed directly, bypassing
    restore, for the same reason given above."""
    owner = create_account("move-safety-deliberate-active-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    other_folder = services.create_folder(owner=owner, name="Personal")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": other_folder.id}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id == other_folder.id


@pytest.mark.django_db
def test_can_deliberately_move_note_with_trashed_folder_to_unfiled():
    """State constructed directly, bypassing
    restore, for the same reason given above."""
    owner = create_account("move-safety-deliberate-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": ""}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id is None


@pytest.mark.django_db
def test_trashed_folder_id_never_appears_as_a_move_option_value():
    """State constructed directly, bypassing
    restore, for the same reason given above."""
    owner = create_account("move-safety-no-trashed-option-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert f'value="{folder.id}"' not in content


@pytest.mark.django_db
def test_existing_move_behavior_for_active_folder_note_is_unchanged():
    owner = create_account("move-safety-regression-active-owner")
    note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": folder.id}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_existing_move_behavior_for_unfiled_note_is_unchanged():
    owner = create_account("move-safety-regression-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_move", args=[note.id]), {"folder": ""}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.folder_id is None


# -- existing on_delete=RESTRICT behavior remains valid ------------------------


@pytest.mark.django_db
def test_note_folder_restrict_still_blocks_hard_delete_of_folder_with_active_notes():
    from django.db.models.deletion import RestrictedError

    owner = create_account("restrict-still-valid-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)

    with pytest.raises(RestrictedError):
        folder.delete()
