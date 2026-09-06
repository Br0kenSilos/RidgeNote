"""Production Theme Promotion.

Covers the six themes promoted from their approved Palette Lab
candidates (Glacier/Granite/Alpine Mist/Blue Dusk/Midnight Ridge/
Nightfall) into `core.themes.THEME_CHOICES`, the widened
`accounts.User.theme` field/DB constraint (migration
`0010_user_theme_production_promotion`), and the Preferences Appearance
selector. Palette Lab isolation is re-verified from the production
side: none of the promoted values collides with a Palette Lab id, and
no dropped/exploratory Palette Lab candidate id is a valid production
theme.
"""

import pytest
from core.themes import DEFAULT_THEME, THEME_CHOICES, THEME_VALUES
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import User
from accounts.tests.test_accounts import authenticated_client, create_account

EXPECTED_THEME_CHOICES = (
    ("warm-light", "Warm Light"),
    ("dark", "Dark"),
    ("glacier", "Glacier"),
    ("granite", "Granite"),
    ("alpine-mist", "Alpine Mist"),
    ("blue-dusk", "Blue Dusk"),
    ("midnight-ridge", "Midnight Ridge"),
    ("nightfall", "Nightfall"),
)


def test_theme_choices_is_exactly_the_eight_approved_themes_in_order():
    assert THEME_CHOICES == EXPECTED_THEME_CHOICES


def test_theme_values_has_exactly_eight_entries():
    assert len(THEME_VALUES) == 8
    assert THEME_VALUES == tuple(value for value, _label in EXPECTED_THEME_CHOICES)


def test_default_theme_remains_warm_light():
    assert DEFAULT_THEME == "warm-light"


@pytest.mark.django_db
@pytest.mark.parametrize("theme_value", [value for value, _label in EXPECTED_THEME_CHOICES])
def test_user_theme_accepts_every_approved_value(theme_value):
    user = create_account(f"theme-accept-{theme_value.replace('-', '_')}", theme=theme_value)

    user.refresh_from_db()

    assert user.theme == theme_value


@pytest.mark.django_db
def test_invalid_theme_still_rejected_at_the_db_level_after_promotion():
    user = create_account("theme-still-invalid-user")
    user.theme = "not-a-real-theme"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save(update_fields=["theme"])


@pytest.mark.django_db
def test_palette_lab_id_is_not_a_valid_production_theme_value():
    user = create_account("theme-palette-lab-id-user")
    user.theme = "cool-a"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save(update_fields=["theme"])


@pytest.mark.django_db
def test_dropped_palette_lab_candidate_id_is_not_a_valid_production_theme_value():
    user = create_account("theme-dropped-candidate-user")
    user.theme = "dim-a"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            user.save(update_fields=["theme"])


def test_production_theme_values_are_disjoint_from_palette_lab_candidate_ids():
    from core.palette_lab import PALETTE_LAB_CANDIDATE_IDS

    assert set(THEME_VALUES).isdisjoint(set(PALETTE_LAB_CANDIDATE_IDS))


@pytest.mark.django_db
def test_preferences_page_renders_all_eight_approved_theme_names():
    user = create_account("preferences-eight-themes-user")

    response = authenticated_client(user).get(reverse("accounts:preferences"))
    content = response.content.decode()

    assert response.status_code == 200
    for _value, label in EXPECTED_THEME_CHOICES:
        assert label in content


@pytest.mark.django_db
@pytest.mark.parametrize("theme_value", [value for value, _label in EXPECTED_THEME_CHOICES])
def test_preferences_appearance_post_accepts_every_approved_value(theme_value):
    user = create_account(f"preferences-post-{theme_value.replace('-', '_')}")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:preferences"),
        {"preferences_action": "appearance", "theme": theme_value},
    )

    user.refresh_from_db()
    assert response.status_code == 302
    assert user.theme == theme_value


@pytest.mark.django_db
@pytest.mark.parametrize("theme_value", [value for value, _label in EXPECTED_THEME_CHOICES])
def test_quick_set_theme_accepts_every_approved_value(theme_value):
    user = create_account(f"quick-set-{theme_value.replace('-', '_')}")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:quick_set_theme"), {"theme": theme_value})

    user.refresh_from_db()
    assert response.status_code == 302
    assert user.theme == theme_value


@pytest.mark.django_db
def test_authenticated_page_renders_html_and_body_data_theme_for_a_promoted_value():
    user = create_account("promoted-render-user", theme="midnight-ridge")

    response = authenticated_client(user).get(reverse("home"))
    content = response.content.decode()

    assert '<html lang="en" data-theme="midnight-ridge">' in content
    assert 'data-theme="midnight-ridge"' in content.split("<body")[1].split(">")[0]


@pytest.mark.django_db
def test_login_page_remains_dark_only_regardless_of_promoted_theme():
    from django.test import Client

    admin = create_account("promoted-login-admin", role=User.ROLE_ADMIN, theme="nightfall")

    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()

    assert '<html lang="en" data-theme="dark">' in content
    assert 'data-theme="dark" class="login-page"' in content
    assert admin.theme == "nightfall"
