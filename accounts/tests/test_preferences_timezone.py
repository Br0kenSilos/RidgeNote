"""Per-user timezone foundation.

Covers `accounts.User.timezone_name`, `PreferencesTimezoneForm`,
`services.resolve_display_timezone()`/`is_valid_timezone_name()`, and the
`/preferences/` view. The timezone preference lives under Preferences,
not Account (which stays scoped to identity/security/library backup);
`/account/` does not expose timezone UI or POST handling at all;
regression coverage for that boundary lives here alongside the
Preferences coverage. Request-level `timezone.activate()`/global
template-datetime rendering is out of scope here --
not tested in this file.
"""

from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone as django_timezone

from accounts import services
from accounts.forms import PreferencesTimezoneForm
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
# Model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_user_timezone_name_defaults_to_blank():
    user = create_account("model-default-user")

    assert user.timezone_name == ""


@pytest.mark.django_db
def test_user_timezone_name_persists_a_valid_value():
    user = create_account("model-persist-user")

    user.timezone_name = "America/New_York"
    user.save(update_fields=["timezone_name"])
    user.refresh_from_db()

    assert user.timezone_name == "America/New_York"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_is_valid_timezone_name_accepts_modern_iana_zone():
    assert services.is_valid_timezone_name("America/New_York") is True


def test_is_valid_timezone_name_accepts_legacy_alias():
    assert services.is_valid_timezone_name("US/Eastern") is True


def test_is_valid_timezone_name_rejects_unknown_string():
    assert services.is_valid_timezone_name("Not/AZone") is False


def test_is_valid_timezone_name_rejects_raw_utc_offset():
    assert services.is_valid_timezone_name("UTC-5") is False


def test_is_valid_timezone_name_rejects_unrecognized_abbreviation():
    # "PST" (unlike the fixed-offset legacy zone "EST", which tzdata does
    # recognize and this membership check therefore legitimately accepts)
    # is not itself a valid IANA identifier on this system's tzdata.
    assert services.is_valid_timezone_name("PST") is False


def test_preferences_timezone_form_accepts_blank_as_unset():
    form = PreferencesTimezoneForm({"timezone_name": ""})

    assert form.is_valid()
    assert form.cleaned_data["timezone_name"] == ""


def test_preferences_timezone_form_accepts_valid_zone():
    form = PreferencesTimezoneForm({"timezone_name": "Europe/London"})

    assert form.is_valid()
    assert form.cleaned_data["timezone_name"] == "Europe/London"


def test_preferences_timezone_form_accepts_legacy_alias_uncanonicalized():
    form = PreferencesTimezoneForm({"timezone_name": "US/Eastern"})

    assert form.is_valid()
    assert form.cleaned_data["timezone_name"] == "US/Eastern"


def test_preferences_timezone_form_rejects_invalid_zone():
    form = PreferencesTimezoneForm({"timezone_name": "Mars/OlympusMons"})

    assert not form.is_valid()
    assert "recognized timezone" in form.errors["timezone_name"][0]


def test_preferences_timezone_form_strips_surrounding_whitespace():
    form = PreferencesTimezoneForm({"timezone_name": "  America/Chicago  "})

    assert form.is_valid()
    assert form.cleaned_data["timezone_name"] == "America/Chicago"


# ---------------------------------------------------------------------------
# Helper: resolve_display_timezone()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_display_timezone_uses_explicit_valid_user_value():
    user = create_account("helper-explicit-user", timezone_name="Asia/Tokyo")

    assert services.resolve_display_timezone(user) == ZoneInfo("Asia/Tokyo")


@pytest.mark.django_db
def test_resolve_display_timezone_falls_back_to_settings_time_zone_when_blank():
    user = create_account("helper-blank-user", timezone_name="")

    assert services.resolve_display_timezone(user) == ZoneInfo(settings.TIME_ZONE)


@pytest.mark.django_db
def test_resolve_display_timezone_falls_back_when_stored_value_is_no_longer_valid(monkeypatch):
    # Simulates a value that passed validation when saved but can no
    # longer be resolved (e.g. a future tzdata removal) -- must degrade
    # safely, never raise, and never break the caller (a backup
    # download in production).
    user = create_account("helper-stale-user", timezone_name="Some/StaleZone")
    monkeypatch.setattr(services, "is_valid_timezone_name", lambda _name: False)

    assert services.resolve_display_timezone(user) == ZoneInfo(settings.TIME_ZONE)


@pytest.mark.django_db
def test_resolve_display_timezone_falls_back_to_utc_when_settings_time_zone_invalid(monkeypatch):
    monkeypatch.setattr(settings, "TIME_ZONE", "Not/AZone")
    user = create_account("helper-bad-settings-user", timezone_name="")

    assert services.resolve_display_timezone(user) == ZoneInfo("UTC")


def test_resolve_display_timezone_handles_none_user():
    assert services.resolve_display_timezone(None) == ZoneInfo(settings.TIME_ZONE)


# ---------------------------------------------------------------------------
# Preferences view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_page_redirects_anonymous_users():
    response = Client().get(reverse("accounts:preferences"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_preferences_page_get_renders_timezone_section():
    user = create_account("view-tz-get-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Preferences" in content
    assert "Timezone" in content
    assert 'name="timezone_name"' in content


@pytest.mark.django_db
def test_preferences_page_get_shows_saved_timezone_value():
    user = create_account("view-tz-saved-user", timezone_name="Europe/London")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert 'value="Europe/London"' in content


@pytest.mark.django_db
def test_preferences_page_get_explains_fallback_when_unset():
    user = create_account("view-tz-unset-user", timezone_name="")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert settings.TIME_ZONE in content
    assert "No timezone is saved" in content


@pytest.mark.django_db
def test_preferences_page_get_lists_complete_timezone_options():
    user = create_account("view-tz-options-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert '<datalist id="timezone-options">' in content
    for zone in ("America/New_York", "Europe/London", "Asia/Tokyo", "Australia/Sydney"):
        assert f'<option value="{zone}">' in content


@pytest.mark.django_db
def test_preferences_page_timezone_post_saves_only_current_user():
    owner = create_account("view-tz-owner")
    other = create_account("view-tz-other")
    client = authenticated_client(owner)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": "America/Denver"},
    )

    assert response.status_code == 302
    owner.refresh_from_db()
    other.refresh_from_db()
    assert owner.timezone_name == "America/Denver"
    assert other.timezone_name == ""


@pytest.mark.django_db
def test_preferences_page_timezone_post_redirects_to_preferences():
    user = create_account("view-tz-redirect-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": "America/Denver"},
    )

    assert response.url == reverse("accounts:preferences")


@pytest.mark.django_db
def test_preferences_page_timezone_post_success_shows_message():
    user = create_account("view-tz-success-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": "America/Denver"},
        follow=True,
    )

    assert b"Your timezone has been saved." in response.content


@pytest.mark.django_db
def test_preferences_page_timezone_post_invalid_value_shows_safe_error_and_does_not_save():
    user = create_account("view-tz-invalid-user", timezone_name="America/Denver")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": "Not/AZone"},
        follow=True,
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.timezone_name == "America/Denver"
    assert "recognized timezone" in content
    assert 'class="messages__item messages__item--error"' in content


@pytest.mark.django_db
def test_preferences_page_timezone_post_can_clear_a_saved_value():
    user = create_account("view-tz-clear-user", timezone_name="America/Denver")
    client = authenticated_client(user)

    client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "timezone", "timezone_name": ""},
    )

    user.refresh_from_db()
    assert user.timezone_name == ""


@pytest.mark.django_db
def test_preferences_menu_link_resolves_and_is_reachable():
    user = create_account("view-tz-menu-user")
    client = authenticated_client(user)

    home = client.get(reverse("home"))
    preferences_url = reverse("accounts:preferences")

    assert preferences_url.encode() in home.content
    assert client.get(preferences_url).status_code == 200


# ---------------------------------------------------------------------------
# Account regression -- timezone UI/handling fully removed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_page_no_longer_renders_timezone_section():
    # Scoped to the page's own
    # <main> content rather than the whole response body, since the
    # site-wide Help panel (embedded on every page, outside <main>)
    # legitimately mentions "Timezone" as an Account-and-Preferences
    # Help topic elsewhere in the same document.
    user = create_account("view-account-no-tz-user")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    main_start = content.index("<main")
    main_end = content.index("</main>", main_start)
    main_content = content[main_start:main_end]

    assert "Timezone" not in main_content
    assert 'name="timezone_name"' not in main_content
    assert "timezone-options" not in main_content


@pytest.mark.django_db
def test_account_page_password_post_still_works():
    other_password = "AnotherLongPassword123!"
    user = create_account("view-account-password-still-works-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"),
        {
            "account_action": "password",
            "current_password": PASSWORD,
            "password1": other_password,
            "password2": other_password,
        },
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password(other_password)


@pytest.mark.django_db
def test_account_page_post_never_touches_timezone_name():
    user = create_account("view-account-tz-untouched-user", timezone_name="Europe/London")
    client = authenticated_client(user)

    client.post(
        reverse("accounts:account"),
        {
            "account_action": "password",
            "current_password": PASSWORD,
            "password1": "AnotherLongPassword123!",
            "password2": "AnotherLongPassword123!",
        },
    )

    user.refresh_from_db()
    assert user.timezone_name == "Europe/London"
