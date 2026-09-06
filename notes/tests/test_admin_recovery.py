from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
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


# -- authorization --------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_access_is_blocked():
    response = Client().get(reverse("notes:admin_recovery"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_inactive_user_is_blocked():
    # Django's ModelBackend.get_user() already refuses to resolve an inactive
    # user from the session (request.user resolves to AnonymousUser), so this
    # is blocked at the authentication layer with the same redirect as an
    # anonymous request -- admin_required's own is_active check is defense in
    # depth for this case, not the layer that actually fires here.
    user = create_account("inactive-user", is_active=False)

    response = authenticated_client(user).get(reverse("notes:admin_recovery"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_active_non_admin_is_blocked():
    user = create_account("plain-user")

    response = authenticated_client(user).get(reverse("notes:admin_recovery"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_active_admin_succeeds():
    admin = create_admin("recovery-admin")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_existing_account_admin_view_still_works_after_helper_relocation():
    admin = create_admin("account-admin-still-works")

    response = authenticated_client(admin).get(reverse("accounts:user_list"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_existing_account_admin_view_still_blocks_non_admin():
    user = create_account("account-non-admin")

    response = authenticated_client(user).get(reverse("accounts:user_list"))

    assert response.status_code == 403


# -- lifecycle inclusion / exclusion ---------------------------------------------


@pytest.mark.django_db
def test_note_hidden_by_emptied_at_is_included():
    owner = create_account("recovery-note-emptied-owner")
    admin = create_admin("recovery-admin-note-emptied")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Emptied Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Emptied Note" in content


@pytest.mark.django_db
def test_folder_hidden_by_emptied_at_is_included():
    owner = create_account("recovery-folder-emptied-owner")
    admin = create_admin("recovery-admin-folder-emptied")
    folder = services.create_folder(owner=owner, name="Emptied Folder")
    services.move_folder_to_trash(folder=folder)
    from notes.models import Folder

    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Emptied Folder" in content


@pytest.mark.django_db
def test_note_aged_beyond_30_within_90_is_included():
    owner = create_account("recovery-note-aged-owner")
    admin = create_admin("recovery-admin-note-aged")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Aged Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Aged Note" in content


@pytest.mark.django_db
def test_folder_aged_beyond_30_within_90_is_included():
    owner = create_account("recovery-folder-aged-owner")
    admin = create_admin("recovery-admin-folder-aged")
    folder = services.create_folder(owner=owner, name="Aged Folder")
    services.move_folder_to_trash(folder=folder)
    from notes.models import Folder

    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Aged Folder" in content


@pytest.mark.django_db
def test_active_note_is_excluded():
    owner = create_account("recovery-active-note-owner")
    admin = create_admin("recovery-admin-active-note")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Active Note Not Recoverable")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Active Note Not Recoverable" not in content


@pytest.mark.django_db
def test_active_folder_is_excluded():
    owner = create_account("recovery-active-folder-owner")
    admin = create_admin("recovery-admin-active-folder")
    services.create_folder(owner=owner, name="Active Folder Not Recoverable")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Active Folder Not Recoverable" not in content


@pytest.mark.django_db
def test_owner_visible_self_restorable_note_is_excluded():
    owner = create_account("recovery-visible-note-owner")
    admin = create_admin("recovery-admin-visible-note")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Visible Trashed Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Visible Trashed Note" not in content


@pytest.mark.django_db
def test_owner_visible_self_restorable_folder_is_excluded():
    owner = create_account("recovery-visible-folder-owner")
    admin = create_admin("recovery-admin-visible-folder")
    folder = services.create_folder(owner=owner, name="Visible Trashed Folder")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Visible Trashed Folder" not in content


@pytest.mark.django_db
def test_note_older_than_90_days_is_excluded():
    owner = create_account("recovery-purge-note-owner")
    admin = create_admin("recovery-admin-purge-note")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Purge Eligible Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=91))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Purge Eligible Note" not in content


@pytest.mark.django_db
def test_folder_older_than_90_days_is_excluded():
    owner = create_account("recovery-purge-folder-owner")
    admin = create_admin("recovery-admin-purge-folder")
    folder = services.create_folder(owner=owner, name="Purge Eligible Folder")
    services.move_folder_to_trash(folder=folder)
    from notes.models import Folder

    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=91))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Purge Eligible Folder" not in content


@pytest.mark.django_db
def test_restored_note_is_excluded():
    owner = create_account("recovery-restored-note-owner")
    admin = create_admin("recovery-admin-restored-note")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Restored Note")
    services.move_note_to_trash(note=note)
    services.restore_note_from_trash(note=note)

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "Restored Note" not in content


# -- metadata correctness --------------------------------------------------------


@pytest.mark.django_db
def test_metadata_projection_fields_are_correct():
    owner = create_account("recovery-metadata-owner", display_name="Metadata Owner")
    folder = services.create_folder(owner=owner, name="Metadata Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Metadata Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    original_trashed_at = note.trashed_at
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    note.refresh_from_db()

    items = services.list_administrator_recoverable_notes()
    matching = next(item for item in items if item["title"] == "Metadata Note")

    assert matching["item_type"] == "Note"
    assert matching["owner_label"] == "Metadata Owner"
    assert matching["folder_label"] == "Metadata Folder"
    assert matching["trashed_at"] == original_trashed_at
    assert matching["emptied_at"] == note.emptied_at
    assert matching["owner_visible_expiration_at"] == original_trashed_at + timedelta(days=30)
    assert matching["final_purge_at"] == original_trashed_at + timedelta(days=90)
    assert matching["stage_label"] == "Administrator-only recovery"


@pytest.mark.django_db
def test_metadata_folder_shows_unfiled_when_note_has_no_folder():
    owner = create_account("recovery-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Unfiled Recovery Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    items = services.list_administrator_recoverable_notes()
    matching = next(item for item in items if item["title"] == "Unfiled Recovery Note")

    assert matching["folder_label"] == "Unfiled"


# -- folder note-count correctness ------------------------------------------------
#
# `list_administrator_recoverable_folders()` previously counted every note
# matching a recoverable folder's `folder_id`, with no filter on the note's
# own trashed/emptied state. A reachable, already-tested scenario (a note
# individually restored while its parent folder remains trashed; see
# `notes/tests/test_folder_trash.py` lines 517-719) could therefore inflate a
# folder's recovery note count with an active, non-recovery-eligible note.


@pytest.mark.django_db
def test_folder_note_count_includes_its_own_recoverable_note():
    """Positive-count test: establishes the intended meaning of the field
    using the simplest case, one recoverable note inside one recoverable
    folder."""
    owner = create_account("recovery-count-basic-owner")
    admin = create_admin("recovery-count-basic-admin")
    folder = services.create_folder(owner=owner, name="Count Basic Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Count Basic Note")
    services.assign_note_folder(note=note, folder=folder)

    services.move_folder_to_trash(folder=folder)
    from notes.models import Folder

    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    items = services.list_administrator_recoverable_folders()
    matching = next(item for item in items if item["title"] == "Count Basic Folder")
    assert matching["note_count"] == 1

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()
    assert "Count Basic Folder" in content


@pytest.mark.django_db
def test_folder_note_count_excludes_an_active_note_still_referencing_it():
    """Active-note-exclusion test: reproduces the confirmed cross-state
    scenario -- folder is administrator-recoverable, but the note that still
    references it (folder_id unchanged) was individually restored and is now
    active. The recovery view's note count for this folder must not include
    an active, non-recovery-eligible note, and the active note's title must
    not appear on the rendered page."""
    owner = create_account("recovery-count-active-owner")
    admin = create_admin("recovery-count-active-admin")
    folder = services.create_folder(owner=owner, name="Count Active Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Count Active Note")
    services.assign_note_folder(note=note, folder=folder)

    # Trash the folder -- this cascades trashed_at onto the currently-active
    # note, exactly as `move_folder_to_trash` is documented to do.
    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()
    assert note.trashed_at is not None

    # Construct "active note, folder_id still pointing at the trashed
    # folder" directly. corrected owner restore so it
    # no longer produces this state itself (a restored note now falls back
    # to Recovered Items instead) -- this bypasses that correction only for
    # this test's own unrelated subject, the folder note-count projection,
    # which must remain correct given this state however it arises (for
    # example, `assign_note_folder()` still never validates a target
    # folder's own trash state, an intentionally separate, unaffected edge
    # case).
    Note.objects.filter(pk=note.pk).update(trashed_at=None)
    note.refresh_from_db()
    assert note.trashed_at is None
    assert note.folder_id == folder.id

    # Age the folder into the administrator-only recovery window.
    from notes.models import Folder

    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    items = services.list_administrator_recoverable_folders()
    matching = next(item for item in items if item["title"] == "Count Active Folder")
    assert matching["note_count"] == 0

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()
    assert "Count Active Note" not in content


@pytest.mark.django_db
def test_deterministic_ordering():
    owner = create_account("recovery-order-owner")
    admin = create_admin("recovery-admin-order")
    now = timezone.now()

    later_purge_note = services.create_note(owner=owner)
    services.rename_note(note=later_purge_note, title="Later Purge")
    services.move_note_to_trash(note=later_purge_note)
    Note.objects.filter(pk=later_purge_note.pk).update(trashed_at=now - timedelta(days=40))

    earlier_purge_note = services.create_note(owner=owner)
    services.rename_note(note=earlier_purge_note, title="Earlier Purge")
    services.move_note_to_trash(note=earlier_purge_note)
    Note.objects.filter(pk=earlier_purge_note.pk).update(trashed_at=now - timedelta(days=80))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert content.index("Earlier Purge") < content.index("Later Purge")


# -- content privacy --------------------------------------------------------------


@pytest.mark.django_db
def test_secret_body_content_never_appears_in_response():
    owner = create_account("recovery-secret-owner")
    admin = create_admin("recovery-admin-secret")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Secret Bearing Note")

    body_json = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "SECRET_BODY_VALUE_XYZ"}]}
        ],
    }
    Note.objects.filter(pk=note.pk).update(
        body_json=body_json,
        body_plain_text="SECRET_PLAIN_TEXT_VALUE_XYZ",
    )
    tag = services.get_or_create_tag(owner=owner, name="SECRET_TAG_VALUE_XYZ", color="rose")
    note.refresh_from_db()
    services.assign_tag_to_note(note=note, tag=tag)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()

    assert "SECRET_BODY_VALUE_XYZ" not in content
    assert "SECRET_PLAIN_TEXT_VALUE_XYZ" not in content
    assert "SECRET_TAG_VALUE_XYZ" not in content


@pytest.mark.django_db
def test_service_projection_does_not_contain_content_keys():
    owner = create_account("recovery-projection-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Projection Check Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    items = services.list_administrator_recoverable_notes()
    matching = next(item for item in items if item["title"] == "Projection Check Note")

    forbidden_keys = {"body_json", "body_plain_text", "tags", "pinned"}
    assert forbidden_keys.isdisjoint(matching.keys())


@pytest.mark.django_db
def test_queryset_values_projection_excludes_content_fields():
    """Directly inspects the queryset's .values() field list to confirm the
    query itself never selects content-bearing columns, not merely that the
    template happens not to render them."""
    from notes.models import Note as NoteModel

    now = timezone.now()
    qs = NoteModel.objects.filter(services._hidden_but_recoverable_filter(now=now)).values(
        "title",
        "trashed_at",
        "emptied_at",
        "owner__username",
        "owner__display_name",
        "folder__name",
    )

    selected_fields = set(qs.query.values_select) | set(qs.query.annotation_select)
    assert "body_json" not in selected_fields
    assert "body_plain_text" not in selected_fields


# -- direct URL / no mutation surface --------------------------------------------


@pytest.mark.django_db
def test_folder_rows_have_a_restore_control():
    """The folder row's Restore control is now
    present alongside the note row's; no other mutation surface (Delete,
    Empty Trash) is present anywhere on this otherwise metadata-only page."""
    owner = create_account("recovery-folder-mutation-owner")
    admin = create_admin("recovery-admin-folder-mutation")
    folder = services.create_folder(owner=owner, name="Mutation Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()
    # Scope to this page's own panel content -- the shared base.html chrome
    # (account menu, sign-out form, global search, delete-confirm dialog)
    # legitimately contains forms/buttons unrelated to this page's own
    # mutation surface. Bounded at the panel's own closing `</section>`,
    # not left unbounded to the end of the document.
    panel_content = _panel_content(content)

    assert f'action="/admin/recovery/folders/{folder.id}/restore/"' in panel_content
    assert "Restore" in panel_content
    assert "Delete" not in panel_content
    assert "Empty Trash" not in panel_content


@pytest.mark.django_db
def test_note_rows_have_a_restore_control():
    """The note row's Restore control is the one
    authorized mutation surface on this otherwise metadata-only page."""
    owner = create_account("recovery-note-mutation-owner")
    admin = create_admin("recovery-admin-note-mutation")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Mutation Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    content = response.content.decode()
    panel_content = _panel_content(content)

    assert f'action="/admin/recovery/notes/{note.id}/restore/"' in panel_content
    assert "Restore" in panel_content
    assert "Delete" not in panel_content
    assert "Empty Trash" not in panel_content


# -- semantic section/row presentation ----------------------
#
# The legacy 11-column `<table>` and its `.admin-recovery__table-scroll` narrow
# workaround are gone, replaced with the same `.note-list__row`/
# `.trash-item__lifecycle` semantic anatomy already proven for owner Trash.
# These tests are subtree-scoped (never a page-wide
# count where one section's markup could satisfy an assertion meant to prove
# another's).


def _panel_content(content: str) -> str:
    # bounded at the panel's own closing `</section>`
    # rather than running unbounded to the end of the document -- the
    # shared `#delete-confirm-dialog` (`core/templates/base.html`) added a
    # trailing `<form>` after every page's own content, which an unbounded
    # slice would otherwise pick up as if it belonged to this panel.
    start = content.index('id="admin-recovery-title"')
    end = content.index("</section>", start)
    return content[start:end]


def _section_content(panel_content: str, heading: str) -> str:
    """Scopes to the `<h2>{heading}</h2>` section that immediately follows,
    up to the next `<h2>` (or end of panel) -- so an assertion made "within
    the Notes section" cannot be satisfied by markup that actually belongs
    to the Folders section, or vice versa."""
    start = panel_content.index(f"<h2>{heading}</h2>")
    remainder = panel_content[start:]
    next_heading = remainder.find("<h2>", len(f"<h2>{heading}</h2>"))
    return remainder if next_heading == -1 else remainder[:next_heading]


def _select_content(panel_content: str) -> str:
    """Scopes to the owner `<select>` element only -- so an owner-option
    assertion (label text, ordering, presence, count) cannot be satisfied
    by that same owner's name appearing in a row's own metadata line
    instead of in the dropdown itself."""
    start = panel_content.index("<select")
    end = panel_content.index("</select>", start) + len("</select>")
    return panel_content[start:end]


@pytest.mark.django_db
def test_no_legacy_table_structure_remains():
    owner = create_account("recovery-no-table-owner")
    admin = create_admin("recovery-admin-no-table")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Table Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "<table>" not in panel_content
    assert "<th>" not in panel_content
    assert "admin-recovery__table-scroll" not in panel_content


@pytest.mark.django_db
def test_type_column_label_is_not_rendered():
    """The removed table-era `Type` column/label must not return. This is
    distinct from the row-level `Note`/`Folder` type label (see
    `test_note_row_shows_note_type_label` and
    `test_folder_row_shows_folder_type_label` below) -- that label is a
    deliberate `<span class="note-list__type">`, not a bare table-cell
    value or a column named "Type"."""
    owner = create_account("recovery-no-type-owner")
    admin = create_admin("recovery-admin-no-type")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Type Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="No Type Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">Type<" not in panel_content
    assert "item_type" not in panel_content


@pytest.mark.django_db
def test_stage_field_is_not_rendered():
    owner = create_account("recovery-no-stage-owner")
    admin = create_admin("recovery-admin-no-stage")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Stage Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">Stage<" not in panel_content
    assert "Administrator-only recovery" not in panel_content


@pytest.mark.django_db
def test_global_empty_state_when_nothing_recoverable():
    admin = create_admin("recovery-admin-empty")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "Nothing to recover" in panel_content
    assert "empty-state--compact" in panel_content
    assert "<h2>Notes</h2>" not in panel_content
    assert "<h2>Folders</h2>" not in panel_content


@pytest.mark.django_db
def test_notes_section_omitted_when_only_folders_exist():
    owner = create_account("recovery-folders-only-owner")
    admin = create_admin("recovery-admin-folders-only")
    folder = services.create_folder(owner=owner, name="Folders Only Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "<h2>Folders</h2>" in panel_content
    assert "<h2>Notes</h2>" not in panel_content
    assert "empty-state--compact" not in panel_content


@pytest.mark.django_db
def test_folders_section_omitted_when_only_notes_exist():
    owner = create_account("recovery-notes-only-owner")
    admin = create_admin("recovery-admin-notes-only")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Notes Only Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "<h2>Notes</h2>" in panel_content
    assert "<h2>Folders</h2>" not in panel_content
    assert "empty-state--compact" not in panel_content


@pytest.mark.django_db
def test_folders_section_precedes_notes_section():
    # aligned to Trash's own order and the tree's
    # folders-before-notes convention (`notes_grouped_for_tree()`, used
    # identically by Home, All Notes, and Trash) -- previously the
    # opposite of both.
    owner = create_account("recovery-section-order-owner")
    admin = create_admin("recovery-admin-section-order")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Section Order Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Section Order Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert panel_content.index("<h2>Folders</h2>") < panel_content.index("<h2>Notes</h2>")


@pytest.mark.django_db
def test_owner_rendered_as_secondary_metadata_within_note_row():
    owner = create_account("recovery-owner-meta-owner", display_name="Owner Meta Display")
    admin = create_admin("recovery-admin-owner-meta")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Owner Metadata Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert '<span class="note-list__owner">Owner Meta Display</span>' in notes_section


@pytest.mark.django_db
def test_original_folder_rendered_for_note_when_available():
    owner = create_account("recovery-orig-folder-owner")
    admin = create_admin("recovery-admin-orig-folder")
    folder = services.create_folder(owner=owner, name="Original Location Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Original Location Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert (
        '<span class="note-list__location">Original folder: Original Location Folder</span>'
        in notes_section
    )


@pytest.mark.django_db
def test_original_folder_falls_back_to_unfiled_when_unavailable():
    owner = create_account("recovery-no-orig-folder-owner")
    admin = create_admin("recovery-admin-no-orig-folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Original Folder Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert '<span class="note-list__location">Unfiled</span>' in notes_section


@pytest.mark.django_db
def test_folder_restore_count_rendered_only_for_folder_rows():
    """The row-visible count is `associated_
    restorable_note_count` (the count Restore actually acts on), not the
    old, broader `note_count` -- and it is folder-only, never rendered on
    a note row."""
    owner = create_account("recovery-count-render-owner")
    admin = create_admin("recovery-admin-count-render")
    folder = services.create_folder(owner=owner, name="Count Render Folder")
    inner_note = services.create_note(owner=owner)
    services.rename_note(note=inner_note, title="Count Render Inner Note")
    services.assign_note_folder(note=inner_note, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))
    Note.objects.filter(pk=inner_note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    lone_note = services.create_note(owner=owner)
    services.rename_note(note=lone_note, title="Count Render Lone Note")
    services.move_note_to_trash(note=lone_note)
    Note.objects.filter(pk=lone_note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")
    notes_section = _section_content(panel_content, "Notes")

    assert '<span class="note-list__restore-count">Restores 1 note</span>' in folders_section
    assert "note-list__restore-count" not in notes_section
    # The old, broader "N recoverable note(s)" wording/class is gone from
    # the row entirely, not merely relabeled.
    assert "recoverable note" not in folders_section
    assert "note-list__count" not in panel_content


@pytest.mark.django_db
def test_folder_restore_count_zero_wording():
    owner = create_account("recovery-count-zero-owner")
    admin = create_admin("recovery-admin-count-zero")
    folder = services.create_folder(owner=owner, name="Count Zero Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert '<span class="note-list__restore-count">Restores 0 notes</span>' in folders_section


@pytest.mark.django_db
def test_folder_restore_count_plural_wording():
    owner = create_account("recovery-count-plural-owner")
    admin = create_admin("recovery-admin-count-plural")
    folder = services.create_folder(owner=owner, name="Count Plural Folder")
    note_a = services.create_note(owner=owner)
    services.rename_note(note=note_a, title="Count Plural Note A")
    services.assign_note_folder(note=note_a, folder=folder)
    note_b = services.create_note(owner=owner)
    services.rename_note(note=note_b, title="Count Plural Note B")
    services.assign_note_folder(note=note_b, folder=folder)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))
    Note.objects.filter(pk__in=[note_a.pk, note_b.pk]).update(
        trashed_at=timezone.now() - timedelta(days=45)
    )

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert '<span class="note-list__restore-count">Restores 2 notes</span>' in folders_section


@pytest.mark.django_db
def test_folder_restore_count_differs_from_broader_note_count():
    """Direct regression coverage for the exact gap this test targets:
    a folder whose broader `note_count` (notes currently referencing
    it that also happen to be individually recoverable) differs from its
    `associated_restorable_note_count` (notes carrying this folder's own
    trash-cascade marker) must render the latter, not the former, and
    must never present the broader count as if it were the actionable
    one. Constructed the same way `test_folder_trash.py` already proves a
    note trashed *before* its folder keeps no marker: the note is trashed
    independently while the folder is still active, so the later folder
    cascade (which only marks notes it newly sweeps in) never touches it,
    yet it still references the folder and is itself independently
    administrator-recoverable."""
    owner = create_account("recovery-count-differ-owner")
    admin = create_admin("recovery-admin-count-differ")
    folder = services.create_folder(owner=owner, name="Count Differ Folder")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Count Differ Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    assert note.trashed_via_folder_id is None

    services.move_folder_to_trash(folder=folder)
    note.refresh_from_db()
    assert note.trashed_via_folder_id is None

    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    items = services.list_administrator_recoverable_folders()
    matching = next(item for item in items if item["title"] == "Count Differ Folder")
    assert matching["note_count"] == 1
    assert matching["associated_restorable_note_count"] == 0

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert '<span class="note-list__restore-count">Restores 0 notes</span>' in folders_section
    assert "1 recoverable note" not in folders_section
    assert "Restores 1 note" not in folders_section


@pytest.mark.django_db
def test_lifecycle_labels_rendered_for_note_row():
    owner = create_account("recovery-note-labels-owner")
    admin = create_admin("recovery-admin-note-labels")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Lifecycle Labels Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    for label in ("Moved to Trash", "Emptied", "Left owner Trash", "Final purge"):
        assert f"<dt>{label}</dt>" in notes_section


@pytest.mark.django_db
def test_lifecycle_labels_rendered_for_folder_row():
    owner = create_account("recovery-folder-labels-owner")
    admin = create_admin("recovery-admin-folder-labels")
    folder = services.create_folder(owner=owner, name="Lifecycle Labels Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    for label in ("Moved to Trash", "Emptied", "Left owner Trash", "Final purge"):
        assert f"<dt>{label}</dt>" in folders_section


@pytest.mark.django_db
def test_emptied_value_renders_plain_hyphen_when_not_emptied():
    owner = create_account("recovery-not-emptied-owner")
    admin = create_admin("recovery-admin-not-emptied")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Not Emptied Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    emptied_label_index = notes_section.index("<dt>Emptied</dt>")
    dd_start = notes_section.index("<dd>", emptied_label_index)
    dd_end = notes_section.index("</dd>", dd_start)
    assert notes_section[dd_start : dd_end + len("</dd>")] == "<dd>-</dd>"


@pytest.mark.django_db
def test_final_purge_is_the_only_emphasized_lifecycle_entry():
    owner = create_account("recovery-emphasis-owner")
    admin = create_admin("recovery-admin-emphasis")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Emphasis Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Emphasis Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    # Exactly one emphasized entry per row (one note row + one folder row).
    assert panel_content.count("trash-item__lifecycle-entry--emphasis") == 2
    notes_section = _section_content(panel_content, "Notes")
    # The emphasis div opens immediately before <dt>Final purge</dt> and
    # after the (non-emphasized) Left owner Trash entry closes --
    # confirming the emphasis class lands on that one entry, not Moved to
    # Trash/Emptied/Left owner Trash.
    left_trash_index = notes_section.index("<dt>Left owner Trash</dt>")
    emphasis_index = notes_section.index("trash-item__lifecycle-entry--emphasis")
    final_purge_index = notes_section.index("<dt>Final purge</dt>")
    assert left_trash_index < emphasis_index < final_purge_index


@pytest.mark.django_db
def test_exactly_one_restore_form_per_note_row():
    owner = create_account("recovery-one-restore-note-owner")
    admin = create_admin("recovery-admin-one-restore-note")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="One Restore Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert notes_section.count(f'action="/admin/recovery/notes/{note.id}/restore/"') == 1
    assert notes_section.count("<form") == 1
    assert 'method="post"' in notes_section
    assert "csrfmiddlewaretoken" in notes_section


@pytest.mark.django_db
def test_exactly_one_restore_form_per_folder_row():
    owner = create_account("recovery-one-restore-folder-owner")
    admin = create_admin("recovery-admin-one-restore-folder")
    folder = services.create_folder(owner=owner, name="One Restore Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    full_content = response.content.decode()
    panel_content = _panel_content(full_content)
    folders_section = _section_content(panel_content, "Folders")

    # the folder row's Restore control became a
    # dialog-opening button (`data-restore-trigger`) carrying its target
    # route as `data-restore-action` rather than an inline per-row form --
    # the actual `<form>`/CSRF token now live once in the shared dialog in
    # `base.html`, outside the panel's own `</section>` boundary (see
    # `_panel_content`'s own comment), so the CSRF check below is against
    # the full page, not the panel-scoped slice.
    assert (
        folders_section.count(f'data-restore-action="/admin/recovery/folders/{folder.id}/restore/"')
        == 1
    )
    assert folders_section.count("<form") == 0
    assert "csrfmiddlewaretoken" in full_content


@pytest.mark.django_db
def test_long_note_title_still_renders_lifecycle_metadata():
    owner = create_account("recovery-long-title-owner")
    admin = create_admin("recovery-admin-long-title")
    note = services.create_note(owner=owner)
    long_title = ("A very long note title " * 10).strip()
    services.rename_note(note=note, title=long_title)
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert long_title in notes_section
    assert "<dt>Final purge</dt>" in notes_section


@pytest.mark.django_db
def test_long_folder_name_still_renders_lifecycle_metadata():
    owner = create_account("recovery-long-folder-owner")
    admin = create_admin("recovery-admin-long-folder")
    long_name = "A" * 120
    folder = services.create_folder(owner=owner, name=long_name)
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert long_name in folders_section
    assert "<dt>Final purge</dt>" in folders_section


@pytest.mark.django_db
def test_no_duplicate_alternate_layout_markup():
    owner = create_account("recovery-no-duplicate-owner")
    admin = create_admin("recovery-admin-no-duplicate")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Duplicate Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert panel_content.count("No Duplicate Note") == 1
    assert panel_content.count(f'data-note-id="{note.id}"') == 1


@pytest.mark.django_db
def test_admin_recovery_query_count_does_not_scale_with_item_count():
    owner = create_account("recovery-query-count-owner")
    admin = create_admin("recovery-admin-query-count")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Query Count Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    client = authenticated_client(admin)
    with CaptureQueriesContext(connection) as baseline:
        response = client.get(reverse("notes:admin_recovery"))
    assert response.status_code == 200
    baseline_count = len(baseline.captured_queries)

    for i in range(8):
        extra_note = services.create_note(owner=owner)
        services.rename_note(note=extra_note, title="Extra Note 2")
        services.move_note_to_trash(note=extra_note)
        Note.objects.filter(pk=extra_note.pk).update(emptied_at=timezone.now())
        extra_folder = services.create_folder(owner=owner, name=f"Query Count Folder {i}")
        services.move_folder_to_trash(folder=extra_folder)
        Folder.objects.filter(pk=extra_folder.pk).update(emptied_at=timezone.now())

    with CaptureQueriesContext(connection) as scaled:
        response = client.get(reverse("notes:admin_recovery"))
    assert response.status_code == 200
    scaled_count = len(scaled.captured_queries)

    assert scaled_count == baseline_count


# -- folder-name fidelity ---------------------------------------------------------


@pytest.mark.django_db
def test_folder_name_fidelity_shows_current_name_not_original():
    owner = create_account("recovery-fidelity-owner")
    admin = create_admin("recovery-admin-fidelity")
    folder = services.create_folder(owner=owner, name="Original Folder Name")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Fidelity Test Note")
    services.assign_note_folder(note=note, folder=folder)
    services.move_note_to_trash(note=note)
    services.rename_folder(folder=folder, name="Renamed Folder Name")
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert (
        '<span class="note-list__location">Original folder: Renamed Folder Name</span>'
        in notes_section
    )
    assert "Original Folder Name" not in notes_section


# -- cross-owner attribution ------------------------------------------------------


@pytest.mark.django_db
def test_multiple_owners_correctly_attributed():
    owner_a = create_account("recovery-multi-owner-a", display_name="Owner A")
    owner_b = create_account("recovery-multi-owner-b", display_name="Owner B")

    note_a = services.create_note(owner=owner_a)
    services.rename_note(note=note_a, title="Owner A Note")
    services.move_note_to_trash(note=note_a)
    Note.objects.filter(pk=note_a.pk).update(emptied_at=timezone.now())

    note_b = services.create_note(owner=owner_b)
    services.rename_note(note=note_b, title="Owner B Note")
    services.move_note_to_trash(note=note_b)
    Note.objects.filter(pk=note_b.pk).update(emptied_at=timezone.now())

    items = services.list_administrator_recoverable_notes()
    a_item = next(item for item in items if item["title"] == "Owner A Note")
    b_item = next(item for item in items if item["title"] == "Owner B Note")

    assert a_item["owner_label"] == "Owner A"
    assert b_item["owner_label"] == "Owner B"


# -- row-level type label and plain-hyphen punctuation --


@pytest.mark.django_db
def test_note_row_shows_note_type_label():
    owner = create_account("recovery-note-type-owner")
    admin = create_admin("recovery-admin-note-type")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note Type Label Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert '<span class="note-list__type">Note</span>' in notes_section


@pytest.mark.django_db
def test_folder_row_shows_folder_type_label():
    owner = create_account("recovery-folder-type-owner")
    admin = create_admin("recovery-admin-folder-type")
    folder = services.create_folder(owner=owner, name="Folder Type Label Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert '<span class="note-list__type">Folder</span>' in folders_section


@pytest.mark.django_db
def test_note_rows_do_not_contain_folder_type_label():
    owner = create_account("recovery-note-no-folder-type-owner")
    admin = create_admin("recovery-admin-note-no-folder-type")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Folder Type Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="No Folder Type Sibling Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert '<span class="note-list__type">Folder</span>' not in notes_section
    assert '<span class="note-list__type">Note</span>' in notes_section


@pytest.mark.django_db
def test_folder_rows_do_not_contain_note_type_label():
    owner = create_account("recovery-folder-no-note-type-owner")
    admin = create_admin("recovery-admin-folder-no-note-type")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Note Type Sibling Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="No Note Type Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    folders_section = _section_content(panel_content, "Folders")

    assert '<span class="note-list__type">Note</span>' not in folders_section
    assert '<span class="note-list__type">Folder</span>' in folders_section


@pytest.mark.django_db
def test_row_level_type_labels_are_scoped_per_item():
    """Each row's own type label lives inside that row's own <li>, not
    merely somewhere in its section -- confirms the label is genuinely
    row-level, not a single label shared across a section's items."""
    owner = create_account("recovery-type-scope-owner")
    admin = create_admin("recovery-admin-type-scope")
    note_a = services.create_note(owner=owner)
    services.rename_note(note=note_a, title="Type Scope Note A")
    services.move_note_to_trash(note=note_a)
    Note.objects.filter(pk=note_a.pk).update(emptied_at=timezone.now())
    note_b = services.create_note(owner=owner)
    services.rename_note(note=note_b, title="Type Scope Note B")
    services.move_note_to_trash(note=note_b)
    Note.objects.filter(pk=note_b.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    assert notes_section.count('<span class="note-list__type">Note</span>') == 2


@pytest.mark.django_db
def test_type_label_is_grouped_with_title_as_primary_information():
    """The type label sits in the same title-row
    as the item's own name (primary tier), no longer folded into the
    secondary metadata line alongside owner/location text -- confirmed
    by exact adjacency in the row's own markup, not merely presence
    somewhere in the section."""
    owner = create_account("recovery-type-primary-owner")
    admin = create_admin("recovery-admin-type-primary")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Type Primary Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Type Primary Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")
    folders_section = _section_content(panel_content, "Folders")

    # Both the type label and the title live inside the same title-row,
    # with nothing else from the row's own markup between them.
    for section, type_text, title_text in (
        (notes_section, "Note", "Type Primary Note"),
        (folders_section, "Folder", "Type Primary Folder"),
    ):
        title_row_start = section.index('<div class="note-list__title-row">')
        title_row_end = section.index("</div>", title_row_start)
        title_row_markup = section[title_row_start:title_row_end]
        assert f'<span class="note-list__type">{type_text}</span>' in title_row_markup
        assert f'<span class="note-list__title">{title_text}</span>' in title_row_markup

    # The secondary metadata line no longer contains the type text.
    notes_meta_start = notes_section.index('<p class="note-list__meta">')
    notes_meta_end = notes_section.index("</p>", notes_meta_start)
    assert "note-list__type" not in notes_section[notes_meta_start:notes_meta_end]


@pytest.mark.django_db
def test_row_content_order_is_type_title_then_meta_then_lifecycle_then_actions():
    """Keyboard/screen-reader traversal follows
    source order, not CSS -- this pins that order directly so a future
    change cannot silently reverse it. Restore must follow, never
    precede, the row's own content."""
    owner = create_account("recovery-order-primary-owner")
    admin = create_admin("recovery-admin-order-primary")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Row Order Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    title_row_index = notes_section.index('<div class="note-list__title-row">')
    meta_index = notes_section.index('<p class="note-list__meta">')
    lifecycle_index = notes_section.index('<dl class="trash-item__lifecycle">')
    actions_index = notes_section.index('<div class="note-list__actions">')
    assert title_row_index < meta_index < lifecycle_index < actions_index


@pytest.mark.django_db
def test_em_dash_absent_from_administrator_recovery():
    owner = create_account("recovery-no-em-dash-owner")
    admin = create_admin("recovery-admin-no-em-dash")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Em Dash Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))
    folder = services.create_folder(owner=owner, name="No Em Dash Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "—" not in panel_content
    assert "&mdash;" not in panel_content


@pytest.mark.django_db
def test_missing_emptied_value_uses_plain_hyphen():
    owner = create_account("recovery-plain-hyphen-owner")
    admin = create_admin("recovery-admin-plain-hyphen")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Plain Hyphen Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=45))

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")

    emptied_label_index = notes_section.index("<dt>Emptied</dt>")
    dd_start = notes_section.index("<dd>", emptied_label_index)
    dd_end = notes_section.index("</dd>", dd_start)
    assert notes_section[dd_start : dd_end + len("</dd>")] == "<dd>-</dd>"


@pytest.mark.django_db
def test_secondary_metadata_separators_use_plain_hyphen():
    owner = create_account("recovery-sep-hyphen-owner")
    admin = create_admin("recovery-admin-sep-hyphen")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Separator Hyphen Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="Separator Hyphen Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")
    folders_section = _section_content(panel_content, "Folders")

    # The type label lives in the title row, not the secondary
    # metadata line, leaving exactly one separator
    # between owner and the row's one remaining contextual fact
    # (location for notes, restore count for folders) -- a single
    # separator, not two, since type/owner/location-or-count no longer
    # all share one line.
    assert notes_section.count('<span class="note-list__meta-sep" aria-hidden="true">-</span>') == 1
    assert (
        folders_section.count('<span class="note-list__meta-sep" aria-hidden="true">-</span>') == 1
    )
    assert "&middot;" not in panel_content


@pytest.mark.django_db
def test_no_table_structure_returns():
    owner = create_account("recovery-no-table-return-owner")
    admin = create_admin("recovery-admin-no-table-return")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="No Table Return Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "<table>" not in panel_content
    assert "<th>" not in panel_content


@pytest.mark.django_db
def test_row_markup_has_one_shared_dom_anatomy():
    """A row's markup shape (note-list__row/content/actions) is identical
    regardless of section -- confirms there is no
    second, type-specific DOM anatomy."""
    owner = create_account("recovery-one-anatomy-owner")
    admin = create_admin("recovery-admin-one-anatomy")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="One Anatomy Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    folder = services.create_folder(owner=owner, name="One Anatomy Folder")
    services.move_folder_to_trash(folder=folder)
    Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())
    notes_section = _section_content(panel_content, "Notes")
    folders_section = _section_content(panel_content, "Folders")

    for section in (notes_section, folders_section):
        assert section.count('<div class="note-list__row">') == 1
        assert section.count('<div class="note-list__content">') == 1
        assert section.count('<div class="note-list__actions">') == 1


# -- owner filtering and ordering  --------------


def _make_recoverable_note(owner, title, *, emptied=True, aged_days=None):
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title=title)
    services.move_note_to_trash(note=note)
    if emptied:
        Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())
    if aged_days is not None:
        Note.objects.filter(pk=note.pk).update(
            trashed_at=timezone.now() - timedelta(days=aged_days)
        )
    note.refresh_from_db()
    return note


def _make_recoverable_folder(owner, name, *, emptied=True, aged_days=None):
    folder = services.create_folder(owner=owner, name=name)
    services.move_folder_to_trash(folder=folder)
    if emptied:
        Folder.objects.filter(pk=folder.pk).update(emptied_at=timezone.now())
    if aged_days is not None:
        Folder.objects.filter(pk=folder.pk).update(
            trashed_at=timezone.now() - timedelta(days=aged_days)
        )
    folder.refresh_from_db()
    return folder


# -- default state ------------------------------------------------------------------


@pytest.mark.django_db
def test_default_state_shows_all_owners_selected():
    owner = create_account("recovery-default-owner")
    admin = create_admin("recovery-admin-default")
    _make_recoverable_note(owner, "Default State Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert '<option value="" selected>All owners</option>' in panel_content


@pytest.mark.django_db
def test_default_state_no_clear_filter_link():
    owner = create_account("recovery-default-noclear-owner")
    admin = create_admin("recovery-admin-default-noclear")
    _make_recoverable_note(owner, "Default No Clear Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "Clear filter" not in panel_content


@pytest.mark.django_db
def test_owner_filter_is_not_persisted_in_session():
    owner_a = create_account("recovery-session-a-owner", display_name="Session Owner A")
    owner_b = create_account("recovery-session-b-owner", display_name="Session Owner B")
    admin = create_admin("recovery-admin-session")
    _make_recoverable_note(owner_a, "Session Note A")
    _make_recoverable_note(owner_b, "Session Note B")

    client = authenticated_client(admin)
    first = client.get(reverse("notes:admin_recovery") + f"?owner={owner_a.id}")
    assert "Session Note A" in first.content.decode()
    assert "Session Note B" not in first.content.decode()

    second = client.get(reverse("notes:admin_recovery"))
    second_content = second.content.decode()
    assert "Session Note A" in second_content
    assert "Session Note B" in second_content
    assert '<option value="" selected>All owners</option>' in second_content


# -- owner options --------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_with_notes_only_appears_in_options():
    owner = create_account("recovery-opt-notes-only-owner", display_name="Notes Only Owner")
    admin = create_admin("recovery-admin-opt-notes-only")
    _make_recoverable_note(owner, "Opt Notes Only Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    select_content = _select_content(_panel_content(response.content.decode()))

    assert "Notes Only Owner" in select_content


@pytest.mark.django_db
def test_owner_with_folders_only_appears_in_options():
    owner = create_account("recovery-opt-folders-only-owner", display_name="Folders Only Owner")
    admin = create_admin("recovery-admin-opt-folders-only")
    _make_recoverable_folder(owner, "Opt Folders Only Folder")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    select_content = _select_content(_panel_content(response.content.decode()))

    assert "Folders Only Owner" in select_content


@pytest.mark.django_db
def test_owner_with_both_appears_once_in_options():
    owner = create_account("recovery-opt-both-owner", display_name="Both Owner")
    admin = create_admin("recovery-admin-opt-both")
    _make_recoverable_note(owner, "Opt Both Note")
    _make_recoverable_folder(owner, "Opt Both Folder")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    select_content = _select_content(_panel_content(response.content.decode()))

    assert select_content.count("Both Owner") == 1


@pytest.mark.django_db
def test_owner_with_no_recoverable_items_absent_from_options():
    owner_with_items = create_account("recovery-opt-has-items-owner", display_name="Has Items")
    owner_without_items = create_account(
        "recovery-opt-no-items-owner", display_name="No Items Owner"
    )
    admin = create_admin("recovery-admin-opt-absent")
    _make_recoverable_note(owner_with_items, "Opt Absent Note")
    services.create_note(owner=owner_without_items)  # active, not recoverable

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">No Items Owner<" not in panel_content


@pytest.mark.django_db
def test_owner_option_label_uses_display_name_and_username_when_they_differ():
    owner = create_account("recovery-label-differ-owner", display_name="Differing Display Name")
    admin = create_admin("recovery-admin-label-differ")
    _make_recoverable_note(owner, "Label Differ Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">Differing Display Name (recovery-label-differ-owner)<" in panel_content


@pytest.mark.django_db
def test_owner_option_label_uses_username_only_when_no_display_name():
    owner = create_account("recovery-label-nodisplay-owner", display_name="")
    admin = create_admin("recovery-admin-label-nodisplay")
    _make_recoverable_note(owner, "Label No Display Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">recovery-label-nodisplay-owner<" in panel_content
    assert "(recovery-label-nodisplay-owner)" not in panel_content


@pytest.mark.django_db
def test_owner_option_label_uses_username_only_when_identical_to_display_name():
    owner = create_account(
        "recovery-label-identical-owner", display_name="recovery-label-identical-owner"
    )
    admin = create_admin("recovery-admin-label-identical")
    _make_recoverable_note(owner, "Label Identical Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert ">recovery-label-identical-owner<" in panel_content
    assert "(recovery-label-identical-owner)" not in panel_content


@pytest.mark.django_db
def test_owner_option_label_does_not_expose_email():
    owner = create_account(
        "recovery-label-email-owner", display_name="Email Owner", email="secret@example.com"
    )
    admin = create_admin("recovery-admin-label-email")
    _make_recoverable_note(owner, "Label Email Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "secret@example.com" not in panel_content


@pytest.mark.django_db
def test_owner_options_ordered_alphabetically_by_label():
    owner_z = create_account("recovery-order-z-owner", display_name="Zeta Owner")
    owner_a = create_account("recovery-order-a-owner", display_name="Alpha Owner")
    admin = create_admin("recovery-admin-order")
    _make_recoverable_note(owner_z, "Order Zeta Note")
    _make_recoverable_note(owner_a, "Order Alpha Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    select_content = _select_content(_panel_content(response.content.decode()))

    assert select_content.index("Alpha Owner") < select_content.index("Zeta Owner")


@pytest.mark.django_db
def test_no_duplicate_owner_options():
    owner = create_account("recovery-no-dup-opt-owner", display_name="No Dup Owner")
    admin = create_admin("recovery-admin-no-dup-opt")
    _make_recoverable_note(owner, "No Dup Note 1")
    _make_recoverable_note(owner, "No Dup Note 2")
    _make_recoverable_folder(owner, "No Dup Folder")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    select_content = _select_content(_panel_content(response.content.decode()))

    assert select_content.count("No Dup Owner") == 1


# -- filtering --------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_owner_filters_notes_and_folders_together():
    owner_a = create_account("recovery-filter-a-owner", display_name="Filter Owner A")
    owner_b = create_account("recovery-filter-b-owner", display_name="Filter Owner B")
    admin = create_admin("recovery-admin-filter-together")
    _make_recoverable_note(owner_a, "Filter A Note")
    _make_recoverable_folder(owner_a, "Filter A Folder")
    _make_recoverable_note(owner_b, "Filter B Note")
    _make_recoverable_folder(owner_b, "Filter B Folder")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner_a.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "Filter A Note" in panel_content
    assert "Filter A Folder" in panel_content
    assert "Filter B Note" not in panel_content
    assert "Filter B Folder" not in panel_content


@pytest.mark.django_db
def test_selected_owner_option_marked_selected():
    owner = create_account("recovery-selected-owner", display_name="Selected Owner")
    admin = create_admin("recovery-admin-selected")
    _make_recoverable_note(owner, "Selected Owner Note")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert f'value="{owner.id}" selected' in panel_content
    assert '<option value="" selected>' not in panel_content


@pytest.mark.django_db
def test_clear_filter_shown_when_valid_owner_active():
    owner = create_account("recovery-clear-shown-owner")
    admin = create_admin("recovery-admin-clear-shown")
    _make_recoverable_note(owner, "Clear Shown Note")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "Clear filter" in panel_content
    assert f'href="{reverse("notes:admin_recovery")}"' in panel_content


@pytest.mark.django_db
def test_owner_with_notes_only_omits_folders_section_when_filtered():
    owner = create_account("recovery-filtered-notes-only-owner")
    admin = create_admin("recovery-admin-filtered-notes-only")
    _make_recoverable_note(owner, "Filtered Notes Only Note")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "<h2>Notes</h2>" in panel_content
    assert "<h2>Folders</h2>" not in panel_content


@pytest.mark.django_db
def test_owner_with_folders_only_omits_notes_section_when_filtered():
    owner = create_account("recovery-filtered-folders-only-owner")
    admin = create_admin("recovery-admin-filtered-folders-only")
    _make_recoverable_folder(owner, "Filtered Folders Only Folder")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "<h2>Folders</h2>" in panel_content
    assert "<h2>Notes</h2>" not in panel_content


@pytest.mark.django_db
def test_owner_with_both_renders_both_sections_when_filtered():
    owner = create_account("recovery-filtered-both-owner")
    admin = create_admin("recovery-admin-filtered-both")
    _make_recoverable_note(owner, "Filtered Both Note")
    _make_recoverable_folder(owner, "Filtered Both Folder")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "<h2>Notes</h2>" in panel_content
    assert "<h2>Folders</h2>" in panel_content


@pytest.mark.django_db
def test_final_purge_ordering_preserved_when_filtered():
    owner = create_account("recovery-filtered-order-owner")
    admin = create_admin("recovery-admin-filtered-order")
    now = timezone.now()

    later_purge = _make_recoverable_note(owner, "Filtered Later Purge", emptied=False)
    Note.objects.filter(pk=later_purge.pk).update(trashed_at=now - timedelta(days=40))
    earlier_purge = _make_recoverable_note(owner, "Filtered Earlier Purge", emptied=False)
    Note.objects.filter(pk=earlier_purge.pk).update(trashed_at=now - timedelta(days=80))

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    content = response.content.decode()

    assert content.index("Filtered Earlier Purge") < content.index("Filtered Later Purge")


# -- invalid and stale owner values -------------------------------------------------


@pytest.mark.django_db
def test_non_integer_owner_falls_back_to_all_owners():
    owner = create_account("recovery-noninteger-owner")
    admin = create_admin("recovery-admin-noninteger")
    _make_recoverable_note(owner, "Noninteger Note")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + "?owner=not-a-number"
    )

    assert response.status_code == 200
    panel_content = _panel_content(response.content.decode())
    assert '<option value="" selected>All owners</option>' in panel_content
    assert "Clear filter" not in panel_content
    assert "not-a-number" not in panel_content


@pytest.mark.django_db
def test_nonexistent_owner_id_falls_back_to_all_owners():
    owner = create_account("recovery-nonexistent-owner")
    admin = create_admin("recovery-admin-nonexistent")
    _make_recoverable_note(owner, "Nonexistent Owner Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery") + "?owner=99999999")

    assert response.status_code == 200
    panel_content = _panel_content(response.content.decode())
    assert '<option value="" selected>All owners</option>' in panel_content
    assert "Clear filter" not in panel_content


@pytest.mark.django_db
def test_existing_owner_with_no_eligible_items_falls_back_to_all_owners():
    ineligible_owner = create_account("recovery-ineligible-owner")
    other_owner = create_account("recovery-ineligible-other-owner")
    admin = create_admin("recovery-admin-ineligible")
    services.create_note(owner=ineligible_owner)  # active, not recoverable
    _make_recoverable_note(other_owner, "Ineligible Other Note")

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={ineligible_owner.id}"
    )

    assert response.status_code == 200
    panel_content = _panel_content(response.content.decode())
    assert '<option value="" selected>All owners</option>' in panel_content
    assert "Clear filter" not in panel_content
    assert "Ineligible Other Note" in panel_content


@pytest.mark.django_db
def test_stale_owner_after_final_item_restored_falls_back_to_all_owners():
    owner = create_account("recovery-stale-owner")
    admin = create_admin("recovery-admin-stale")
    note = _make_recoverable_note(owner, "Stale Owner Note")

    services.restore_note_for_administrator(note_id=note.id, actor=admin)

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )

    assert response.status_code == 200
    panel_content = _panel_content(response.content.decode())
    assert '<option value="" selected>All owners</option>' in panel_content
    assert "Clear filter" not in panel_content


# -- empty states ------------------------------------------------------------------


@pytest.mark.django_db
def test_filtered_empty_copy_shown_for_internally_consistent_race_state(monkeypatch):
    owner = create_account("recovery-race-empty-owner", display_name="Race Empty Owner")
    admin = create_admin("recovery-admin-race-empty")
    _make_recoverable_note(owner, "Race Empty Note")

    def empty_notes(*, owner_id=None):
        return []

    def empty_folders(*, owner_id=None):
        return []

    monkeypatch.setattr(services, "list_administrator_recoverable_notes", empty_notes)
    monkeypatch.setattr(services, "list_administrator_recoverable_folders", empty_folders)

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    panel_content = _panel_content(response.content.decode())

    assert "No recoverable items for this owner." in panel_content
    assert "Clear filter" in panel_content


@pytest.mark.django_db
def test_filtered_empty_copy_not_shown_for_invalid_owner():
    owner = create_account("recovery-invalid-not-filtered-empty-owner")
    admin = create_admin("recovery-admin-invalid-not-filtered-empty")
    _make_recoverable_note(owner, "Invalid Not Filtered Empty Note")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery") + "?owner=99999999")
    panel_content = _panel_content(response.content.decode())

    assert "No recoverable items for this owner." not in panel_content
    assert "Invalid Not Filtered Empty Note" in panel_content


# -- URL and control semantics -------------------------------------------------------


@pytest.mark.django_db
def test_filter_form_is_get_method_with_owner_parameter():
    admin = create_admin("recovery-admin-form-get")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert '<form class="admin-recovery-filter-form" method="get"' in panel_content
    assert 'name="owner"' in panel_content
    assert '<label for="admin-recovery-owner-select"' in panel_content
    assert 'id="admin-recovery-owner-select"' in panel_content


@pytest.mark.django_db
def test_apply_button_present():
    admin = create_admin("recovery-admin-apply-present")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "Apply" in panel_content


@pytest.mark.django_db
def test_no_javascript_dependency_in_filter_form():
    admin = create_admin("recovery-admin-no-js")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert "onchange" not in panel_content
    assert "onsubmit" not in panel_content


@pytest.mark.django_db
def test_no_duplicate_filter_controls():
    admin = create_admin("recovery-admin-no-dup-control")

    response = authenticated_client(admin).get(reverse("notes:admin_recovery"))
    panel_content = _panel_content(response.content.decode())

    assert panel_content.count("admin-recovery-owner-select") == 2  # label `for` + select `id`
    assert panel_content.count("<select") == 1


# -- Restore filter preservation -----------------------------------------------------


@pytest.mark.django_db
def test_successful_note_restore_preserves_owner_filter():
    owner = create_account("recovery-restore-preserve-note-owner")
    admin = create_admin("recovery-admin-restore-preserve-note")
    note = _make_recoverable_note(owner, "Restore Preserve Note")

    response = authenticated_client(admin).post(
        reverse("notes:admin_note_restore", args=[note.id]), {"owner": owner.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery") + f"?owner={owner.id}"


@pytest.mark.django_db
def test_successful_folder_restore_preserves_owner_filter():
    owner = create_account("recovery-restore-preserve-folder-owner")
    admin = create_admin("recovery-admin-restore-preserve-folder")
    folder = _make_recoverable_folder(owner, "Restore Preserve Folder")

    response = authenticated_client(admin).post(
        reverse("notes:admin_folder_restore", args=[folder.id]), {"owner": owner.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery") + f"?owner={owner.id}"


@pytest.mark.django_db
def test_note_race_ineligible_redirect_preserves_owner():
    owner = create_account("recovery-race-note-owner")
    admin = create_admin("recovery-admin-race-note")
    note = _make_recoverable_note(owner, "Race Note")
    client = authenticated_client(admin)
    client.post(reverse("notes:admin_note_restore", args=[note.id]), {"owner": owner.id})

    response = client.post(reverse("notes:admin_note_restore", args=[note.id]), {"owner": owner.id})

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery") + f"?owner={owner.id}"


@pytest.mark.django_db
def test_folder_race_ineligible_redirect_preserves_owner():
    owner = create_account("recovery-race-folder-owner")
    admin = create_admin("recovery-admin-race-folder")
    folder = _make_recoverable_folder(owner, "Race Folder")
    client = authenticated_client(admin)
    client.post(reverse("notes:admin_folder_restore", args=[folder.id]), {"owner": owner.id})

    response = client.post(
        reverse("notes:admin_folder_restore", args=[folder.id]), {"owner": owner.id}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery") + f"?owner={owner.id}"


@pytest.mark.django_db
def test_restoring_final_item_falls_back_to_all_owners_at_destination():
    owner = create_account("recovery-final-item-owner")
    admin = create_admin("recovery-admin-final-item")
    note = _make_recoverable_note(owner, "Final Item Note")
    client = authenticated_client(admin)

    redirect_response = client.post(
        reverse("notes:admin_note_restore", args=[note.id]), {"owner": owner.id}
    )
    assert redirect_response.url == reverse("notes:admin_recovery") + f"?owner={owner.id}"

    destination = client.get(redirect_response.url)
    panel_content = _panel_content(destination.content.decode())

    assert '<option value="" selected>All owners</option>' in panel_content
    assert "Clear filter" not in panel_content


@pytest.mark.django_db
def test_invalid_raw_owner_field_not_propagated_on_restore():
    owner = create_account("recovery-invalid-raw-owner")
    admin = create_admin("recovery-admin-invalid-raw")
    note = _make_recoverable_note(owner, "Invalid Raw Note")

    response = authenticated_client(admin).post(
        reverse("notes:admin_note_restore", args=[note.id]), {"owner": "not-a-number"}
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery")
    assert "not-a-number" not in response.url


@pytest.mark.django_db
def test_no_open_redirect_from_owner_field():
    owner = create_account("recovery-open-redirect-owner")
    admin = create_admin("recovery-admin-open-redirect")
    note = _make_recoverable_note(owner, "Open Redirect Note")

    response = authenticated_client(admin).post(
        reverse("notes:admin_note_restore", args=[note.id]),
        {"owner": "http://evil.example/"},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery")
    assert "evil.example" not in response.url


@pytest.mark.django_db
def test_missing_owner_field_omits_query_string_on_restore():
    owner = create_account("recovery-missing-owner-field-owner")
    admin = create_admin("recovery-admin-missing-owner-field")
    note = _make_recoverable_note(owner, "Missing Owner Field Note")

    response = authenticated_client(admin).post(reverse("notes:admin_note_restore", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("notes:admin_recovery")


# -- privacy and query-count behavior -------------------------------------------------


@pytest.mark.django_db
def test_owner_filtered_page_no_note_body_leakage():
    owner = create_account("recovery-filtered-privacy-owner")
    admin = create_admin("recovery-admin-filtered-privacy")
    note = _make_recoverable_note(owner, "Filtered Privacy Note", emptied=False)
    Note.objects.filter(pk=note.pk).update(
        body_json={"type": "doc", "content": []},
        body_plain_text="SECRET_FILTERED_VALUE_XYZ",
        trashed_at=timezone.now() - timedelta(days=45),
    )

    response = authenticated_client(admin).get(
        reverse("notes:admin_recovery") + f"?owner={owner.id}"
    )
    content = response.content.decode()

    assert "SECRET_FILTERED_VALUE_XYZ" not in content


@pytest.mark.django_db
def test_owner_options_query_count_bounded_across_owner_count():
    owner_a = create_account("recovery-qc-owner-a")
    admin = create_admin("recovery-admin-qc")
    _make_recoverable_note(owner_a, "QC Note A")

    client = authenticated_client(admin)
    with CaptureQueriesContext(connection) as baseline:
        response = client.get(reverse("notes:admin_recovery"))
    assert response.status_code == 200
    baseline_count = len(baseline.captured_queries)

    for i in range(8):
        extra_owner = create_account(f"recovery-qc-owner-extra-{i}")
        extra_note = _make_recoverable_note(extra_owner, f"QC Extra Note {i}")
        assert extra_note is not None

    with CaptureQueriesContext(connection) as scaled:
        response = client.get(reverse("notes:admin_recovery"))
    assert response.status_code == 200
    scaled_count = len(scaled.captured_queries)

    assert scaled_count == baseline_count


@pytest.mark.django_db
def test_query_count_with_owner_filter_applied_stays_bounded():
    owner = create_account("recovery-qc-filtered-owner")
    admin = create_admin("recovery-admin-qc-filtered")
    _make_recoverable_note(owner, "QC Filtered Note")
    _make_recoverable_folder(owner, "QC Filtered Folder")

    client = authenticated_client(admin)
    with CaptureQueriesContext(connection) as baseline:
        response = client.get(reverse("notes:admin_recovery") + f"?owner={owner.id}")
    assert response.status_code == 200
    baseline_count = len(baseline.captured_queries)

    for i in range(8):
        extra_note = services.create_note(owner=owner)
        services.rename_note(note=extra_note, title=f"QC Filtered Extra Note {i}")
        services.move_note_to_trash(note=extra_note)
        Note.objects.filter(pk=extra_note.pk).update(emptied_at=timezone.now())

    with CaptureQueriesContext(connection) as scaled:
        response = client.get(reverse("notes:admin_recovery") + f"?owner={owner.id}")
    assert response.status_code == 200
    scaled_count = len(scaled.captured_queries)

    assert scaled_count == baseline_count
