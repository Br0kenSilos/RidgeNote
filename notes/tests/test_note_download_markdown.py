"""Individual Note Markdown Download.

`note_download_markdown` is a thin wrapper: canonical conversion is
reused verbatim from `notes.markdown.note_to_markdown()` (the same
function `notes/library_backup_archive.py` already calls for library
backups) -- no second converter, no duplicated logic. Lifecycle access
reuses `services.note_content_accessible_to_owner()`
unmodified, so this route inherits the Active-only rule rather than
becoming a fifth direct-content loophole.
"""

from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.markdown import note_to_markdown
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


def _doc(*content):
    return {"type": "doc", "content": list(content)}


def _paragraph(*content):
    return {"type": "paragraph", "content": list(content)}


def _text(value, marks=None):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def _heading(level, *content):
    return {"type": "heading", "attrs": {"level": level}, "content": list(content)}


def _list(kind, *items):
    return {
        "type": kind,
        "content": [{"type": "listItem", "content": [_paragraph(item)]} for item in items],
    }


RICH_BODY = _doc(
    _heading(1, _text("A Heading")),
    _paragraph(
        _text("Bold", marks=[{"type": "bold"}]),
        _text(" and "),
        _text("italic", marks=[{"type": "italic"}]),
        _text(" and "),
        _text("underlined", marks=[{"type": "underline"}]),
        _text(" and a "),
        _text("link", marks=[{"type": "link", "attrs": {"href": "https://example.com"}}]),
        _text("."),
    ),
    _list("bulletList", _text("bullet one"), _text("bullet two")),
    _list("orderedList", _text("first"), _text("second")),
)


def _note_with_rich_body(owner, *, title="Rich Note"):
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title=title)
    services.save_note(note=note, title=note.title, body_json=RICH_BODY, version=note.version)
    note.refresh_from_db()
    return note


def _url(note_id):
    return reverse("notes:download_markdown", args=[note_id])


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_can_download_own_active_note_as_markdown():
    owner = create_account("md-owner")
    note = _note_with_rich_body(owner)

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 200


@pytest.mark.django_db
def test_response_content_type_is_text_markdown_utf8():
    owner = create_account("md-content-type-owner")
    note = _note_with_rich_body(owner)

    response = authenticated_client(owner).get(_url(note.id))

    assert response["Content-Type"] == "text/markdown; charset=utf-8"


@pytest.mark.django_db
def test_content_disposition_filename_is_slugified_title_and_id():
    owner = create_account("md-filename-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="My Server Notes")

    response = authenticated_client(owner).get(_url(note.id))

    assert response["Content-Disposition"] == (
        f'attachment; filename="my-server-notes-{note.id}.md"'
    )


@pytest.mark.django_db
def test_blank_or_degenerate_title_falls_back_to_note():
    owner = create_account("md-blank-title-owner")
    note = services.create_note(owner=owner)
    Note.objects.filter(pk=note.pk).update(title="***")
    note.refresh_from_db()

    response = authenticated_client(owner).get(_url(note.id))

    assert response["Content-Disposition"] == f'attachment; filename="note-{note.id}.md"'


@pytest.mark.django_db
def test_output_matches_direct_note_to_markdown_call_exactly():
    owner = create_account("md-equivalence-owner")
    note = _note_with_rich_body(owner, title="Equivalence Note")

    response = authenticated_client(owner).get(_url(note.id))
    expected = note_to_markdown(note.title, note.body_json)

    assert response.content.decode("utf-8") == expected


@pytest.mark.django_db
def test_title_appears_as_leading_h1():
    owner = create_account("md-h1-owner")
    note = _note_with_rich_body(owner, title="Heading Check")

    response = authenticated_client(owner).get(_url(note.id))

    assert response.content.decode("utf-8").startswith("# Heading Check\n\n")


@pytest.mark.django_db
def test_representative_rich_text_survives_conversion():
    owner = create_account("md-rich-text-owner")
    note = _note_with_rich_body(owner)

    text = authenticated_client(owner).get(_url(note.id)).content.decode("utf-8")

    assert "# A Heading" in text
    assert "**Bold**" in text
    assert "_italic_" in text
    assert "<u>underlined</u>" in text
    assert "[link](https://example.com)" in text
    assert "- bullet one" in text
    assert "1. first" in text


@pytest.mark.django_db
def test_blank_body_produces_title_only_output():
    owner = create_account("md-blank-body-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Empty Body Note")

    response = authenticated_client(owner).get(_url(note.id))

    assert response.content.decode("utf-8") == "# Empty Body Note\n"


@pytest.mark.django_db
def test_output_has_exactly_one_trailing_newline():
    owner = create_account("md-trailing-newline-owner")
    note = _note_with_rich_body(owner)

    text = authenticated_client(owner).get(_url(note.id)).content.decode("utf-8")

    assert text.endswith("\n")
    assert not text.endswith("\n\n")


@pytest.mark.django_db
def test_unicode_content_preserved():
    owner = create_account("md-unicode-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Café Ünïcödé")
    body = _doc(_paragraph(_text("héllo wörld 日本語")))
    services.save_note(note=note, title=note.title, body_json=body, version=note.version)
    note.refresh_from_db()

    text = authenticated_client(owner).get(_url(note.id)).content.decode("utf-8")

    assert "Café Ünïcödé" in text
    assert "héllo wörld 日本語" in text


# ---------------------------------------------------------------------------
# Authorization / non-disclosure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_owner_returns_404():
    owner = create_account("md-cross-owner-a")
    other = create_account("md-cross-owner-b")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).get(_url(note.id))

    assert response.status_code == 404


@pytest.mark.django_db
def test_missing_note_returns_404():
    owner = create_account("md-missing-owner")

    response = authenticated_client(owner).get(_url(999999))

    assert response.status_code == 404


@pytest.mark.django_db
def test_unauthenticated_is_blocked():
    owner = create_account("md-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().get(_url(note.id))

    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Lifecycle access -- inherits the shared Active-only rule
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_visible_trash_blocks_markdown_download():
    owner = create_account("md-trash-owner")
    note = _note_with_rich_body(owner, title="Trashed Note")
    services.move_note_to_trash(note=note)

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 302
    assert response["Location"] == reverse("notes:trash")
    assert b"A Heading" not in response.content


@pytest.mark.django_db
def test_administrator_recovery_only_blocks_markdown_download():
    owner = create_account("md-emptied-owner")
    note = _note_with_rich_body(owner, title="Emptied Note")
    services.move_note_to_trash(note=note)
    services.empty_trash_for_owner(owner=owner)
    note.refresh_from_db()
    assert note.emptied_at is not None

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 302
    assert response["Location"] == reverse("notes:trash")


@pytest.mark.django_db
def test_naturally_aged_administrator_recovery_only_blocks_markdown_download():
    owner = create_account("md-aged-owner")
    note = _note_with_rich_body(owner, title="Aged Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(
        trashed_at=timezone.now() - services.TRASH_VISIBLE_MAX_AGE - timedelta(days=1)
    )
    note.refresh_from_db()
    assert note.emptied_at is None

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 302
    assert response["Location"] == reverse("notes:trash")


@pytest.mark.django_db
def test_markdown_route_inherits_the_shared_content_accessibility_predicate():
    """Direct proof this route calls the same shared predicate,
    rather than re-deriving an independent (and possibly
    divergent) lifecycle check."""
    owner = create_account("md-predicate-owner")
    note = _note_with_rich_body(owner)
    services.move_note_to_trash(note=note)
    note.refresh_from_db()

    assert services.note_content_accessible_to_owner(note) is False

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Unsupported schema
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unsupported_schema_version_returns_409_not_500():
    owner = create_account("md-unsupported-version-owner")
    note = Note.objects.create(
        owner=owner,
        title="Old note",
        body_json=RICH_BODY,
        body_plain_text="",
        editor_schema_version=999,
        version=1,
    )

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 409
    assert b"unsupported document version" in response.content.lower()


# ---------------------------------------------------------------------------
# HTTP method / audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_is_allowed():
    owner = create_account("md-get-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(_url(note.id))

    assert response.status_code == 200


@pytest.mark.django_db
def test_post_is_not_allowed():
    owner = create_account("md-post-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(_url(note.id))

    assert response.status_code == 405


@pytest.mark.django_db
def test_no_audit_event_recorded():
    owner = create_account("md-audit-owner")
    note = services.create_note(owner=owner)

    authenticated_client(owner).get(_url(note.id))

    assert not AuditEvent.objects.filter(actor_id=owner.id).exists()
