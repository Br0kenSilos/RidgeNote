"""Library-backup restore preview view.

Covers `notes:library_backup_restore_preview` -- the login-protected
GET/POST endpoint that validates an uploaded RidgeNote library backup
ZIP and renders a preview. This view never mutates the database, never
records an audit event, and never offers a restore action.
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
_VALID_README = b"RidgeNote Library Backup\n"
_VALID_MARKDOWN = b"# Sample\n\nHello\n"


def _valid_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", json.dumps(_VALID_MANIFEST).encode("utf-8"))
        archive.writestr("README.txt", _VALID_README)
        archive.writestr("notes/active/Unfiled/Sample.md", _VALID_MARKDOWN)
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


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _upload(client, raw: bytes, name="backup.zip", **kwargs):
    upload = io.BytesIO(raw)
    upload.name = name
    return client.post(
        reverse("notes:library_backup_restore_preview"),
        {"backup_file": upload},
        format="multipart",
        **kwargs,
    )


def _library_snapshot(owner):
    return {
        "folders": list(
            Folder.objects.filter(owner=owner).values(
                "id", "name", "is_recovery_folder", "trashed_at", "emptied_at"
            )
        ),
        "notes": list(
            Note.objects.filter(owner=owner).values(
                "id",
                "title",
                "body_json",
                "folder_id",
                "pinned",
                "trashed_at",
                "emptied_at",
                "modified_at",
            )
        ),
        "tags": list(Tag.objects.filter(owner=owner).values("id", "name")),
        "audit_count": AuditEvent.objects.count(),
    }


# ---------------------------------------------------------------------------
# Access and method
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_get_redirects_to_login():
    response = Client().get(reverse("notes:library_backup_restore_preview"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.django_db
def test_anonymous_post_redirects_to_login():
    response = _upload(Client(), _valid_zip_bytes())
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


@pytest.mark.django_db
def test_get_renders_upload_form():
    user = create_account("get-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="library-restore-form"' in content
    assert 'method="post"' in content
    assert 'enctype="multipart/form-data"' in content
    assert "Validate backup and preview" in content


@pytest.mark.django_db
def test_get_renders_new_initial_copy():
    user = create_account("initial-copy-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    content = " ".join(response.content.decode().split())
    assert "Select a RidgeNote library backup ZIP file to validate / restore." in content
    assert "The maximum file size allowed is 200 MiB." in content
    assert "Backup files may contain sensitive note content." not in content


@pytest.mark.django_db
def test_get_renders_new_initial_note():
    user = create_account("initial-note-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    content = " ".join(response.content.decode().split())
    assert "<strong>NOTE:</strong>" in content
    assert (
        "Validating a backup does not modify your current library, and no "
        "restore occurs until you explicitly confirm a restore is desired." in content
    )
    assert (
        "The uploaded file is processed temporarily and is not retained "
        "after validation." in content
    )


@pytest.mark.django_db
def test_no_implementation_commentary_leaks_into_rendered_html():
    # Regression test: a multiline `{# ... #}` Django comment does
    # NOT span lines -- it renders as literal text -- and that leaked text
    # happened to contain a literal "<form>" substring, which the
    # browser's HTML parser treated as a real (phantom) opening form tag.
    # Because HTML forbids nested forms, the browser then silently
    # dropped the real `<form id="library-restore-form">` element
    # entirely, breaking every button on the page (nothing could be
    # wired up, since `document.querySelector('[data-library-restore-form]')`
    # returned null). Implementation commentary belongs in
    # `{% comment %}...{% endcomment %}` blocks, never a plain `{# #}`
    # spanning more than one line.
    user = create_account("no-leaked-comment-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    content = response.content.decode()
    assert "{#" not in content
    assert "#}" not in content
    # The real form must actually exist as a distinct element -- not
    # merely contain matching text, which a corrupted/dropped form could
    # still coincidentally produce elsewhere in the response.
    assert content.count('id="library-restore-form"') == 1


@pytest.mark.django_db
def test_restore_decision_prompt_hidden_in_initial_server_rendered_html():
    # Regression test for a second real browser-found defect from the
    # same checkpoint: the "Restore this backup?" prompt was visible on
    # first load, before any successful validation. The template already
    # carried the native `hidden` attribute, but a same-specificity CSS
    # rule (`.library-restore-decision { display: flex; ... }`) was an
    # *author* stylesheet rule, and author rules always win over the
    # user-agent default `[hidden] { display: none }` regardless of
    # selector specificity -- so the attribute was present but had no
    # visual effect. This asserts the safety net at the only layer that
    # cannot be silently defeated by CSS: the attribute's presence in the
    # server-rendered HTML itself.
    user = create_account("decision-hidden-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    content = response.content.decode()
    start = content.index("data-library-restore-decision")
    end = content.index(">", start)
    assert "hidden" in content[start:end]


# ---------------------------------------------------------------------------
# Successful preview
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_upload_renders_preview_inline():
    user = create_account("valid-upload-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-library-restore-validated="true"' in content


@pytest.mark.django_db
def test_valid_upload_unhides_validated_instructions_and_hides_initial():
    user = create_account("validated-instructions-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()

    start = content.index("data-library-restore-validated-instructions")
    end = content.index(">", start)
    assert "hidden" not in content[start:end]

    start = content.index("data-library-restore-initial-instructions")
    end = content.index(">", start)
    assert "hidden" in content[start:end]


@pytest.mark.django_db
def test_valid_upload_shows_new_validated_instructions_copy():
    user = create_account("validated-copy-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = " ".join(response.content.decode().split())
    assert "<h2>Backup validated</h2>" in content
    assert "has been validated as a valid RidgeNote library backup." in content
    assert (
        "Review the summary below to see how it compares with your current "
        "library. You may also select a different backup file to validate "
        "and potentially restore." in content
    )
    # Redundant post-validation status text is gone now that the top of the
    # page communicates validation success once, clearly.
    assert "No changes have been made." not in content


@pytest.mark.django_db
def test_valid_upload_shows_simplified_decision_copy():
    # One simplified decision block with a heading, two short paragraphs,
    # and Continue/Cancel controls -- no second Yes/No question anywhere
    # on this page.
    user = create_account("decision-copy-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = " ".join(response.content.decode().split())
    assert "<h2>Restore this backup?</h2>" in content
    assert (
        "Review the comparison table above. If this is the backup you "
        "want to restore, click Continue. Otherwise, click Cancel." in content
    )
    assert "data-library-restore-decision-continue" in content
    assert "data-library-restore-decision-cancel" in content
    assert ">Continue<" in content
    assert ">Cancel<" in content
    assert "What a restore would do" not in content
    assert "This preview does not perform a restore" not in content
    assert "Would you like to restore this backup?" not in content
    assert "Restore this backup? Yes" not in content
    # Removed as redundant now that the top-of-page block already
    # communicates validation success once, clearly.
    assert (
        "Backup validation is complete. No changes have been made to your library." not in content
    )


@pytest.mark.django_db
def test_valid_upload_shows_destructive_dialog_warning_and_note_copy():
    # The dialog markup is always server-rendered (just hidden pre-open by
    # native <dialog> semantics), so its copy is verifiable without a
    # browser.
    user = create_account("dialog-copy-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = " ".join(response.content.decode().split())

    assert "<strong>WARNING:</strong>" in content
    assert "This is <strong>NOT</strong> a data merge." in content
    assert (
        "This action will <strong>REPLACE</strong> and <strong>OVERWRITE</strong> "
        "your current RidgeNote library." in content
    )
    assert (
        "All content in your current library that is not present within "
        "this backup file will be removed." in content
    )
    assert (
        "We recommend that you download a backup of your current library "
        "before proceeding with the restore." in content
    )

    assert "<strong>NOTE:</strong>" in content
    assert "This action affects only the data in <strong>YOUR</strong> library." in content
    assert "It does not affect the libraries of other users." in content
    assert (
        "Your account credentials, password, session, audit history, and "
        "RidgeNote server configuration are not replaced and will remain "
        "unchanged." in content
    )

    assert "Download current library backup" in content
    assert "data-library-restore-phrase-cancel" in content
    assert ">Cancel<" in content
    assert "<strong>Do you wish to proceed?</strong>" in content
    assert "Are you sure?" not in content


@pytest.mark.django_db
def test_valid_upload_shows_comparison_counts():
    user = create_account("comparison-owner")
    services.create_note(owner=user)
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()
    # Current library: 1 note (from create_note); backup: 1 note (Sample).
    assert content.count("<td>1</td>") >= 2


@pytest.mark.django_db
def test_valid_upload_shows_renamed_summary_heading():
    user = create_account("summary-heading-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()
    assert "Backup ZIP file summary" in content
    assert "Archive summary" not in content


@pytest.mark.django_db
def test_valid_upload_has_visual_separation_before_decision_block():
    user = create_account("separation-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()
    # The comparison table's closing tag must precede the decision block
    # in document order, with the decision block carrying its own
    # separation class -- the exact visual treatment lives in CSS, but
    # the markup hook it depends on is verifiable here.
    comparison_end = content.index("</table>")
    decision_start = content.index('class="library-restore-decision"')
    assert comparison_end < decision_start


@pytest.mark.django_db
def test_cache_control_header_present():
    user = create_account("cache-header-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.django_db
def test_get_also_sets_cache_control_header():
    user = create_account("cache-header-get-owner")
    response = authenticated_client(user).get(reverse("notes:library_backup_restore_preview"))
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.django_db
def test_uploaded_filename_not_reflected_in_response():
    user = create_account("filename-not-reflected-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes(), name="super-secret-name.zip")
    assert "super-secret-name.zip" not in response.content.decode()


@pytest.mark.django_db
def test_note_titles_not_reflected_in_response():
    user = create_account("titles-not-reflected-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    assert "Sample" not in response.content.decode()


def _preview_section(content: str) -> str:
    # The persistent upload form
    # (holding "Validate backup and preview") lives *before* the
    # replaceable result region that holds the actual preview content, so
    # this must scope strictly to the result region itself -- not the
    # whole `.library-restore-preview` section, which also contains
    # the persistent form and, further down, the decision prompt/dialog
    # markup (including the dialog's own "Restore library" button).
    start = content.index("data-library-restore-result-region")
    end = content.index("data-library-restore-decision", start)
    return content[start:end]


@pytest.mark.django_db
def test_no_restore_button_or_action():
    user = create_account("no-restore-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    section = _preview_section(response.content.decode())
    assert ">Restore<" not in section
    assert "Restore library" not in section


@pytest.mark.django_db
def test_successful_preview_marks_result_region_as_validated():
    # There is no "Continue to restore" link -- the
    # flow never navigates to a second page at all. Instead,
    # `library-restore.ts`'s async validation looks for exactly this
    # marker in the fetched HTML fragment to decide whether to reveal the
    # in-page "Restore this backup?" decision prompt.
    user = create_account("validated-marker-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()
    assert 'data-library-restore-validated="true"' in content


@pytest.mark.django_db
def test_failed_preview_does_not_mark_result_region_as_validated():
    user = create_account("failed-no-validated-marker-owner")
    response = _upload(authenticated_client(user), b"not a zip file at all")
    content = response.content.decode()
    assert 'data-library-restore-validated="true"' not in content


# ---------------------------------------------------------------------------
# Failure -- safe error messages
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_file_selected_shows_safe_message():
    user = create_account("no-file-owner")
    response = authenticated_client(user).post(
        reverse("notes:library_backup_restore_preview"), {}, follow=True
    )
    content = response.content.decode()
    assert "Choose a RidgeNote library backup ZIP" in content


@pytest.mark.django_db
def test_invalid_zip_shows_safe_message_not_raw_code():
    user = create_account("invalid-zip-owner")
    response = _upload(authenticated_client(user), b"not a zip file at all", follow=True)
    content = response.content.decode()
    assert "not a valid RidgeNote library backup" in content
    assert "invalid_zip" not in content


@pytest.mark.django_db
def test_manifest_failure_shows_safe_message():
    user = create_account("manifest-failure-owner")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", b"{not valid json")
        archive.writestr("README.txt", _VALID_README)
    response = _upload(authenticated_client(user), buf.getvalue())
    content = response.content.decode()
    assert "backup manifest is invalid" in content


@pytest.mark.django_db
def test_upload_too_large_shows_safe_message(monkeypatch):
    from notes import views

    monkeypatch.setattr(views.library_backup_upload, "MAX_LIBRARY_BACKUP_UPLOAD_BYTES", 10)
    user = create_account("too-large-owner")
    response = _upload(authenticated_client(user), _valid_zip_bytes())
    content = response.content.decode()
    assert "too large" in content


@pytest.mark.django_db
def test_extra_field_shows_safe_message():
    user = create_account("extra-field-owner")
    upload = io.BytesIO(_valid_zip_bytes())
    upload.name = "backup.zip"
    other = io.BytesIO(_valid_zip_bytes())
    other.name = "second.zip"
    response = authenticated_client(user).post(
        reverse("notes:library_backup_restore_preview"),
        {"backup_file": upload, "extra_file": other},
        format="multipart",
    )
    content = response.content.decode()
    assert "Select exactly one" in content


@pytest.mark.django_db
def test_failed_upload_does_not_render_preview_block():
    user = create_account("failed-no-preview-owner")
    response = _upload(authenticated_client(user), b"not a zip file at all")
    content = response.content.decode()
    assert 'data-library-restore-validated="true"' not in content


@pytest.mark.django_db
def test_failed_upload_keeps_initial_instructions_visible_and_validated_hidden():
    # The initial/validated top-of-page instruction blocks are always
    # both present in server-rendered HTML (matching this feature's
    # established hidden-attribute-toggle convention, e.g. the decision
    # prompt and dialog sections) -- a failed validation must leave the
    # *validated* block's `hidden` attribute in place and never add one
    # to the *initial* block.
    user = create_account("failed-instructions-owner")
    response = _upload(authenticated_client(user), b"not a zip file at all")
    content = response.content.decode()

    start = content.index("data-library-restore-initial-instructions")
    end = content.index(">", start)
    assert "hidden" not in content[start:end]

    start = content.index("data-library-restore-validated-instructions")
    end = content.index(">", start)
    assert "hidden" in content[start:end]


# ---------------------------------------------------------------------------
# No retention / no audit / no database mutation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_audit_event_recorded_on_success():
    user = create_account("no-audit-success-owner")
    _upload(authenticated_client(user), _valid_zip_bytes())
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_no_audit_event_recorded_on_failure():
    user = create_account("no-audit-failure-owner")
    _upload(authenticated_client(user), b"not a zip file at all")
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_database_unchanged_on_valid_preview():
    user = create_account("db-unchanged-valid-owner")
    services.create_note(owner=user)
    before = _library_snapshot(user)
    _upload(authenticated_client(user), _valid_zip_bytes())
    after = _library_snapshot(user)
    assert before == after


@pytest.mark.django_db
def test_database_unchanged_on_invalid_zip():
    user = create_account("db-unchanged-invalid-zip-owner")
    services.create_note(owner=user)
    before = _library_snapshot(user)
    _upload(authenticated_client(user), b"not a zip file at all")
    after = _library_snapshot(user)
    assert before == after


@pytest.mark.django_db
def test_database_unchanged_on_manifest_failure():
    user = create_account("db-unchanged-manifest-owner")
    services.create_note(owner=user)
    before = _library_snapshot(user)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("manifest.json", b"{not valid json")
        archive.writestr("README.txt", _VALID_README)
    _upload(authenticated_client(user), buf.getvalue())
    after = _library_snapshot(user)
    assert before == after


@pytest.mark.django_db
def test_database_unchanged_on_size_failure(monkeypatch):
    from notes import views

    monkeypatch.setattr(views.library_backup_upload, "MAX_LIBRARY_BACKUP_UPLOAD_BYTES", 10)
    user = create_account("db-unchanged-size-owner")
    services.create_note(owner=user)
    before = _library_snapshot(user)
    _upload(authenticated_client(user), _valid_zip_bytes())
    after = _library_snapshot(user)
    assert before == after


@pytest.mark.django_db
def test_database_unchanged_on_multipart_failure():
    user = create_account("db-unchanged-multipart-owner")
    services.create_note(owner=user)
    before = _library_snapshot(user)
    authenticated_client(user).post(
        reverse("notes:library_backup_restore_preview"), {}, follow=True
    )
    after = _library_snapshot(user)
    assert before == after


@pytest.mark.django_db
def test_no_upload_retained_after_request(tmp_path, settings):
    settings.FILE_UPLOAD_TEMP_DIR = str(tmp_path)
    user = create_account("no-retention-owner")
    _upload(authenticated_client(user), _valid_zip_bytes())
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Account-page entry point
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_page_links_to_restore_preview():
    # The explanatory copy is
    # one combined sentence pair (still
    # covering the same two facts -- what Download does, and that
    # Restore previews differences before applying anything), placed
    # above the action buttons.
    user = create_account("account-link-owner")
    response = authenticated_client(user).get(reverse("accounts:account"))
    content = " ".join(response.content.decode().split())
    assert reverse("notes:library_backup_restore_preview") in content
    assert "Restore previous library backup" in content
    assert (
        "Create and download a ZIP backup of your notes, folders, and tags. "
        "For Restore, you can preview the statistical differences between "
        "your current library and a backup file you select, then choose "
        "whether to restore that backup."
    ) in content


@pytest.mark.django_db
def test_account_page_download_link_unaffected():
    user = create_account("account-download-unaffected-owner")
    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()
    assert reverse("notes:library_backup_download") in content
    assert "Download library backup" in content
