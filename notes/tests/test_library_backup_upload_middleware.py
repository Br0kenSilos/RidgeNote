""" implementation correction --
`LibraryBackupUploadGuardMiddleware`.

Covers the middleware that installs `LibraryBackupRequestSizeGuard`
before Django's `CsrfViewMiddleware` can lock `request.upload_handlers`
by accessing `request.POST` during `process_view()` -- the real bug
this middleware fixes.
"""

import io
import json
import zipfile

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from notes import library_backup_upload, views
from notes.middleware import LibraryBackupUploadGuardMiddleware

PASSWORD = "LongUniquePassword123!"

_VALID_MANIFEST = {
    "format_identifier": "ridgenote-library-backup",
    "format_version": 1,
    "exported_at": "2026-08-06T12:00:00+00:00",
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


def _valid_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(_VALID_MANIFEST).encode("utf-8"))
        archive.writestr("README.txt", b"RidgeNote Library Backup\n")
        archive.writestr("notes/active/Unfiled/Sample.md", b"# Sample\n\nHello\n")
    return buf.getvalue()


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def authenticated_client(user, **kwargs):
    client = Client(**kwargs)
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


RESTORE_PREVIEW_PATH = "/notes/library-backup/restore-preview/"
RESTORE_PATH = "/notes/library-backup/restore/"
CONFIRMATION = "REPLACE MY CURRENT LIBRARY"


# ---------------------------------------------------------------------------
# Middleware unit tests -- route/method scoping, guard installation
# ---------------------------------------------------------------------------


def test_post_to_restore_preview_receives_guard():
    captured = {}

    def get_response(request):
        captured["guard"] = getattr(request, "library_backup_upload_guard", None)
        captured["handlers"] = list(request.upload_handlers)
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().post(RESTORE_PREVIEW_PATH, {})
    middleware(request)

    assert isinstance(captured["guard"], library_backup_upload.LibraryBackupRequestSizeGuard)
    assert captured["handlers"][0] is captured["guard"]


def test_get_to_restore_preview_does_not_receive_guard():
    captured = {}

    def get_response(request):
        captured["guard"] = getattr(request, "library_backup_upload_guard", None)
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().get(RESTORE_PREVIEW_PATH)
    middleware(request)

    assert captured["guard"] is None


def test_post_to_restore_receives_guard():
    """The middleware's allowlist covers
    two routes -- the restore route must be guarded exactly
    like the restore-preview route."""
    captured = {}

    def get_response(request):
        captured["guard"] = getattr(request, "library_backup_upload_guard", None)
        captured["handlers"] = list(request.upload_handlers)
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().post(RESTORE_PATH, {})
    middleware(request)

    assert isinstance(captured["guard"], library_backup_upload.LibraryBackupRequestSizeGuard)
    assert captured["handlers"][0] is captured["guard"]


def test_get_to_restore_does_not_receive_guard():
    captured = {}

    def get_response(request):
        captured["guard"] = getattr(request, "library_backup_upload_guard", None)
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().get(RESTORE_PATH)
    middleware(request)

    assert captured["guard"] is None


def test_unrelated_post_route_does_not_receive_guard():
    captured = {}

    def get_response(request):
        captured["guard"] = getattr(request, "library_backup_upload_guard", None)
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().post("/notes/library-backup/download/", {})
    middleware(request)

    assert captured["guard"] is None


def test_guard_is_installed_before_get_response_is_called():
    """The whole point of this middleware: the guard must be in place
    strictly before `get_response()` runs, since CSRF's `process_view`
    (which can lock `request.upload_handlers`) fires inside it."""
    order = []

    def get_response(request):
        order.append("get_response")
        assert getattr(request, "library_backup_upload_guard", None) is not None
        return HttpResponse()

    middleware = LibraryBackupUploadGuardMiddleware(get_response)
    request = RequestFactory().post(RESTORE_PREVIEW_PATH, {})
    middleware(request)

    assert order == ["get_response"]


# ---------------------------------------------------------------------------
# End-to-end: guard survives real, enforced CSRF checking
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_guard_survives_real_enforced_csrf_check():
    """Regression test for the bug this middleware fixes: with CSRF
    actually enforced (unlike the default test Client), a POST that
    installs the guard only inside the view raises
    `AttributeError: You cannot alter upload handlers after the upload
    has been processed.` because CsrfViewMiddleware.process_view()
    already consumed request.POST first. With the middleware in place,
    this succeeds."""
    user = create_account("csrf-ordering-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)

    get_response = client.get(reverse("notes:library_backup_restore_preview"))
    assert get_response.status_code == 200
    token = client.cookies["csrftoken"].value

    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore_preview"),
        {"backup_file": upload, "csrfmiddlewaretoken": token},
        format="multipart",
    )

    assert response.status_code == 200
    assert "Backup validated" in response.content.decode()


@pytest.mark.django_db
def test_missing_csrf_token_is_still_rejected_normally():
    """The middleware must not weaken CSRF protection -- a POST with no
    valid token is still refused by Django's own CSRF enforcement."""
    user = create_account("csrf-still-enforced-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)
    client.get(reverse("notes:library_backup_restore_preview"))

    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore_preview"),
        {"backup_file": upload},
        format="multipart",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_restore_guard_survives_real_enforced_csrf_check():
    """Same regression coverage as the restore-preview route, for the
 restore route. The restore route is POST-only (
 the real browser flow submits it only
    from a dialog on the preview page), so the CSRF token/cookie comes
    from a GET on the preview page instead of a GET on this route."""
    user = create_account("restore-csrf-ordering-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)

    get_response = client.get(reverse("notes:library_backup_restore_preview"))
    assert get_response.status_code == 200
    token = client.cookies["csrftoken"].value

    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION, "csrfmiddlewaretoken": token},
        format="multipart",
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_restore_missing_csrf_token_is_still_rejected_normally():
    user = create_account("restore-csrf-still-enforced-owner")
    client = authenticated_client(user, enforce_csrf_checks=True)
    client.get(reverse("notes:library_backup_restore_preview"))

    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    response = client.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION},
        format="multipart",
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Missing-guard fallback in the view itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_treats_missing_guard_as_malformed_upload():
    """If the middleware somehow did not run (route aliasing, direct
    view invocation, a future refactor), the view must fail safely
    rather than crash on a missing `library_backup_upload_guard`."""
    user = create_account("missing-guard-owner")
    factory = RequestFactory()
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    request = factory.post(reverse("notes:library_backup_restore_preview"), {"backup_file": upload})
    request.user = user

    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.middleware import SessionMiddleware

    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)

    assert not hasattr(request, "library_backup_upload_guard")
    response = views.library_backup_restore_preview(request)

    assert response.status_code == 200
    # `library_backup_restore_preview.html`'s "Backup validated" heading is
    # always statically rendered -- gated only by a `hidden` attribute on
    # its containing element ({% if not preview %} hidden{% endif %}) for
    # progressive-enhancement JS, not omitted from markup -- so a bare
    # substring search for "Backup validated" cannot tell an error
    # response apart from a real success one. Matched on the exact
    # `data-library-restore-validated-instructions hidden` attribute
    # pairing instead, which directly reflects `preview` being falsy (the
    # same condition the template itself branches on).
    assert "data-library-restore-validated-instructions hidden" in response.content.decode()
    messages = list(request._messages)
    assert any("could not read that upload" in str(m) for m in messages)


@pytest.mark.django_db
def test_restore_view_treats_missing_guard_as_malformed_upload():
    user = create_account("restore-missing-guard-owner")
    factory = RequestFactory()
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    request = factory.post(
        reverse("notes:library_backup_restore"),
        {"backup_file": upload, "confirmation": CONFIRMATION},
    )
    request.user = user

    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.middleware import SessionMiddleware

    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)

    assert not hasattr(request, "library_backup_upload_guard")
    response = views.library_backup_restore(request)

    assert response.status_code == 302
    assert response["Location"] == reverse("notes:library_backup_restore_preview")
    messages = list(request._messages)
    assert any("could not read that upload" in str(m) for m in messages)
