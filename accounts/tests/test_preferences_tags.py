"""Tag Preferences.

Covers the three per-user Tag-creation preferences
(`tag_uppercase_enabled`, `tag_semantic_color_enabled`,
`tag_default_color`) on `accounts.models.User`, `PreferencesTagsForm`,
and the `/preferences/` view's `preferences_action` discriminator
that lets the Timezone and Tags sections save independently. Tag
creation/rename/color-resolution *behavior* under these preferences is
covered in `notes/tests/test_tags.py` and
`accounts/tests/test_tag_management.py`; this file is scoped to the
preferences themselves (model, form, view, isolation, persistence).
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone as django_timezone

from accounts import services
from accounts.forms import PreferencesTagsForm
from accounts.models import User

PASSWORD = "LongUniquePassword123!"


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", django_timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def _checkbox_is_checked(content: str, field_name: str) -> bool:
    # Django's checkbox widget renders attributes as `type="checkbox"
    # name="..." id="..." checked` -- `id="..."` sits between `name=` and
    # `checked`, so a naive adjacent-substring check is wrong; this finds
    # the specific `<input>` tag and checks within it.
    match = re.search(rf'<input[^>]*name="{field_name}"[^>]*>', content)
    assert match is not None, f"no checkbox input found for {field_name}"
    return "checked" in match.group(0)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = django_timezone.now().timestamp()
    session.save()
    return client


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_user_tag_uppercase_enabled_defaults_to_true():
    user = create_account("model-default-uppercase-user")

    assert user.tag_uppercase_enabled is True


@pytest.mark.django_db
def test_user_tag_semantic_color_enabled_defaults_to_true():
    user = create_account("model-default-semantic-user")

    assert user.tag_semantic_color_enabled is True


@pytest.mark.django_db
def test_user_tag_default_color_defaults_to_slate():
    user = create_account("model-default-color-user")

    assert user.tag_default_color == "slate"


@pytest.mark.django_db
def test_user_tag_preferences_persist():
    user = create_account("model-persist-user")

    user.tag_uppercase_enabled = False
    user.tag_semantic_color_enabled = False
    user.tag_default_color = "violet"
    user.save(
        update_fields=["tag_uppercase_enabled", "tag_semantic_color_enabled", "tag_default_color"]
    )
    user.refresh_from_db()

    assert user.tag_uppercase_enabled is False
    assert user.tag_semantic_color_enabled is False
    assert user.tag_default_color == "violet"


# ---------------------------------------------------------------------------
# DB-level protection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invalid_tag_default_color_cannot_be_persisted_at_the_db_level():
    user = create_account("model-invalid-color-db-user")
    user.tag_default_color = "not-a-real-color"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save(update_fields=["tag_default_color"])


# ---------------------------------------------------------------------------
# PreferencesTagsForm
# ---------------------------------------------------------------------------


def test_preferences_tags_form_accepts_all_defaults():
    form = PreferencesTagsForm(
        {
            "tag_uppercase_enabled": "on",
            "tag_semantic_color_enabled": "on",
            "tag_default_color": "slate",
        }
    )

    assert form.is_valid()
    assert form.cleaned_data["tag_uppercase_enabled"] is True
    assert form.cleaned_data["tag_semantic_color_enabled"] is True
    assert form.cleaned_data["tag_default_color"] == "slate"


def test_preferences_tags_form_unchecked_checkboxes_are_false():
    # Standard HTML checkbox semantics: an unchecked box sends no field
    # at all, and `BooleanField(required=False)` must interpret that as
    # False, not raise.
    form = PreferencesTagsForm({"tag_default_color": "slate"})

    assert form.is_valid()
    assert form.cleaned_data["tag_uppercase_enabled"] is False
    assert form.cleaned_data["tag_semantic_color_enabled"] is False


def test_preferences_tags_form_accepts_every_valid_color():
    for color in (
        "slate",
        "blue",
        "teal",
        "green",
        "yellow",
        "amber",
        "orange",
        "red",
        "rose",
        "violet",
        "brown",
    ):
        form = PreferencesTagsForm({"tag_default_color": color})
        assert form.is_valid(), color


def test_preferences_tags_form_rejects_invalid_color():
    form = PreferencesTagsForm({"tag_default_color": "not-a-real-color"})

    assert not form.is_valid()
    assert "tag_default_color" in form.errors


def test_preferences_tags_form_requires_a_default_color():
    form = PreferencesTagsForm({})

    assert not form.is_valid()
    assert "tag_default_color" in form.errors


# ---------------------------------------------------------------------------
# Preferences view -- GET
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_page_get_renders_tags_section():
    user = create_account("view-tags-get-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Tags" in content
    assert 'name="tag_uppercase_enabled"' in content
    assert 'name="tag_semantic_color_enabled"' in content
    assert 'name="tag_default_color"' in content


@pytest.mark.django_db
def test_preferences_page_get_shows_current_tag_preference_values():
    user = create_account(
        "view-tags-current-user",
        tag_uppercase_enabled=False,
        tag_semantic_color_enabled=False,
        tag_default_color="violet",
    )

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    # The two checked-by-default checkboxes must NOT be checked here.
    assert _checkbox_is_checked(content, "tag_uppercase_enabled") is False
    assert _checkbox_is_checked(content, "tag_semantic_color_enabled") is False
    assert 'value="violet" selected' in content


@pytest.mark.django_db
def test_preferences_page_get_defaults_are_checked():
    user = create_account("view-tags-defaults-checked-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert _checkbox_is_checked(content, "tag_uppercase_enabled") is True
    assert _checkbox_is_checked(content, "tag_semantic_color_enabled") is True


@pytest.mark.django_db
def test_preferences_page_get_still_preserves_tag_manager_cross_link():
    user = create_account("view-tags-crosslink-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert reverse("accounts:tag_management").encode() in response.content
    assert "Tag manager" in content


# ---------------------------------------------------------------------------
# Preferences view -- POST (tags action)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_page_tags_post_saves_only_current_user():
    owner = create_account("view-tags-owner")
    other = create_account("view-tags-other")
    client = authenticated_client(owner)

    response = client.post(
        reverse("accounts:preferences"),
        {
            "preferences_action": "tags",
            "tag_default_color": "rose",
            # uppercase/semantic omitted -> unchecked -> False
        },
    )

    assert response.status_code == 302
    owner.refresh_from_db()
    other.refresh_from_db()
    assert owner.tag_uppercase_enabled is False
    assert owner.tag_semantic_color_enabled is False
    assert owner.tag_default_color == "rose"
    # Cross-owner isolation: the other owner's preferences are untouched.
    assert other.tag_uppercase_enabled is True
    assert other.tag_semantic_color_enabled is True
    assert other.tag_default_color == "slate"


@pytest.mark.django_db
def test_preferences_page_tags_post_redirects_to_preferences():
    user = create_account("view-tags-redirect-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "tags", "tag_default_color": "teal"},
    )

    assert response.url == reverse("accounts:preferences")


@pytest.mark.django_db
def test_preferences_page_tags_post_success_shows_message():
    user = create_account("view-tags-success-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {
            "preferences_action": "tags",
            "tag_uppercase_enabled": "on",
            "tag_semantic_color_enabled": "on",
            "tag_default_color": "teal",
        },
        follow=True,
    )

    assert b"Your tag preferences have been saved." in response.content


@pytest.mark.django_db
def test_preferences_page_tags_post_invalid_color_shows_safe_error_and_does_not_save():
    user = create_account("view-tags-invalid-user", tag_default_color="blue")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "tags", "tag_default_color": "not-a-real-color"},
        follow=True,
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.tag_default_color == "blue"
    assert 'class="messages__item messages__item--error"' in content


@pytest.mark.django_db
def test_preferences_page_tags_post_can_re_enable_after_disabling():
    user = create_account(
        "view-tags-reenable-user", tag_uppercase_enabled=False, tag_semantic_color_enabled=False
    )
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {
            "preferences_action": "tags",
            "tag_uppercase_enabled": "on",
            "tag_semantic_color_enabled": "on",
            "tag_default_color": "slate",
        },
    )

    user.refresh_from_db()
    assert user.tag_uppercase_enabled is True
    assert user.tag_semantic_color_enabled is True


# ---------------------------------------------------------------------------
# Timezone/Tags actions stay independent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_timezone_post_never_touches_tag_preferences():
    user = create_account(
        "view-independent-tz-user", tag_uppercase_enabled=False, tag_default_color="rose"
    )
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": "America/Denver"},
    )

    user.refresh_from_db()
    assert user.tag_uppercase_enabled is False
    assert user.tag_default_color == "rose"


@pytest.mark.django_db
def test_tags_post_never_touches_timezone():
    user = create_account("view-independent-tags-user", timezone_name="Europe/London")
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "tags", "tag_default_color": "amber"},
    )

    user.refresh_from_db()
    assert user.timezone_name == "Europe/London"


@pytest.mark.django_db
def test_preferences_post_with_no_recognized_action_saves_nothing():
    user = create_account("view-no-action-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:preferences"), {"tag_default_color": "violet"})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.tag_default_color == "slate"
