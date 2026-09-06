"""Owner Note Content Lifecycle Access.

`note_for_owner_or_404()` enforces ownership only, correctly and
unchanged -- Restore and other lifecycle actions still need to retrieve
a trashed Note. Relying on that ownership-only lookup alone, with no
independent lifecycle-state check, would let an owner who knew/retained/
reconstructed a direct URL read a Note's title/body while it was in
owner-visible Trash or administrator-recovery-only state -- directly
contradicting the promise that after `Delete
permanently` "the owner can no longer access... it." `note_print`,
`note_export`, `note_download_text`, and `note_freshness` all
call `services.note_content_accessible_to_owner()`
(`note.trashed_at is None`) before returning any content.
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


def make_owner_visible_trashed_note(owner, *, title="Trashed Note"):
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title=title)
    services.move_note_to_trash(note=note)
    note.refresh_from_db()
    return note


def make_administrator_recovery_note(owner, *, title="Emptied Note"):
    note = make_owner_visible_trashed_note(owner, title=title)
    services.empty_trash_for_owner(owner=owner)
    note.refresh_from_db()
    assert note.emptied_at is not None
    return note


def make_naturally_aged_note(owner, *, title="Aged Note"):
    note = make_owner_visible_trashed_note(owner, title=title)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_VISIBLE_MAX_AGE - timedelta(days=1)
    )
    note.refresh_from_db()
    assert note.emptied_at is None
    assert note.trashed_at is not None
    return note


ROUTES = {
    "print": "notes:print",
    "export": "notes:export",
    "download_text": "notes:download_text",
    "freshness": "notes:freshness",
}


def _get(client, route_name, note_id, *, version=0):
    url = reverse(ROUTES[route_name], args=[note_id])
    if route_name == "freshness":
        url = f"{url}?version={version}"
    return client.get(url)


CONTENT_MARKER = "Secret Trashed Body Marker"


def _note_with_marker_body(owner, *, title):
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title=title)
    body = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": CONTENT_MARKER}],
            }
        ],
    }
    services.save_note(note=note, title=note.title, body_json=body, version=note.version)
    note.refresh_from_db()
    return note


# ---------------------------------------------------------------------------
# 1. Active Note: unchanged, succeeds normally, for every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_active_note_content_access_succeeds(route_name):
    owner = create_account(f"active-{route_name}-owner")
    note = _note_with_marker_body(owner, title="Active Note")

    # `_note_with_marker_body()` creates then saves once, so `note.version`
    # is 2 -- `version=1` is the smallest valid, in-range value that makes
    # `note_freshness` include content in its response; ignored by the
    # other three routes.
    response = _get(authenticated_client(owner), route_name, note.id, version=1)

    assert response.status_code == 200
    assert CONTENT_MARKER in response.content.decode()


# ---------------------------------------------------------------------------
# 2. Owner-visible Trash: content blocked for every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_owner_visible_trash_blocks_content_access(route_name):
    owner = create_account(f"trash-{route_name}-owner")
    note = _note_with_marker_body(owner, title="Trashed Note")
    services.move_note_to_trash(note=note)
    note.refresh_from_db()

    response = _get(authenticated_client(owner), route_name, note.id, version=0)

    assert CONTENT_MARKER not in response.content.decode()
    if route_name == "freshness":
        assert response.status_code == 409
        payload = response.json()
        assert payload["ok"] is False
        assert "title" not in payload
        assert "body_json" not in payload
    else:
        assert response.status_code == 302
        assert response["Location"] == reverse("notes:trash")


# ---------------------------------------------------------------------------
# 3. Manually emptied / administrator-recovery-only: content blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_administrator_recovery_only_blocks_content_access(route_name):
    owner = create_account(f"emptied-{route_name}-owner")
    note = _note_with_marker_body(owner, title="Emptied Note")
    services.move_note_to_trash(note=note)
    services.empty_trash_for_owner(owner=owner)
    note.refresh_from_db()
    assert note.emptied_at is not None

    response = _get(authenticated_client(owner), route_name, note.id, version=0)

    assert CONTENT_MARKER not in response.content.decode()
    if route_name == "freshness":
        assert response.status_code == 409
    else:
        assert response.status_code == 302
        assert response["Location"] == reverse("notes:trash")


# ---------------------------------------------------------------------------
# 4. Naturally aged past the owner-visible window, still admin-recoverable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_naturally_aged_note_blocks_content_access(route_name):
    owner = create_account(f"aged-{route_name}-owner")
    note = _note_with_marker_body(owner, title="Aged Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_VISIBLE_MAX_AGE - timedelta(days=1)
    )
    note.refresh_from_db()
    assert note.emptied_at is None

    response = _get(authenticated_client(owner), route_name, note.id, version=0)

    assert CONTENT_MARKER not in response.content.decode()
    if route_name == "freshness":
        assert response.status_code == 409
    else:
        assert response.status_code == 302
        assert response["Location"] == reverse("notes:trash")


# ---------------------------------------------------------------------------
# 5. Cross-owner: non-disclosing 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_cross_owner_returns_404(route_name):
    owner = create_account(f"cross-owner-a-{route_name}")
    other = create_account(f"cross-owner-b-{route_name}")
    note = services.create_note(owner=owner)

    response = _get(authenticated_client(other), route_name, note.id, version=0)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 6. Missing/purged: 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_missing_note_returns_404(route_name):
    owner = create_account(f"missing-{route_name}-owner")

    response = _get(authenticated_client(owner), route_name, 999999, version=0)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Regression: after Delete permanently, owner content access is blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_name", list(ROUTES))
@pytest.mark.django_db
def test_owner_cannot_access_content_after_delete_permanently(route_name):
    owner = create_account(f"deleted-permanently-{route_name}-owner")
    note = _note_with_marker_body(owner, title="Permanently Deleted Note")
    services.move_note_to_trash(note=note)
    services.permanently_delete_note_for_owner(note=note)
    note.refresh_from_db()
    assert note.trashed_at is not None
    assert note.emptied_at is not None
    assert Note.objects.filter(pk=note.pk).exists()

    response = _get(authenticated_client(owner), route_name, note.id, version=0)

    assert CONTENT_MARKER not in response.content.decode()
    if route_name == "freshness":
        assert response.status_code == 409
    else:
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Helper unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_content_accessible_to_owner_true_for_active_note():
    owner = create_account("helper-active-owner")
    note = services.create_note(owner=owner)

    assert services.note_content_accessible_to_owner(note) is True


@pytest.mark.django_db
def test_note_content_accessible_to_owner_false_for_owner_visible_trash():
    owner = create_account("helper-trash-owner")
    note = make_owner_visible_trashed_note(owner)

    assert services.note_content_accessible_to_owner(note) is False


@pytest.mark.django_db
def test_note_content_accessible_to_owner_false_for_administrator_recovery():
    owner = create_account("helper-emptied-owner")
    note = make_administrator_recovery_note(owner)

    assert services.note_content_accessible_to_owner(note) is False


@pytest.mark.django_db
def test_note_content_accessible_to_owner_false_for_naturally_aged_note():
    owner = create_account("helper-aged-owner")
    note = make_naturally_aged_note(owner)

    assert services.note_content_accessible_to_owner(note) is False


@pytest.mark.django_db
def test_note_content_accessible_to_owner_distinct_from_restore_eligibility():
    """Owner-visible Trash: restore-eligible but not content-accessible --
    the two predicates must disagree here, proving they are genuinely
    separate, not aliases of the same check."""
    owner = create_account("helper-distinct-owner")
    note = make_owner_visible_trashed_note(owner)

    assert services.note_is_visible_and_self_restorable(note) is True
    assert services.note_content_accessible_to_owner(note) is False


# ---------------------------------------------------------------------------
# Neighboring behavior must remain unaffected
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_note_for_owner_or_404_still_returns_trashed_notes_unfiltered():
    """`note_for_owner_or_404()` must remain ownership-only -- Restore and
    other lifecycle actions still need to retrieve a trashed row."""
    owner = create_account("ownership-only-owner")
    note = make_owner_visible_trashed_note(owner)

    fetched = services.note_for_owner_or_404(note_id=note.id, owner=owner)

    assert fetched.pk == note.pk


@pytest.mark.django_db
def test_note_detail_still_redirects_trashed_notes_to_trash():
    owner = create_account("detail-unchanged-owner")
    note = make_owner_visible_trashed_note(owner)

    response = authenticated_client(owner).get(reverse("notes:detail", args=[note.id]))

    assert response.status_code == 302
    assert response["Location"] == reverse("notes:trash")


@pytest.mark.django_db
def test_restore_is_unaffected_by_the_content_accessibility_check():
    owner = create_account("restore-unaffected-owner")
    note = make_owner_visible_trashed_note(owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_restore", args=[note.id]), follow=True
    )

    note.refresh_from_db()
    assert response.status_code == 200
    assert note.trashed_at is None


@pytest.mark.django_db
def test_permanent_delete_is_unaffected_by_the_content_accessibility_check():
    owner = create_account("permanent-delete-unaffected-owner")
    note = make_owner_visible_trashed_note(owner)

    authenticated_client(owner).post(reverse("notes:note_permanent_delete", args=[note.id]))

    note.refresh_from_db()
    assert note.emptied_at is not None


@pytest.mark.django_db
def test_autosave_on_trashed_note_still_rejected_as_before():
    """Confirms the pre-existing, already-correct mutation guard is
    untouched by this module's read-path-only checks."""
    owner = create_account("autosave-unaffected-owner")
    note = make_owner_visible_trashed_note(owner)

    response = authenticated_client(owner).post(
        reverse("notes:autosave", args=[note.id]),
        data="{}",
        content_type="application/json",
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "trashed"
