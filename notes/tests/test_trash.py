import pytest
from accounts import services as account_services
from accounts.models import User
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


# -- model / migration --------------------------------------------------------


@pytest.mark.django_db
def test_note_trashed_at_field_is_nullable_and_defaults_to_none():
    owner = create_account("model-trash-owner")
    note = services.create_note(owner=owner)

    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_trashed_at_can_be_set_directly():
    owner = create_account("model-trash-set-owner")
    note = services.create_note(owner=owner)
    now = timezone.now()

    Note.objects.filter(pk=note.pk).update(trashed_at=now)
    note.refresh_from_db()

    assert note.trashed_at is not None


# -- move_note_to_trash / restore_note_from_trash (service) -------------------


@pytest.mark.django_db
def test_move_note_to_trash_sets_trashed_at():
    owner = create_account("svc-delete-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note With Content")

    trashed = services.move_note_to_trash(note=note)

    assert trashed.trashed_at is not None


@pytest.mark.django_db
def test_restore_note_from_trash_clears_trashed_at():
    owner = create_account("svc-restore-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)

    restored = services.restore_note_from_trash(note=note)

    assert restored.trashed_at is None


@pytest.mark.django_db
def test_list_trashed_notes_for_owner_returns_only_that_owners_trashed_notes():
    owner = create_account("svc-list-owner")
    other = create_account("svc-list-other")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Note 2")
    services.move_note_to_trash(note=trashed_note)
    active_note = services.create_note(owner=owner)
    other_trashed_note = services.create_note(owner=other)
    services.rename_note(note=other_trashed_note, title="Other Trashed Note 3")
    services.move_note_to_trash(note=other_trashed_note)

    results = services.list_trashed_notes_for_owner(owner=owner)

    assert trashed_note in results
    assert active_note not in results
    assert other_trashed_note not in results


# -- note_delete view -----------------------------------------------------------


@pytest.mark.django_db
def test_note_delete_get_is_rejected_without_trashing():
    # Confirmation happens via
    # an in-context dialog (`delete-confirm.ts`) -- this route is
    # POST-only.
    owner = create_account("delete-confirm-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:note_delete", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 405
    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_delete_post_sets_trashed_at_and_redirects_home():
    # With no `origin` state supplied (a direct/
    # legacy POST), the fixed safe fallback is Home, never Trash.
    owner = create_account("delete-post-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Delete Post Note")

    response = authenticated_client(owner).post(reverse("notes:note_delete", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_note_delete_is_owner_scoped():
    owner = create_account("delete-scope-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Delete Scope Note")

    authenticated_client(owner).post(reverse("notes:note_delete", args=[note.id]))
    note.refresh_from_db()

    assert note.owner_id == owner.id
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_note_delete_cross_owner_is_blocked():
    owner = create_account("delete-cross-owner")
    other = create_account("delete-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(reverse("notes:note_delete", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 404
    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_delete_unauthenticated_post_is_blocked():
    owner = create_account("delete-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().post(reverse("notes:note_delete", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.trashed_at is None


# -- active-surface exclusions ------------------------------------------------


@pytest.mark.django_db
def test_home_excludes_trashed_notes():
    owner = create_account("exclude-home-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Active Note")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Note")
    services.move_note_to_trash(note=trashed_note)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "Active Note" in content
    assert "Trashed Note" not in content


@pytest.mark.django_db
def test_tree_navigation_excludes_trashed_notes():
    owner = create_account("exclude-tree-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Active In Folder")
    services.assign_note_folder(note=active_note, folder=folder)
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed In Folder")
    services.assign_note_folder(note=trashed_note, folder=folder)
    services.move_note_to_trash(note=trashed_note)

    folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)

    folder_note_titles = {n.title for n in folders[0].notes.all()}
    assert "Active In Folder" in folder_note_titles
    assert "Trashed In Folder" not in folder_note_titles


@pytest.mark.django_db
def test_tree_navigation_excludes_trashed_unfiled_notes():
    owner = create_account("exclude-tree-unfiled-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Active Unfiled")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Unfiled")
    services.move_note_to_trash(note=trashed_note)

    _folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)
    titles = {n.title for n in unfiled_notes}

    assert "Active Unfiled" in titles
    assert "Trashed Unfiled" not in titles


@pytest.mark.django_db
def test_recent_notes_excludes_trashed_notes():
    owner = create_account("exclude-recent-owner")
    current = services.create_note(owner=owner)
    active_note = services.create_note(owner=owner)
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Note 4")
    services.move_note_to_trash(note=trashed_note)

    recent = services.recent_notes_for_owner(owner=owner, exclude_note_id=current.id)

    assert active_note in recent
    assert trashed_note not in recent


@pytest.mark.django_db
def test_quick_switch_excludes_trashed_notes():
    owner = create_account("exclude-quick-switch-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Findable Note")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Findable Trashed Note")
    services.move_note_to_trash(note=trashed_note)

    results = services.search_notes_for_quick_switch(owner=owner, query="findable")

    assert active_note in results
    assert trashed_note not in results


@pytest.mark.django_db
def test_global_search_excludes_trashed_notes():
    owner = create_account("exclude-global-search-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Searchable Roadmap")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Searchable Roadmap Trashed")
    services.move_note_to_trash(note=trashed_note)

    results = services.search_notes_global(owner=owner, query="roadmap")

    assert active_note in results
    assert trashed_note not in results


# -- Trash page -----------------------------------------------------------------


@pytest.mark.django_db
def test_trash_page_lists_only_owners_trashed_notes():
    owner = create_account("trash-page-owner")
    other = create_account("trash-page-other")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="My Trashed Note")
    services.move_note_to_trash(note=trashed_note)
    other_trashed_note = services.create_note(owner=other)
    services.rename_note(note=other_trashed_note, title="Someone Elses Trashed Note")
    services.move_note_to_trash(note=other_trashed_note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "My Trashed Note" in content
    assert "Someone Elses Trashed Note" not in content


@pytest.mark.django_db
def test_trash_page_does_not_list_active_notes():
    owner = create_account("trash-page-active-owner")
    active_note = services.create_note(owner=owner)
    services.rename_note(note=active_note, title="Still Active Note")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    # Trash carries the same tree rail Home/note-
    # detail already have, so the active note legitimately appears there
    # (as it does on every other tree-having page) -- this assertion is
    # specifically about the Trash *listing* itself, not the whole page.
    trash_panel_start = content.index('class="panel panel--trash trash-panel"')
    trash_panel_end = content.index('id="workspace-drawer"')
    trash_panel = content[trash_panel_start:trash_panel_end]
    assert "Still Active Note" not in trash_panel


@pytest.mark.django_db
def test_trash_page_has_per_note_permanent_delete_control():
    # A per-Note "Delete permanently" control
    # exists on this page -- ends owner self-service access,
    # never a bulk/whole-page control, and never on Folder rows.
    owner = create_account("trash-page-purge-owner")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Note 5")
    services.move_note_to_trash(note=trashed_note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Delete permanently" in content
    assert reverse("notes:note_permanent_delete", args=[trashed_note.id]) in content


@pytest.mark.django_db
def test_trash_page_shows_empty_trash_when_eligible_item_exists():
    owner = create_account("trash-page-show-empty-owner")
    trashed_note = services.create_note(owner=owner)
    services.rename_note(note=trashed_note, title="Trashed Note 6")
    services.move_note_to_trash(note=trashed_note)

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    assert "Empty Trash" in content


@pytest.mark.django_db
def test_trash_page_hides_empty_trash_when_no_eligible_item_exists():
    owner = create_account("trash-page-hide-empty-owner")

    response = authenticated_client(owner).get(reverse("notes:trash"))
    content = response.content.decode()

    # Targets the actual Empty Trash control (an `<a ...>Empty Trash</a>`
    # link), not the bare phrase -- the in-app Help panel (embedded on
    # every page) explains the Empty Trash feature in its own prose,
    # which contains the same words outside of any such link.
    assert ">Empty Trash</a>" not in content


# -- note_restore view -----------------------------------------------------------


@pytest.mark.django_db
def test_note_restore_clears_trashed_at():
    owner = create_account("restore-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 7")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")
    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_restore_returns_note_to_its_folder():
    owner = create_account("restore-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Note 8")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert note.folder_id == folder.id


@pytest.mark.django_db
def test_note_restore_unfiled_note_stays_unfiled():
    owner = create_account("restore-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 9")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert note.folder_id is None


@pytest.mark.django_db
def test_note_restore_is_owner_scoped():
    owner = create_account("restore-scope-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 10")
    services.move_note_to_trash(note=note)

    authenticated_client(owner).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert note.owner_id == owner.id
    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_restore_cross_owner_is_blocked():
    owner = create_account("restore-cross-owner")
    other = create_account("restore-cross-other")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 11")
    services.move_note_to_trash(note=note)

    response = authenticated_client(other).post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 404
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_note_restore_unauthenticated_post_is_blocked():
    owner = create_account("restore-unauth-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 12")
    services.move_note_to_trash(note=note)

    response = Client().post(reverse("notes:note_restore", args=[note.id]))
    note.refresh_from_db()

    assert response.status_code == 302
    assert note.trashed_at is not None


# -- direct access to trashed note detail --------------------------------------


@pytest.mark.django_db
def test_note_detail_get_redirects_to_trash_when_note_is_trashed():
    owner = create_account("direct-access-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 13")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")


@pytest.mark.django_db
def test_note_detail_post_redirects_to_trash_when_note_is_trashed():
    owner = create_account("direct-access-post-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 14")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:detail", args=[note.id]),
        {"title": "New Title", "body_json": "{}", "version": note.version},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:trash")


@pytest.mark.django_db
def test_note_detail_cross_owner_access_to_trashed_note_remains_blocked():
    owner = create_account("direct-access-cross-owner")
    other = create_account("direct-access-cross-other")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 15")
    services.move_note_to_trash(note=note)

    response = authenticated_client(other).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 404
