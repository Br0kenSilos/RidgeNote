"""Downloadable library-backup view.

Covers `notes:library_backup_download` -- the login-protected GET
endpoint that creates a `SpooledTemporaryFile`, calls
`write_library_backup_archive()`, records the audit event, and returns
a `FileResponse`.
"""

import re
import tempfile
import zipfile
from io import BytesIO

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import library_backup_archive, services, views

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


def _consume(response) -> bytes:
    return b"".join(response.streaming_content)


class _TrackingSpool(tempfile.SpooledTemporaryFile):
    """A real `SpooledTemporaryFile` that records how many times
    `.close()` is called, so tests can assert the view's cleanup
    contract without depending on Django's own response-close timing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


@pytest.fixture
def tracked_spools(monkeypatch):
    created: list[_TrackingSpool] = []

    def factory(*args, **kwargs):
        spool = _TrackingSpool(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(views.tempfile, "SpooledTemporaryFile", factory)
    return created


# ---------------------------------------------------------------------------
# Access and method
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_anonymous_request_redirects_to_login():
    response = Client().get(reverse("notes:library_backup_download"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.django_db(transaction=True)
def test_get_succeeds_and_returns_zip_content_type():
    user = create_account("download-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_download"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


@pytest.mark.django_db(transaction=True)
def test_post_is_rejected():
    user = create_account("post-rejected-owner")
    response = authenticated_client(user).post(reverse("notes:library_backup_download"))
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_content_disposition_filename_matches_contract():
    user = create_account("filename-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_download"))
    disposition = response["Content-Disposition"]
    assert disposition.startswith("attachment;")
    match = re.search(r'filename="(ridgenote-library-backup-[^"]+\.zip)"', disposition)
    assert match
    assert re.fullmatch(r"ridgenote-library-backup-\d{4}-\d{2}-\d{2}_\d{6}\.zip", match.group(1))


@pytest.mark.django_db(transaction=True)
def test_cache_and_content_type_headers():
    user = create_account("headers-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_download"))
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db(transaction=True)
def test_content_length_present_and_correct():
    user = create_account("content-length-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_download"))
    body = _consume(response)
    assert response["Content-Length"] == str(len(body))


@pytest.mark.django_db(transaction=True)
def test_response_body_is_a_valid_zip():
    user = create_account("valid-zip-owner")
    services.create_note(owner=user)
    response = authenticated_client(user).get(reverse("notes:library_backup_download"))
    body = _consume(response)
    with zipfile.ZipFile(BytesIO(body)) as archive:
        assert archive.namelist()[0] == "manifest.json"
        assert archive.namelist()[1] == "README.txt"


@pytest.mark.django_db(transaction=True)
def test_response_contains_only_owner_content():
    owner = create_account("owner-only-a")
    other = create_account("owner-only-b")
    services.rename_note(note=services.create_note(owner=other), title="Not Mine")
    services.rename_note(note=services.create_note(owner=owner), title="Mine")

    response = authenticated_client(owner).get(reverse("notes:library_backup_download"))
    body = _consume(response)
    with zipfile.ZipFile(BytesIO(body)) as archive:
        manifest_text = archive.read("manifest.json").decode("utf-8")
    assert "Mine" in manifest_text
    assert "Not Mine" not in manifest_text


# ---------------------------------------------------------------------------
# Audit behavior
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_audit_event_recorded_on_success():
    user = create_account("audit-success-owner")
    authenticated_client(user).get(reverse("notes:library_backup_download"))
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_DOWNLOADED)
    assert events.count() == 1
    event = events.first()
    assert event.actor_id == user.id
    assert event.target_user_id == user.id
    assert event.details == {}


@pytest.mark.django_db(transaction=True)
def test_repeated_downloads_create_repeated_audit_events():
    user = create_account("repeated-audit-owner")
    client = authenticated_client(user)
    client.get(reverse("notes:library_backup_download"))
    client.get(reverse("notes:library_backup_download"))
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_DOWNLOADED)
    assert events.count() == 2


@pytest.mark.django_db(transaction=True)
def test_no_success_audit_event_on_generation_failure(monkeypatch):
    user = create_account("no-audit-on-failure-owner")
    services.create_note(owner=user)
    monkeypatch.setattr(library_backup_archive, "MAX_NOTE_COUNT", 0)

    response = authenticated_client(user).get(reverse("notes:library_backup_download"))

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:account")
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_DOWNLOADED)
    assert events.count() == 0


@pytest.mark.django_db(transaction=True)
def test_generation_failure_shows_safe_message(monkeypatch):
    user = create_account("safe-message-owner")
    services.create_note(owner=user)
    monkeypatch.setattr(library_backup_archive, "MAX_NOTE_COUNT", 0)

    response = authenticated_client(user).get(reverse("notes:library_backup_download"), follow=True)

    content = response.content.decode()
    assert "too many notes" in content
    assert "note_limit_exceeded" not in content


# ---------------------------------------------------------------------------
# Spool ownership / cleanup
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_expected_generation_failure_closes_the_spool(monkeypatch, tracked_spools):
    user = create_account("close-on-expected-failure-owner")
    services.create_note(owner=user)
    monkeypatch.setattr(library_backup_archive, "MAX_NOTE_COUNT", 0)

    authenticated_client(user).get(reverse("notes:library_backup_download"))

    assert len(tracked_spools) == 1
    assert tracked_spools[0].close_calls == 1


@pytest.mark.django_db(transaction=True)
def test_unexpected_exception_closes_the_spool_and_propagates(monkeypatch, tracked_spools):
    user = create_account("close-on-unexpected-exception-owner")

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(views.library_backup_archive, "write_library_backup_archive", _boom)

    client = authenticated_client(user)
    with pytest.raises(RuntimeError, match="boom"):
        client.get(reverse("notes:library_backup_download"))

    assert len(tracked_spools) == 1
    assert tracked_spools[0].close_calls == 1


@pytest.mark.django_db(transaction=True)
def test_audit_failure_closes_the_spool_and_propagates(monkeypatch, tracked_spools):
    user = create_account("close-on-audit-failure-owner")

    def _boom(*args, **kwargs):
        raise RuntimeError("audit boom")

    monkeypatch.setattr(views.account_services, "record_audit_event", _boom)

    client = authenticated_client(user)
    with pytest.raises(RuntimeError, match="audit boom"):
        client.get(reverse("notes:library_backup_download"))

    assert len(tracked_spools) == 1
    assert tracked_spools[0].close_calls == 1
    events = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_LIBRARY_BACKUP_DOWNLOADED)
    assert events.count() == 0
