"""Delete-origin and confirmation-return
convergence; delete confirmation and post-delete
navigation.

Covers the `DeleteOrigin`/`DeleteReturnState` contract and every row of
the binding destination table: successful deletion and the
already-in-Trash/unavailable race path, across Home, All Notes, and
note detail; the adjacent-active-note-in-folder fallback for
deleting the currently-open note; and the security boundary
around untrusted state.

Confirmation happens via
an in-context dialog (`delete-confirm.ts`) -- it is
entirely client-side, so `note_delete`/`note_delete_all_notes`/
`folder_delete`/`folder_delete_home` are POST-only and there is no
server-rendered "Cancel" state to test.
"""

from datetime import timedelta

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


def _seed_note(owner, *, title, modified_hours_ago, pinned=False, folder=None):
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(
        title=title,
        modified_at=now - timedelta(hours=modified_hours_ago),
        pinned=pinned,
    )
    if folder is not None:
        services.assign_note_folder(note=note, folder=folder)
    note.refresh_from_db()
    return note


# -- note detail --------------------------------------------------------------


@pytest.mark.django_db
def test_note_detail_current_note_success_returns_home():
    owner = create_account("nd-current-success-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Current Note Success")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_note_detail_current_note_already_trashed_returns_home_with_message():
    owner = create_account("nd-current-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 1")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": note.id},
        follow=True,
    )

    assert response.redirect_chain[-1][0] == reverse("home")
    messages = list(response.context["messages"])
    assert any(m.message == "That note is already in Trash." for m in messages)


@pytest.mark.django_db
def test_note_detail_other_tree_note_success_preserves_hosting_note():
    owner = create_account("nd-other-success-owner")
    hosting_note = services.create_note(owner=owner)
    other_note = services.create_note(owner=owner)
    services.rename_note(note=other_note, title="Other Tree Note")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[other_note.id]),
        {"origin": "note_detail", "current_note": hosting_note.id},
    )
    other_note.refresh_from_db()
    hosting_note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[hosting_note.id])
    assert other_note.trashed_at is not None
    assert hosting_note.trashed_at is None


@pytest.mark.django_db
def test_note_detail_other_tree_note_already_trashed_preserves_hosting_note_with_message():
    owner = create_account("nd-other-race-owner")
    hosting_note = services.create_note(owner=owner)
    other_note = services.create_note(owner=owner)
    services.rename_note(note=other_note, title="Other Note 2")
    services.move_note_to_trash(note=other_note)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[other_note.id]),
        {"origin": "note_detail", "current_note": hosting_note.id},
        follow=True,
    )

    assert response.redirect_chain[-1][0] == reverse("notes:detail", args=[hosting_note.id])
    messages = list(response.context["messages"])
    assert any(m.message == "That note is already in Trash." for m in messages)


@pytest.mark.django_db
def test_note_detail_tree_folder_success_preserves_hosting_note_when_folder_unrelated():
    owner = create_account("nd-folder-success-owner")
    hosting_note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[hosting_note.id, folder.id])
    )
    folder.refresh_from_db()
    hosting_note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[hosting_note.id])
    assert folder.trashed_at is not None
    assert hosting_note.trashed_at is None


@pytest.mark.django_db
def test_note_detail_tree_folder_success_falls_back_home_when_hosting_note_is_inside_it():
    # Deleting the folder cascades `trashed_at` onto its own active
    # notes, including the currently-viewed one -- the post-mutation
    # revalidation must catch this and fall back Home, not redirect to
    # a now-trashed note's detail page.
    owner = create_account("nd-folder-cascade-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    hosting_note = services.create_note(owner=owner)
    services.assign_note_folder(note=hosting_note, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[hosting_note.id, folder.id])
    )
    folder.refresh_from_db()
    hosting_note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert folder.trashed_at is not None
    assert hosting_note.trashed_at is not None


@pytest.mark.django_db
def test_note_detail_invalid_hosting_note_id_falls_back_home():
    owner = create_account("nd-invalid-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": "not-a-number"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_note_detail_nonexistent_hosting_note_id_falls_back_home():
    owner = create_account("nd-nonexistent-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": 999999},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_note_detail_cross_owner_hosting_note_id_falls_back_home():
    owner = create_account("nd-cross-owner")
    other = create_account("nd-cross-other")
    note = services.create_note(owner=owner)
    other_note = services.create_note(owner=other)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": other_note.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_note_detail_trashed_hosting_note_id_falls_back_home():
    owner = create_account("nd-trashed-hosting-owner")
    note = services.create_note(owner=owner)
    already_trashed = services.create_note(owner=owner)
    services.rename_note(note=already_trashed, title="Already Trashed 3")
    services.move_note_to_trash(note=already_trashed)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": already_trashed.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


# -- Home tree ------------------------------------------------------------------


@pytest.mark.django_db
def test_home_tree_note_success_returns_home():
    owner = create_account("home-note-success-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Home Tree Note")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "home"}
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_home_tree_note_already_trashed_returns_home_with_message():
    owner = create_account("home-note-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 4")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "home"}, follow=True
    )

    assert response.redirect_chain[-1][0] == reverse("home")
    messages = list(response.context["messages"])
    assert any(m.message == "That note is already in Trash." for m in messages)


@pytest.mark.django_db
def test_home_tree_folder_success_returns_home():
    owner = create_account("home-folder-success-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id]), {"origin": "home"}
    )
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_home_tree_folder_already_trashed_returns_home_with_message():
    owner = create_account("home-folder-race-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id]), {"origin": "home"}, follow=True
    )

    assert response.redirect_chain[-1][0] == reverse("home")
    messages = list(response.context["messages"])
    assert any(m.message == "That folder is already in Trash." for m in messages)


# -- All Notes tree ---------------------------------------------------------------


@pytest.mark.django_db
def test_all_notes_tree_note_success_preserves_page_and_sort():
    owner = create_account("an-note-success-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="All Notes Tree Note")

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "all_notes", "page": "2", "sort": "title_asc"},
    )
    note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == f"{reverse('notes:all_notes')}?page=2&sort=title_asc"
    assert note.trashed_at is not None


@pytest.mark.django_db
def test_all_notes_tree_note_already_trashed_preserves_state_with_message():
    owner = create_account("an-note-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 5")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "all_notes", "page": "2", "sort": "title_asc"},
        follow=True,
    )

    assert response.redirect_chain[-1][0] == f"{reverse('notes:all_notes')}?page=2&sort=title_asc"
    messages = list(response.context["messages"])
    assert any(m.message == "That note is already in Trash." for m in messages)


@pytest.mark.django_db
def test_all_notes_tree_folder_success_preserves_page_and_sort():
    owner = create_account("an-folder-success-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id]),
        {"origin": "all_notes", "page": "3", "sort": "modified_new"},
    )
    folder.refresh_from_db()

    assert response.status_code == 302
    assert response.url == f"{reverse('notes:all_notes')}?page=3&sort=modified_new"
    assert folder.trashed_at is not None


@pytest.mark.django_db
def test_all_notes_tree_folder_already_trashed_preserves_state_with_message():
    owner = create_account("an-folder-race-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    services.move_folder_to_trash(folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id]),
        {"origin": "all_notes", "page": "3", "sort": "modified_new"},
        follow=True,
    )

    assert (
        response.redirect_chain[-1][0] == f"{reverse('notes:all_notes')}?page=3&sort=modified_new"
    )
    messages = list(response.context["messages"])
    assert any(m.message == "That folder is already in Trash." for m in messages)


@pytest.mark.django_db
def test_all_notes_row_already_trashed_shows_message():
    # `note_delete_all_notes` itself is unmodified route/behavior-wise;
    # this confirms the newly-added consistent informational message
    # on its own pre-existing race path.
    owner = create_account("an-row-race-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Note 6")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete_all_notes", args=[note.id]),
        {"page": "1", "sort": "pinned_new"},
        follow=True,
    )

    messages = list(response.context["messages"])
    assert any(m.message == "That note is already in Trash." for m in messages)


# -- Direct/legacy/malformed state -----------------------------------------------


@pytest.mark.django_db
def test_missing_origin_falls_back_home():
    owner = create_account("legacy-missing-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(reverse("notes:note_delete", args=[note.id]))

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_unknown_origin_falls_back_home():
    owner = create_account("legacy-unknown-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "somewhere_else"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_mixed_case_origin_falls_back_home():
    owner = create_account("legacy-mixedcase-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "Home"}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_malformed_current_note_falls_back_home():
    owner = create_account("legacy-malformed-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "note_detail", "current_note": "<script>alert(1)</script>"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_folder_delete_home_direct_legacy_link_falls_back_home():
    owner = create_account("legacy-folder-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete_home", args=[folder.id])
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


# -- Security: no state field may ever become an arbitrary redirect target ------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "origin_value",
    [
        "https://example.com",
        "//example.com",
        "/admin/",
        "javascript:alert(1)",
    ],
)
def test_external_or_path_like_origin_values_are_rejected(origin_value):
    owner = create_account("security-origin-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": origin_value}
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert response.url not in (origin_value, "https://example.com", "//example.com")


@pytest.mark.django_db
def test_next_parameter_has_no_effect_on_destination():
    owner = create_account("security-next-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "home", "next": "https://example.com/steal"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_tampered_hidden_post_state_cannot_create_open_redirect():
    # Simulates a POST whose hidden fields were edited in the browser
    # before submission -- every field here is attacker-controlled, and
    # none of it may influence the destination beyond the fixed enum/
    # validated-identifier contract.
    owner = create_account("security-tamper-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {
            "origin": "note_detail",
            "current_note": "http://evil.example/",
            "page": "-99999999999999999999",
            "sort": "'; DROP TABLE notes_note; --",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("bad_page", "expected_page"),
    [
        ("-1", 1),
        ("0", 1),
        ("not-a-number", 1),
        # A syntactically valid but absurd positive integer is not itself
        # unsafe (Python ints are arbitrary precision, and pagination
        # clamps out-of-range pages when the destination page actually
        # renders) -- coercion here only rejects non-positive/malformed
        # input, so this one round-trips unchanged.
        ("99999999999999999999", 99999999999999999999),
    ],
)
def test_invalid_page_normalizes_safely(bad_page, expected_page):
    owner = create_account("security-page-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "all_notes", "page": bad_page, "sort": "pinned_new"},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('notes:all_notes')}?page={expected_page}&sort=pinned_new"


@pytest.mark.django_db
def test_invalid_sort_normalizes_safely():
    owner = create_account("security-sort-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[note.id]),
        {"origin": "all_notes", "page": "1", "sort": "not-a-real-sort"},
    )

    assert response.status_code == 302
    assert "sort=pinned_new" in response.url


# -- Existing mutation/behavior preservation -------------------------------------


@pytest.mark.django_db
def test_folder_delete_still_cascades_active_contained_notes_to_trash():
    owner = create_account("cascade-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    contained_note = services.create_note(owner=owner)
    services.assign_note_folder(note=contained_note, folder=folder)
    hosting_note = services.create_note(owner=owner)

    authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[hosting_note.id, folder.id])
    )
    folder.refresh_from_db()
    contained_note.refresh_from_db()

    assert folder.trashed_at is not None
    assert contained_note.trashed_at is not None


@pytest.mark.django_db
def test_note_delete_get_is_rejected_and_cannot_mutate():
    # Confirmation happens entirely client-side (the
    # dialog), so `note_delete` is POST-only -- GET is rejected
    # outright (405), not just "harmless."
    owner = create_account("get-no-mutate-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_delete", args=[note.id]), {"origin": "home"}
    )
    note.refresh_from_db()

    assert response.status_code == 405
    assert note.trashed_at is None


@pytest.mark.django_db
def test_folder_delete_home_get_is_rejected():
    owner = create_account("folder-get-no-mutate-owner")
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(
        reverse("notes:folder_delete_home", args=[folder.id]), {"origin": "home"}
    )
    folder.refresh_from_db()

    assert response.status_code == 405
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_folder_delete_get_is_rejected():
    owner = create_account("folder-nd-get-no-mutate-owner")
    hosting_note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Projects")

    response = authenticated_client(owner).get(
        reverse("notes:folder_delete", args=[hosting_note.id, folder.id])
    )
    folder.refresh_from_db()

    assert response.status_code == 405
    assert folder.trashed_at is None


@pytest.mark.django_db
def test_note_delete_all_notes_get_is_rejected():
    owner = create_account("an-get-no-mutate-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_delete_all_notes", args=[note.id]), {"page": "1", "sort": "pinned_new"}
    )
    note.refresh_from_db()

    assert response.status_code == 405
    assert note.trashed_at is None


@pytest.mark.django_db
def test_note_delete_cross_owner_still_blocked():
    owner = create_account("ownership-owner")
    other = create_account("ownership-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).post(
        reverse("notes:note_delete", args=[note.id]), {"origin": "home"}
    )
    note.refresh_from_db()

    assert response.status_code == 404
    assert note.trashed_at is None


# -- adjacent-active-note-in-folder fallback for --------------------------------
# -- deleting the currently-open note --------------------------------------------


@pytest.mark.django_db
def test_current_note_deletion_opens_next_note_in_folder_tree_order():
    # Tree order is (-pinned, Lower(title),
    # -id) -- case-insensitive alphabetical, not most-recently-modified
    # first. `modified_at` is deliberately set in reverse of alphabetical
    # order here (Alpha is the *oldest*, Charlie the *newest*) so this
    # test only passes if the tree is genuinely ordering by title, not by
    # recency. Deleting the middle
    # note (by that order) must land on the next one in the same order --
    # the one immediately after it, not the one before.
    owner = create_account("adjacent-next-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    alpha = _seed_note(owner, title="Alpha", modified_hours_ago=3, folder=folder)  # oldest
    bravo = _seed_note(owner, title="Bravo", modified_hours_ago=2, folder=folder)
    charlie = _seed_note(owner, title="Charlie", modified_hours_ago=1, folder=folder)  # newest

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[bravo.id]),
        {"origin": "note_detail", "current_note": bravo.id},
    )
    bravo.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[charlie.id])
    assert bravo.trashed_at is not None
    assert alpha.trashed_at is None
    assert charlie.trashed_at is None


@pytest.mark.django_db
def test_current_note_deletion_falls_back_to_previous_note_when_it_was_last_in_order():
    owner = create_account("adjacent-previous-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    newer = _seed_note(owner, title="Newer", modified_hours_ago=1, folder=folder)
    # Last in tree order (-pinned, Lower(title),
    # -id) -- "Oldest" also happens to sort alphabetically after "Newer".
    # With only one sibling
    # remaining either way, this test exercises the fallback-to-previous
    # branch itself rather than discriminating old vs new ordering.
    oldest = _seed_note(owner, title="Oldest", modified_hours_ago=5, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[oldest.id]),
        {"origin": "note_detail", "current_note": oldest.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[newer.id])


@pytest.mark.django_db
def test_current_note_deletion_prefers_pinned_group_over_alphabetical():
    # Pin state always
    # sorts first, but the secondary tiebreak among unpinned siblings is
    # alphabetical, not recency. "Apple" is deliberately the *least*
    # recently modified unpinned note here (and "Zebra" the most
    # recent), so this test only passes under alphabetical tiebreaking
    # -- it only passes if the tree is genuinely ordering unpinned
    # siblings by title.
    owner = create_account("adjacent-pinned-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    pinned = _seed_note(owner, title="Pinned", modified_hours_ago=10, pinned=True, folder=folder)
    alpha_unpinned = _seed_note(owner, title="Apple", modified_hours_ago=5, folder=folder)
    _seed_note(owner, title="Zebra", modified_hours_ago=1, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[pinned.id]),
        {"origin": "note_detail", "current_note": pinned.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[alpha_unpinned.id])


@pytest.mark.django_db
def test_current_note_deletion_falls_back_home_when_no_sibling_remains_in_folder():
    owner = create_account("adjacent-none-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    only_note = services.create_note(owner=owner)
    services.assign_note_folder(note=only_note, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[only_note.id]),
        {"origin": "note_detail", "current_note": only_note.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_current_note_deletion_adjacent_fallback_applies_to_unfiled_too():
    owner = create_account("adjacent-unfiled-owner")
    # No folder at all -- Unfiled is `folder_id=None`, and the same
    # ordering/fallback logic (-pinned,
    # Lower(title), -id) must apply there identically.
    newer = _seed_note(owner, title="Newer", modified_hours_ago=1)
    older = _seed_note(owner, title="Older", modified_hours_ago=5)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[newer.id]),
        {"origin": "note_detail", "current_note": newer.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[older.id])


@pytest.mark.django_db
def test_current_note_deletion_adjacent_fallback_ignores_notes_in_other_folders():
    owner = create_account("adjacent-cross-folder-owner")
    folder_a = services.create_folder(owner=owner, name="Folder A")
    folder_b = services.create_folder(owner=owner, name="Folder B")
    only_note_in_a = _seed_note(owner, title="Only in A", modified_hours_ago=1, folder=folder_a)
    _seed_note(owner, title="In B", modified_hours_ago=2, folder=folder_b)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[only_note_in_a.id]),
        {"origin": "note_detail", "current_note": only_note_in_a.id},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_deleting_a_different_note_never_triggers_adjacent_lookup_for_current_note():
    # The adjacent-note fallback is only for deleting the *currently open*
    # note -- deleting a sibling elsewhere in the tree must keep returning
    # to the still-open current note exactly as before, even though they
    # share a folder.
    owner = create_account("adjacent-not-current-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    current_note = _seed_note(owner, title="Current", modified_hours_ago=1, folder=folder)
    sibling = _seed_note(owner, title="Sibling", modified_hours_ago=2, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:note_delete", args=[sibling.id]),
        {"origin": "note_detail", "current_note": current_note.id},
    )
    current_note.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("notes:detail", args=[current_note.id])
    assert current_note.trashed_at is None


@pytest.mark.django_db
def test_folder_delete_containing_current_note_goes_home_not_to_an_adjacent_note():
    # Deleting a folder never uses the adjacent-note fallback, even when
    # the currently-open note lives inside it and a sibling remains --
    # the approved contract is unconditionally Home for this case.
    owner = create_account("adjacent-folder-cascade-owner")
    folder = services.create_folder(owner=owner, name="Projects")
    current_note = _seed_note(owner, title="Current", modified_hours_ago=1, folder=folder)
    sibling = _seed_note(owner, title="Sibling", modified_hours_ago=2, folder=folder)

    response = authenticated_client(owner).post(
        reverse("notes:folder_delete", args=[current_note.id, folder.id])
    )
    current_note.refresh_from_db()
    sibling.refresh_from_db()

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert current_note.trashed_at is not None
    assert sibling.trashed_at is not None
