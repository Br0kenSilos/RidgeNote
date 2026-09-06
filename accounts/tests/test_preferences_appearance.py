"""Appearance preference and the top-bar
quick theme selector.

Covers the per-user `theme` field on `accounts.models.User`,
`PreferencesAppearanceForm`, the `/preferences/` view's
`preferences_action=appearance` branch, the dedicated
`accounts:quick_set_theme` endpoint the top-bar selector posts to, and
Login's continued isolation from the authenticated preference. Follows
`test_preferences_tags.py`'s established shape (model defaults, DB
-level protection, form, view GET/POST, cross-action independence).
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone as django_timezone

from accounts import services
from accounts.forms import PreferencesAppearanceForm
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
def test_user_theme_defaults_to_warm_light():
    user = create_account("model-default-theme-user")

    assert user.theme == "warm-light"


@pytest.mark.django_db
def test_user_theme_persists():
    user = create_account("model-persist-theme-user")

    user.theme = "dark"
    user.save(update_fields=["theme"])
    user.refresh_from_db()

    assert user.theme == "dark"


# ---------------------------------------------------------------------------
# DB-level protection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invalid_theme_cannot_be_persisted_at_the_db_level():
    user = create_account("model-invalid-theme-db-user")
    user.theme = "not-a-real-theme"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save(update_fields=["theme"])


# ---------------------------------------------------------------------------
# PreferencesAppearanceForm
# ---------------------------------------------------------------------------


def test_preferences_appearance_form_accepts_warm_light():
    form = PreferencesAppearanceForm({"theme": "warm-light"})

    assert form.is_valid()
    assert form.cleaned_data["theme"] == "warm-light"


def test_preferences_appearance_form_accepts_dark():
    form = PreferencesAppearanceForm({"theme": "dark"})

    assert form.is_valid()
    assert form.cleaned_data["theme"] == "dark"


def test_preferences_appearance_form_rejects_invalid_theme():
    form = PreferencesAppearanceForm({"theme": "not-a-real-theme"})

    assert not form.is_valid()
    assert "theme" in form.errors


def test_preferences_appearance_form_requires_a_theme():
    form = PreferencesAppearanceForm({})

    assert not form.is_valid()
    assert "theme" in form.errors


# ---------------------------------------------------------------------------
# Preferences view -- GET
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_page_get_renders_appearance_section():
    user = create_account("view-appearance-get-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Appearance" in content
    assert 'name="theme"' in content
    assert "Warm Light" in content
    assert "Dark" in content


@pytest.mark.django_db
def test_preferences_page_get_shows_current_theme_selected():
    user = create_account("view-appearance-current-user", theme="dark")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    match = re.search(r'<select[^>]*name="theme"[^>]*>.*?</select>', content, re.DOTALL)
    assert match is not None
    assert '<option value="dark" selected>' in match.group(0)


# ---------------------------------------------------------------------------
# Preferences view -- POST (appearance action)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_page_appearance_post_saves_only_current_user():
    owner = create_account("view-appearance-owner")
    other = create_account("view-appearance-other")
    client = authenticated_client(owner)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": "dark"},
    )

    assert response.status_code == 302
    owner.refresh_from_db()
    other.refresh_from_db()
    assert owner.theme == "dark"
    assert other.theme == "warm-light"


@pytest.mark.django_db
def test_preferences_page_appearance_post_redirects_to_preferences():
    user = create_account("view-appearance-redirect-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": "dark"},
    )

    assert response.url == reverse("accounts:preferences")


@pytest.mark.django_db
def test_preferences_page_appearance_post_success_shows_message():
    user = create_account("view-appearance-success-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": "dark"},
        follow=True,
    )

    assert b"Your appearance preference has been saved." in response.content


@pytest.mark.django_db
def test_preferences_page_appearance_post_invalid_theme_shows_safe_error_and_does_not_save():
    user = create_account("view-appearance-invalid-user", theme="dark")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": "not-a-real-theme"},
        follow=True,
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.theme == "dark"
    assert 'class="messages__item messages__item--error"' in content


# ---------------------------------------------------------------------------
# Timezone/Tags/Appearance actions stay independent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_appearance_post_never_touches_timezone_or_tags():
    user = create_account(
        "view-independent-appearance-user",
        timezone_name="Europe/London",
        tag_default_color="rose",
    )
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": "dark"},
    )

    user.refresh_from_db()
    assert user.timezone_name == "Europe/London"
    assert user.tag_default_color == "rose"


@pytest.mark.django_db
def test_tags_post_never_touches_theme():
    user = create_account("view-independent-tags-theme-user", theme="dark")
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "tags", "tag_default_color": "amber"},
    )

    user.refresh_from_db()
    assert user.theme == "dark"


@pytest.mark.django_db
def test_preferences_post_with_no_recognized_action_saves_nothing_including_theme():
    user = create_account("view-no-action-theme-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:preferences"), {"theme": "dark"})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.theme == "warm-light"


# ---------------------------------------------------------------------------
# Quick-set theme endpoint (top-bar selector)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_quick_set_theme_requires_authentication():
    response = Client().post(reverse("accounts:quick_set_theme"), {"theme": "dark"})

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_quick_set_theme_requires_post():
    user = create_account("quick-theme-get-user")
    client = authenticated_client(user)

    response = client.get(reverse("accounts:quick_set_theme"))

    assert response.status_code == 405


@pytest.mark.django_db
def test_quick_set_theme_valid_update_persists():
    user = create_account("quick-theme-valid-user")
    client = authenticated_client(user)

    client.post(reverse("accounts:quick_set_theme"), {"theme": "dark"})

    user.refresh_from_db()
    assert user.theme == "dark"


@pytest.mark.django_db
def test_quick_set_theme_invalid_value_rejected_and_not_saved():
    user = create_account("quick-theme-invalid-user", theme="warm-light")
    client = authenticated_client(user)

    client.post(reverse("accounts:quick_set_theme"), {"theme": "not-a-real-theme"})

    user.refresh_from_db()
    assert user.theme == "warm-light"


@pytest.mark.django_db
def test_quick_set_theme_only_affects_the_current_user():
    owner = create_account("quick-theme-owner")
    other = create_account("quick-theme-other")
    client = authenticated_client(owner)

    client.post(reverse("accounts:quick_set_theme"), {"theme": "dark"})

    owner.refresh_from_db()
    other.refresh_from_db()
    assert owner.theme == "dark"
    assert other.theme == "warm-light"


@pytest.mark.django_db
def test_quick_set_theme_redirects_to_safe_next_url():
    user = create_account("quick-theme-next-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:quick_set_theme"),
        {"theme": "dark", "next": reverse("accounts:preferences")},
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:preferences")


@pytest.mark.django_db
def test_quick_set_theme_rejects_unsafe_next_url_and_falls_back_to_home():
    user = create_account("quick-theme-unsafe-next-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:quick_set_theme"),
        {"theme": "dark", "next": "https://evil.example.com/"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
def test_quick_set_theme_defaults_to_home_with_no_next():
    user = create_account("quick-theme-no-next-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:quick_set_theme"), {"theme": "dark"})

    assert response.url == reverse("home")


# ---------------------------------------------------------------------------
# Persisted theme reflected in authenticated page render/bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_authenticated_page_renders_persisted_dark_theme_server_side():
    # `data-theme` must be
    # server-rendered on *both* `<html>` (the canonical/authoritative
    # element `:root`'s own token cascade depends on) and `<body>` (kept
    # in sync for compatibility) -- not merely somewhere in the
    # document. A bare substring count of 2 alone wouldn't distinguish
    # "both elements carry it" from "it appears twice for an unrelated
    # reason," so each element is matched explicitly.
    user = create_account("bootstrap-dark-user", theme="dark")
    client = authenticated_client(user)

    response = client.get(reverse("home"))
    content = response.content.decode()

    assert '<html lang="en" data-theme="dark">' in content
    assert 'data-theme="dark"' in content[content.index("<body") :]


@pytest.mark.django_db
def test_authenticated_page_renders_persisted_warm_light_theme_server_side():
    user = create_account("bootstrap-warm-light-user", theme="warm-light")
    client = authenticated_client(user)

    response = client.get(reverse("home"))
    content = response.content.decode()

    assert '<html lang="en" data-theme="warm-light">' in content
    assert 'data-theme="warm-light"' in content[content.index("<body") :]


# ---------------------------------------------------------------------------
# Login remains dark-only, isolated from the authenticated preference
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_page_remains_dark_only_regardless_of_saved_user_theme():
    # Login is rendered unauthenticated -- no request.user.theme exists
    # yet -- but this also guards against any future regression that
    # tried to read a saved preference into the login page. An admin
    # must exist for `/login/` to render rather than redirect to
    # `/setup/`; that admin's own saved theme is deliberately
    # `warm-light` here, the opposite of login's fixed dark shell.
    create_account("login-isolation-admin", role=User.ROLE_ADMIN, theme="warm-light")

    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()

    # Both the canonical `<html>` element and `<body>` must be dark --
    # not just whichever one happens to be checked.
    assert '<html lang="en" data-theme="dark">' in content
    assert '<body data-theme="dark" class="login-page">' in content


@pytest.mark.django_db
def test_login_page_shows_no_theme_quick_selector():
    create_account("login-no-selector-admin", role=User.ROLE_ADMIN)

    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()

    assert "data-theme-quick-form" not in content
