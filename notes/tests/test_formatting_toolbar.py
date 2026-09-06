import re

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services

PASSWORD = "LongUniquePassword123!"

# The authorized command order: the eight converted icon
# commands and the four retained text commands, in their existing relative
# sequence.
TOOLBAR_COMMAND_ORDER = [
    "bold",
    "italic",
    "underline",
    "heading",
    "heading",
    "heading",
    "bulletList",
    "orderedList",
    "link",
    "clearFormatting",
    "undo",
    "redo",
]

ICON_COMMANDS = {
    "bold": ("Bold", "bold.svg"),
    "italic": ("Italic", "italic.svg"),
    "underline": ("Underline", "underline.svg"),
    "bulletList": ("Bulleted list", "list.svg"),
    "orderedList": ("Numbered list", "list-ordered.svg"),
    "link": ("Link", "link.svg"),
    "undo": ("Undo", "undo-2.svg"),
    "redo": ("Redo", "redo-2.svg"),
}

TEXT_COMMANDS = ["H1", "H2", "H3", "Clear"]


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


def _toolbar_html(content: str) -> str:
    match = re.search(
        r'<div class="editor-toolbar".*?id="note-formatting-toolbar".*?</div>',
        content,
        re.DOTALL,
    )
    assert match is not None, "formatting toolbar markup not found"
    return match.group(0)


@pytest.mark.django_db
def test_toolbar_renders_all_eight_icon_controls_with_no_visible_command_text():
    user = create_account("toolbar-icons")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    toolbar = _toolbar_html(response.content.decode())

    for command, (label, icon_file) in ICON_COMMANDS.items():
        button_match = re.search(
            rf'<button[^>]*data-note-command="{command}"[^>]*>.*?</button>',
            toolbar,
            re.DOTALL,
        )
        assert button_match is not None, f"missing icon button for {command}"
        button_html = button_match.group(0)
        assert 'class="icon-button"' in button_html
        assert f'aria-label="{label}"' in button_html
        assert f'data-tooltip="{label}"' in button_html
        assert f"core/icons/{icon_file}" not in button_html  # sanity: not a literal path
        assert "title=" not in button_html
        # No visible command text -- stripping the SVG and every tag leaves
        # only whitespace between the opening and closing button tags.
        without_svg = re.sub(r"<svg.*?</svg>", "", button_html, flags=re.DOTALL)
        without_tags = re.sub(r"<[^>]+>", "", without_svg)
        assert without_tags.strip() == ""


@pytest.mark.django_db
def test_toolbar_icon_buttons_render_svg_with_no_native_title():
    user = create_account("toolbar-svg")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    toolbar = _toolbar_html(response.content.decode())

    assert toolbar.count("<svg") == len(ICON_COMMANDS)
    assert "<title>" not in toolbar
    assert " title=" not in toolbar


@pytest.mark.django_db
def test_h1_h2_h3_clear_remain_plain_text_controls():
    user = create_account("toolbar-text")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    toolbar = _toolbar_html(response.content.decode())

    for label in TEXT_COMMANDS:
        assert f">{label}<" in toolbar

    # None of the four text controls carry the icon-button class or an SVG.
    for command in ("heading", "clearFormatting"):
        for match in re.finditer(
            rf'<button[^>]*data-note-command="{command}"[^>]*>.*?</button>',
            toolbar,
            re.DOTALL,
        ):
            button_html = match.group(0)
            assert "icon-button" not in button_html
            assert "<svg" not in button_html


@pytest.mark.django_db
def test_toolbar_command_order_is_preserved():
    user = create_account("toolbar-order")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    toolbar = _toolbar_html(response.content.decode())

    found_commands = re.findall(r'data-note-command="([^"]+)"', toolbar)
    assert found_commands == TOOLBAR_COMMAND_ORDER


@pytest.mark.django_db
def test_toolbar_is_a_single_shared_element_no_duplicate_narrow_markup():
    user = create_account("toolbar-single")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    content = response.content.decode()

    assert content.count('id="note-formatting-toolbar"') == 1
    # `data-note-toolbar-toggle` also contains the substring
    # `data-note-toolbar`, so this counts the boolean attribute precisely
    # by requiring the delimiter that follows it on the real toolbar
    # element (a space before `hidden`), not a naive substring count.
    assert content.count("data-note-toolbar\n") == 1


@pytest.mark.django_db
def test_toolbar_no_separators_introduced():
    user = create_account("toolbar-separators")
    note = services.create_note(owner=user)

    response = authenticated_client(user).get(reverse("notes:detail", args=[note.id]))
    toolbar = _toolbar_html(response.content.decode())

    assert "toolbar-separator" not in toolbar
    assert "<hr" not in toolbar
