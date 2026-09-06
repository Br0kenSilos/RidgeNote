"""Library-backup restore view/form.

Covers `notes:library_backup_restore` -- the login-protected, POST-only
destructive restore endpoint that requires a fresh ZIP upload, full
revalidation, restore-specific semantic checks, and an exact
confirmation phrase before performing the atomic full-overwrite
restore.

There is no standalone GET-rendered
restore page: the browser flow submits this
endpoint directly from a dialog on `library_backup_restore_preview`,
reusing the same browser-held File selection validated there, so this
endpoint is POST-only (`@require_POST`) and every *expected*
failure redirects back to `notes:library_backup_restore_preview` with
a `messages.error()` banner rather than re-rendering a page of its
own. The confirmation phrase is also now "REPLACE MY CURRENT LIBRARY",
superseding the original "REPLACE MY LIBRARY".
"""

import io
import json
import zipfile

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Folder, Note, Tag

PASSWORD = "LongUniquePassword123!"
CONFIRMATION = "REPLACE MY CURRENT LIBRARY"

_VALID_MANIFEST = {
    "format_identifier": "ridgenote-library-backup",
    "format_version": 1,
    "exported_at": "2026-08-08T12:00:00+00:00",
    "scope": {"active": True, "trash": True, "recovery": True},
    "folders": [],
    "tags": [],
    "notes": [
        {
            "id": "note-000001",
            "title": "Sample",
            "body_json": {"type": "doc", "content": [{"type": "paragraph"}]},
            "folder_id": None,
            "tag_ids": [],
            "pinned": False,
            "lifecycle": "active",
            "trashed_at": None,
            "emptied_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "modified_at": "2026-01-01T00:00:00+00:00",
            "markdown_path": "notes/active/Unfiled/Sample.md",
        }
    ],
}


def _valid_zip_bytes(manifest=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest or _VALID_MANIFEST).encode("utf-8"))
        archive.writestr("README.txt", b"RidgeNote Library Backup\n")
        archive.writestr("notes/active/Unfiled/Sample.md", b"# Sample\n\nHello\n")
    return buf.getvalue()


def _semantic_conflict_zip_bytes() -> bytes:
    manifest = dict(_VALID_MANIFEST)
    manifest["folders"] = [
        {
            "id": "folder-000001",
            "name": "First",
            "lifecycle": "active",
            "trashed_at": None,
            "emptied_at": None,
            "is_recovery_folder": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": "folder-000002",
            "name": "Second",
            "lifecycle": "active",
            "trashed_at": None,
            "emptied_at": None,
            "is_recovery_folder": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    manifest["notes"] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
        archive.writestr("README.txt", b"RidgeNote Library Backup\n")
    return buf.getvalue()


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username, password=password, role=role, **kwargs
    )


def authenticated_client(user, **kwargs):
    client = Client(**kwargs)
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _restore_post(client, raw: bytes, confirmation: str, name="backup.zip", **kwargs):
    upload = io.BytesIO(raw)
    upload.name = name
    return client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": confirmation},
        format="multipart",
        **kwargs,
    )


def _restore_post_json(client, raw: bytes, confirmation: str, name="backup.zip", **kwargs):
    # Mirrors `library-restore.ts`'s own fetch(): `Accept: application/json`
    # is the entire content-negotiation signal `_wants_json_response()`
    # checks -- no other header or query parameter is involved.
    upload = io.BytesIO(raw)
    upload.name = name
    return client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": confirmation},
        format="multipart",
        headers={"accept": "application/json"},
        **kwargs,
    )


def _snapshot(owner):
    return {
        "folders": list(Folder.objects.filter(owner=owner).values("id", "name")),
        "notes": list(Note.objects.filter(owner=owner).values("id", "title")),
        "tags": list(Tag.objects.filter(owner=owner).values("id", "name")),
        "audit_count": AuditEvent.objects.count(),
    }


# ---------------------------------------------------------------------------
# Access and method
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_post_redirects_to_login():
    response = _restore_post(Client(), _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.django_db
def test_get_is_not_allowed():
    user = create_account("get-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore"))
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# Successful restore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_backup_and_correct_phrase_restores_and_redirects_home():
    user = create_account("success-owner")
    response = _restore_post(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert Note.objects.filter(owner=user, title="Sample").exists()


@pytest.mark.django_db
def test_success_message_shown_after_redirect():
    user = create_account("success-message-owner")
    client = authenticated_client(user)
    response = _restore_post(client, _valid_zip_bytes(), CONFIRMATION)
    content = client.get(response["Location"]).content.decode()
    assert "Library restored successfully." in content


@pytest.mark.django_db
def test_audit_event_recorded_on_success():
    user = create_account("audit-owner")
    _restore_post(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_RESTORED)
    assert events.count() == 1
    assert events.first().actor_id == user.id
    assert events.first().target_user_id == user.id


# ---------------------------------------------------------------------------
# Confirmation phrase
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_wrong_phrase_rejected_no_mutation():
    user = create_account("wrong-phrase-owner")
    before = _snapshot(user)
    response = _restore_post(authenticated_client(user), _valid_zip_bytes(), "nope")
    assert response.status_code == 302
    assert response["Location"] == reverse("notes:library_backup_restore_preview")
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_wrong_phrase_message_shown_after_redirect():
    user = create_account("wrong-phrase-message-owner")
    client = authenticated_client(user)
    response = _restore_post(client, _valid_zip_bytes(), "nope")
    content = client.get(response["Location"]).content.decode()
    assert f"Type {CONFIRMATION} exactly to confirm." in content


@pytest.mark.django_db
def test_old_phrase_rejected():
    # The pre-correction phrase must no longer be accepted.
    user = create_account("old-phrase-owner")
    before = _snapshot(user)
    response = _restore_post(authenticated_client(user), _valid_zip_bytes(), "REPLACE MY LIBRARY")
    assert response.status_code == 302
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_wrong_case_rejected():
    user = create_account("wrong-case-owner")
    before = _snapshot(user)
    response = _restore_post(
        authenticated_client(user), _valid_zip_bytes(), "replace my current library"
    )
    assert response.status_code == 302
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_wrong_internal_spacing_rejected():
    user = create_account("wrong-spacing-owner")
    before = _snapshot(user)
    response = _restore_post(
        authenticated_client(user), _valid_zip_bytes(), "REPLACE  MY CURRENT LIBRARY"
    )
    assert response.status_code == 302
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_surrounding_whitespace_trimmed_and_accepted():
    user = create_account("trim-owner")
    response = _restore_post(authenticated_client(user), _valid_zip_bytes(), f"  {CONFIRMATION}  ")
    assert response.status_code == 302
    assert response["Location"] == reverse("home")


# ---------------------------------------------------------------------------
# Upload/archive errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_file_shows_safe_message():
    user = create_account("no-file-owner")
    response = authenticated_client(user).post(
        reverse("notes:library_backup_restore"), {"confirmation": CONFIRMATION}, follow=True
    )
    content = response.content.decode()
    assert "Choose a RidgeNote library backup ZIP" in content


@pytest.mark.django_db
def test_invalid_zip_shows_safe_message_no_mutation():
    user = create_account("invalid-zip-owner")
    before = _snapshot(user)
    response = _restore_post(
        authenticated_client(user), b"not a zip file at all", CONFIRMATION, follow=True
    )
    content = response.content.decode()
    assert "not a valid RidgeNote library backup" in content
    assert "invalid_zip" not in content
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_upload_too_large_shows_safe_message(monkeypatch):
    from notes import views

    monkeypatch.setattr(views.library_backup_upload, "MAX_LIBRARY_BACKUP_UPLOAD_BYTES", 10)
    user = create_account("too-large-owner")
    response = _restore_post(
        authenticated_client(user), _valid_zip_bytes(), CONFIRMATION, follow=True
    )
    assert "too large" in response.content.decode()


@pytest.mark.django_db
def test_multiple_files_rejected():
    user = create_account("multi-file-owner")
    client = authenticated_client(user)
    upload1 = io.BytesIO(_valid_zip_bytes())
    upload1.name = "one.zip"
    upload2 = io.BytesIO(_valid_zip_bytes())
    upload2.name = "two.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": [upload1, upload2], "confirmation": CONFIRMATION},
        format="multipart",
        follow=True,
    )
    assert "Select exactly one" in response.content.decode()


# ---------------------------------------------------------------------------
# Restore-specific semantic errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_semantic_conflict_shows_safe_message_no_mutation():
    user = create_account("semantic-conflict-owner")
    before = _snapshot(user)
    response = _restore_post(
        authenticated_client(user), _semantic_conflict_zip_bytes(), CONFIRMATION, follow=True
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "more than one recovery-folder marker" in content
    assert "multiple_recovery_markers" not in content
    assert _snapshot(user) == before


# ---------------------------------------------------------------------------
# No retention
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_orphaned_tmp_file_after_restore(tmp_path, settings):
    settings.FILE_UPLOAD_TEMP_DIR = str(tmp_path)
    user = create_account("no-retention-owner")
    _restore_post(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.django_db
def test_no_orphaned_tmp_file_after_failed_restore(tmp_path, settings):
    settings.FILE_UPLOAD_TEMP_DIR = str(tmp_path)
    user = create_account("no-retention-failure-owner")
    _restore_post(authenticated_client(user), b"not a zip file at all", CONFIRMATION)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_csrf_token_rejected():
    user = create_account("csrf-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)
    # The GET-rendered preview page is what now supplies the CSRF cookie in
    # the real browser flow -- the destructive restore endpoint itself has
    # no GET of its own to visit first.
    client.get(reverse("notes:library_backup_restore_preview"))
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION},
        format="multipart",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_valid_csrf_token_restores():
    user = create_account("csrf-valid-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)
    get_response = client.get(reverse("notes:library_backup_restore_preview"))
    token = client.cookies["csrftoken"].value
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION, "csrfmiddlewaretoken": token},
        format="multipart",
    )
    assert response.status_code == 302
    assert get_response.status_code == 200


# ---------------------------------------------------------------------------
# JSON response negotiation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_json_success_contract():
    user = create_account("json-success-owner")
    response = _restore_post_json(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"ok": True, "redirect_url": reverse("home")}
    assert Note.objects.filter(owner=user, title="Sample").exists()


@pytest.mark.django_db
def test_json_success_does_not_set_messages_banner():
    # The JS-enhanced path renders its own in-dialog success state; the
    # Django messages banner is only for the non-JS fallback's full-page
    # redirect, so a JSON response must not also queue one.
    user = create_account("json-no-message-owner")
    client = authenticated_client(user)
    _restore_post_json(client, _valid_zip_bytes(), CONFIRMATION)
    home_content = client.get(reverse("home")).content.decode()
    assert "Library restored successfully." not in home_content


@pytest.mark.django_db
def test_json_wrong_phrase_failure_contract():
    user = create_account("json-wrong-phrase-owner")
    before = _snapshot(user)
    response = _restore_post_json(authenticated_client(user), _valid_zip_bytes(), "nope")
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == f"Type {CONFIRMATION} exactly to confirm."
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_json_missing_phrase_failure_contract():
    # Regression test for the real browser-found defect: the phrase field
    # must actually arrive in the multipart body. An empty/missing
    # confirmation value must still be rejected server-side exactly like
    # any other wrong value -- the server never trusts client-side gating.
    user = create_account("json-missing-phrase-owner")
    before = _snapshot(user)
    response = _restore_post_json(authenticated_client(user), _valid_zip_bytes(), "")
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_json_old_phrase_failure_contract():
    # The pre-correction phrase ("REPLACE MY LIBRARY") must still be
    # rejected through the JSON path exactly as it is through the non-JSON
    # path.
    user = create_account("json-old-phrase-owner")
    before = _snapshot(user)
    response = _restore_post_json(
        authenticated_client(user), _valid_zip_bytes(), "REPLACE MY LIBRARY"
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == f"Type {CONFIRMATION} exactly to confirm."
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_json_exact_phrase_succeeds_server_side_authoritative():
    # Server-side validation remains authoritative regardless of what any
    # client-side gating did or didn't allow -- this exercises the exact
    # phrase string end-to-end through the same JSON contract path.
    user = create_account("json-exact-phrase-owner")
    response = _restore_post_json(
        authenticated_client(user), _valid_zip_bytes(), "REPLACE MY CURRENT LIBRARY"
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert Note.objects.filter(owner=user, title="Sample").exists()


@pytest.mark.django_db
def test_json_semantic_validation_failure_contract():
    user = create_account("json-semantic-owner")
    before = _snapshot(user)
    response = _restore_post_json(
        authenticated_client(user), _semantic_conflict_zip_bytes(), CONFIRMATION
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload == {
        "ok": False,
        "error": "The backup contains more than one recovery-folder marker.",
    }
    assert "multiple_recovery_markers" not in response.content.decode()
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_json_invalid_archive_failure_contract():
    user = create_account("json-invalid-archive-owner")
    before = _snapshot(user)
    response = _restore_post_json(
        authenticated_client(user), b"not a zip file at all", CONFIRMATION
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert "not a valid RidgeNote library backup" in payload["error"]
    assert "invalid_zip" not in payload["error"]
    assert _snapshot(user) == before


@pytest.mark.django_db
def test_json_no_file_failure_contract():
    user = create_account("json-no-file-owner")
    response = authenticated_client(user).post(
        reverse("notes:library_backup_restore"),
        {"confirmation": CONFIRMATION},
        headers={"accept": "application/json"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload == {"ok": False, "error": "Choose a RidgeNote library backup ZIP to continue."}


@pytest.mark.django_db
def test_json_upload_too_large_failure_contract(monkeypatch):
    from notes import views

    monkeypatch.setattr(views.library_backup_upload, "MAX_LIBRARY_BACKUP_UPLOAD_BYTES", 10)
    user = create_account("json-too-large-owner")
    response = _restore_post_json(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert "too large" in payload["error"]


@pytest.mark.django_db
def test_json_unexpected_exception_propagates_as_500(monkeypatch):
    # A programming/DB/audit failure must never be silently reshaped into
    # a `{"ok": false}` validation-style payload -- it has to surface as
    # an ordinary unhandled exception, exactly as the non-JSON path
    # already does, so it isn't hidden from monitoring/error tracking.
    from notes import library_backup_restore as library_backup_restore_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(library_backup_restore_service, "restore_library_backup_for_owner", _boom)
    user = create_account("json-unexpected-owner")
    client = authenticated_client(user, raise_request_exception=False)
    response = _restore_post_json(client, _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 500


@pytest.mark.django_db
def test_json_response_requires_csrf():
    user = create_account("json-csrf-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)
    client.get(reverse("notes:library_backup_restore_preview"))
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION},
        format="multipart",
        headers={"accept": "application/json"},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_non_json_fallback_still_redirects_on_success():
    # Regression guard: the plain (non-JS) POST path must remain provably
    # unchanged by the new JSON branch.
    user = create_account("fallback-still-redirects-owner")
    response = _restore_post(authenticated_client(user), _valid_zip_bytes(), CONFIRMATION)
    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert response["Content-Type"] != "application/json"


# ---------------------------------------------------------------------------
# Owner scope
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_restore_only_affects_requesting_user():
    owner = create_account("scope-owner")
    other = create_account("scope-other-owner")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Not Mine")

    _restore_post(authenticated_client(owner), _valid_zip_bytes(), CONFIRMATION)

    other_note.refresh_from_db()
    assert other_note.title == "Not Mine"
