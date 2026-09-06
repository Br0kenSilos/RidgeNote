import re

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


@pytest.mark.django_db
def test_download_text_view_derives_content_from_body_json():
    # The download view derives its content
    # on demand from `body_json` via `documents.derive_export_plain_text()`,
    # not from the stored `body_plain_text` field (which still exists,
    # unchanged, for search -- see `test_export_plain_text.py`).
    user = create_account("download-text-owner")
    note = services.create_note(owner=user)
    Note.objects.filter(pk=note.pk).update(
        title="My Note",
        body_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Line one"},
                        {"type": "hardBreak"},
                        {"type": "text", "text": "Line two"},
                    ],
                }
            ],
        },
        body_plain_text="stale, must not be served",
    )

    response = authenticated_client(user).get(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 200
    assert response.content.decode() == "Line one\nLine two"
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="my-note-{note.id}.txt"'


@pytest.mark.django_db
def test_download_text_view_uses_safe_fallback_filename_for_untitled_note():
    user = create_account("download-text-untitled-owner")
    note = services.create_note(owner=user)
    Note.objects.filter(pk=note.pk).update(title="", body_plain_text="")

    response = authenticated_client(user).get(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 200
    assert response["Content-Disposition"] == f'attachment; filename="note-{note.id}.txt"'


@pytest.mark.django_db
def test_download_text_view_slugifies_unusual_title_characters():
    user = create_account("download-text-unusual-owner")
    note = services.create_note(owner=user)
    Note.objects.filter(pk=note.pk).update(title='Weird & "Title" <Name>')

    response = authenticated_client(user).get(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 200
    expected = f'attachment; filename="weird-title-name-{note.id}.txt"'
    assert response["Content-Disposition"] == expected


@pytest.mark.django_db
def test_download_text_view_works_identically_for_plain_and_rich_notes():
    user = create_account("download-text-modes-owner")
    plain_note = services.create_note(owner=user)
    Note.objects.filter(pk=plain_note.pk).update(
        body_json={
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Plain content"}]}
            ],
        },
    )

    rich_note = services.create_note(owner=user)
    Note.objects.filter(pk=rich_note.pk).update(
        editor_schema_version=1,
        body_json={
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Rich content"}]}
            ],
        },
    )

    plain_response = authenticated_client(user).get(
        reverse("notes:download_text", args=[plain_note.id])
    )
    rich_response = authenticated_client(user).get(
        reverse("notes:download_text", args=[rich_note.id])
    )

    assert plain_response.content.decode() == "Plain content"
    assert rich_response.content.decode() == "Rich content"


@pytest.mark.django_db
def test_download_text_view_cross_owner_note_returns_404():
    owner = create_account("download-text-cross-owner")
    other = create_account("download-text-cross-other")
    note = services.create_note(owner=owner)

    response = authenticated_client(other).get(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_download_text_view_unauthenticated_redirects_to_login():
    owner = create_account("download-text-unauth-owner")
    note = services.create_note(owner=owner)

    response = Client().get(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 302
    assert "/login" in response["Location"]


@pytest.mark.django_db
def test_download_text_view_post_is_not_allowed():
    user = create_account("download-text-post-owner")
    note = services.create_note(owner=user)

    response = authenticated_client(user).post(reverse("notes:download_text", args=[note.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_download_text_view_does_not_affect_json_export_route():
    user = create_account("download-text-export-parity-owner")
    note = services.create_note(owner=user)

    export_response = authenticated_client(user).get(reverse("notes:export", args=[note.id]))

    assert export_response.status_code == 200
    assert export_response["Content-Type"] == "application/json; charset=utf-8"
    assert export_response["Content-Disposition"].endswith(f'{note.id}.ridgenote.json"')


@pytest.mark.django_db
def test_download_text_view_does_not_affect_print_route():
    user = create_account("download-text-print-parity-owner")
    note = services.create_note(owner=user)

    print_response = authenticated_client(user).get(reverse("notes:print", args=[note.id]))

    assert print_response.status_code == 200


@pytest.mark.django_db
def test_note_detail_toolbar_renders_download_action():
    user = create_account("download-toolbar-owner")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    download_url = reverse("notes:download_text", args=[note.id])

    # The ribbon has a single always-in-overflow
    # copy, so the toolbar contributes one href (not two, as a
    # direct-action-plus-responsive-overflow-duplicate pair would). The tree's
    # own row-action menu contributes one copy per rendering of the
    # tree (wide pane + narrow drawer), for a total of three.
    assert content.count(f'href="{download_url}"') == 3
    assert "note-workspace__overflow-menu" in content
    assert ">Download<" in content


@pytest.mark.django_db
def test_row_action_menu_includes_download_as_a_legitimate_item():
    # Download is a legitimate row-action menu item.
    user = create_account("download-row-menu-presence-owner")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()
    download_url = reverse("notes:download_text", args=[note.id])

    panel_pattern = re.compile(
        r'<div class="tree-nav__row-menu-panel">(.*?)</div>\s*</details>', re.DOTALL
    )
    panels = panel_pattern.findall(content)
    assert len(panels) == 2
    for panel in panels:
        assert ">Download<" in panel
        assert f'href="{download_url}"' in panel
        # The row-action menu's note delete trigger is labeled
        # "Move to Trash".
        assert ">Move to Trash<" in panel
