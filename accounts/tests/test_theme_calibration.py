"""Theme / Tag Calibration Fixture.

Covers only access control and that the page renders its required
reference content -- this is an internal, admin-only maintenance page,
not a user-facing feature, so no behavior/permission/route change to
any other page is expected or tested here.
"""

import pytest
from django.test import Client
from django.urls import reverse

from accounts.tests.test_accounts import authenticated_client, create_account, create_admin


@pytest.mark.django_db
def test_theme_calibration_requires_login():
    response = Client().get(reverse("accounts:theme_calibration"))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_theme_calibration_forbidden_for_non_admin():
    user = create_account("calibration-non-admin")
    client = authenticated_client(user)

    response = client.get(reverse("accounts:theme_calibration"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_theme_calibration_accessible_to_admin():
    admin = create_admin("calibration-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))

    assert response.status_code == 200
    assert b"Theme Calibration" in response.content


@pytest.mark.django_db
def test_theme_calibration_renders_all_eleven_tag_colors():
    admin = create_admin("calibration-tags-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    for color, label in [
        ("slate", "Slate"),
        ("blue", "Blue"),
        ("teal", "Teal"),
        ("green", "Green"),
        ("yellow", "Yellow"),
        ("amber", "Amber"),
        ("orange", "Orange"),
        ("red", "Red"),
        ("rose", "Rose"),
        ("violet", "Violet"),
        ("brown", "Brown"),
    ]:
        assert f'data-tag-color="{color}"' in content
        assert label in content


@pytest.mark.django_db
def test_theme_calibration_renders_shell_candidate_reference():
    admin = create_admin("calibration-shell-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "Shell Candidate Reference" in content
    assert "Reference only" in content
    assert "theme-calibration__swatch--rail-b-rest" in content
    assert "theme-calibration__swatch--rail-c-rest" in content
    assert "theme-calibration__swatch--topbar-t1" in content
    assert "theme-calibration__swatch--topbar-t2" in content
    assert "theme-calibration__swatch--topbar-t3" in content


@pytest.mark.django_db
def test_theme_calibration_not_linked_from_account_menu():
    admin = create_admin("calibration-nav-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:account"))

    assert reverse("accounts:theme_calibration") not in response.content.decode()


# Theme Calibration / Palette Lab.
# These candidate palettes are internal design/calibration tools only,
# never real themes -- see `core/palette_lab.py`'s module docstring.
# Access-control coverage above already proves the Palette Lab section
# (rendered on the same page) is equally admin-only; these tests cover
# only the Lab's own content and its non-production isolation.


@pytest.mark.django_db
def test_theme_calibration_renders_palette_lab_section():
    admin = create_admin("calibration-lab-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "Palette Lab" in content
    assert 'data-palette-lab-root' in content
    assert 'data-palette-lab-select' in content
    assert 'data-palette-lab-preview' in content


@pytest.mark.django_db
def test_theme_calibration_renders_all_active_candidate_ids_and_labels():
    """Cool B and Dim B were
    dropped; Dim E/F/G/H were added exploring blue/slate territory,
    followed by Dim I/J/K/L exploring further blue/slate directions
    around Dim G/E. The active set is 14 candidates -- Cool A/C/D and
    Dim A/C/D/E/F/G/H/I/J/K/L."""
    admin = create_admin("calibration-lab-candidates-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    expected_ids = [
        "cool-a",
        "cool-c",
        "cool-d",
        "dim-a",
        "dim-c",
        "dim-d",
        "dim-e",
        "dim-f",
        "dim-g",
        "dim-h",
        "dim-i",
        "dim-j",
        "dim-k",
        "dim-l",
    ]
    for candidate_id in expected_ids:
        assert f'data-palette-candidate="{candidate_id}"' in content
        assert f'data-palette-lab-swatch="{candidate_id}"' in content
        assert f'value="{candidate_id}"' in content

    # The six
    # selected candidates display their production
    # name as the primary visible label, not the utilitarian "Dim E"
    # -style id-label -- exact mapping asserted in
    # test_theme_calibration_approved_names_render_for_selected_candidates
    # below. Only assert the still-unselected candidates' plain labels
    # here.
    for _candidate_id, label in [
        ("dim-a", "Dim A"),
        ("dim-c", "Dim C"),
        ("dim-d", "Dim D"),
        ("dim-f", "Dim F"),
        ("dim-h", "Dim H"),
        ("dim-j", "Dim J"),
        ("dim-k", "Dim K"),
        ("dim-l", "Dim L"),
    ]:
        assert label in content


@pytest.mark.django_db
def test_theme_calibration_palette_lab_dropped_candidates_are_absent():
    admin = create_admin("calibration-lab-dropped-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    for dropped_id in ("cool-b", "dim-b"):
        assert f'data-palette-candidate="{dropped_id}"' not in content
        assert f'data-palette-lab-swatch="{dropped_id}"' not in content
        assert f'value="{dropped_id}"' not in content


@pytest.mark.django_db
def test_theme_calibration_palette_lab_groups_by_category():
    admin = create_admin("calibration-lab-category-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert 'label="Cool Light"' in content
    assert 'label="Soft/Dim Dark"' in content


@pytest.mark.django_db
def test_theme_calibration_palette_lab_preserves_all_eleven_tag_colors():
    admin = create_admin("calibration-lab-tags-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    # The pre-existing top-level tag section is covered by
    # test_theme_calibration_renders_all_eleven_tag_colors above; this
    # confirms the Palette Lab's own preview also carries a complete
    # set, not a partial/duplicated one.
    for color in [
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
    ]:
        assert content.count(f'data-tag-color="{color}"') >= 2


@pytest.mark.django_db
def test_theme_calibration_palette_lab_renders_expanded_fixture_sections():
    admin = create_admin("calibration-lab-sections-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "Disabled input" in content
    assert "Invalid input" in content
    assert "Selected row" in content
    assert "Pinned row" in content
    assert "Dialog / modal surface" in content
    assert "Borders" in content
    assert "Editor controls" in content


def test_palette_lab_active_candidate_count_is_fourteen():
    from core.palette_lab import PALETTE_LAB_CANDIDATE_IDS

    assert len(PALETTE_LAB_CANDIDATE_IDS) == 14


# Final Candidate Naming / Selection / Shell-Treatment Pass.

APPROVED_NAME_MAPPING = {
    "cool-a": "Glacier",
    "cool-c": "Granite",
    "cool-d": "Alpine Mist",
    "dim-e": "Blue Dusk",
    "dim-g": "Midnight Ridge",
    "dim-i": "Nightfall",
}


def test_palette_lab_approved_name_mapping_is_exact():
    from core.palette_lab import PALETTE_LAB_CANDIDATES

    approved = {
        candidate["id"]: candidate["approved_name"]
        for candidate in PALETTE_LAB_CANDIDATES
        if candidate["approved_name"]
    }
    assert approved == APPROVED_NAME_MAPPING


def test_palette_lab_exactly_six_candidates_selected_for_production():
    from core.palette_lab import PALETTE_LAB_CANDIDATES

    selected = [c["id"] for c in PALETTE_LAB_CANDIDATES if c["selected_for_production"]]
    assert len(selected) == 6
    assert set(selected) == set(APPROVED_NAME_MAPPING)


def test_palette_lab_selected_ids_constant_matches_selected_flags():
    from core.palette_lab import PALETTE_LAB_CANDIDATES, PALETTE_LAB_SELECTED_IDS

    flagged = tuple(c["id"] for c in PALETTE_LAB_CANDIDATES if c["selected_for_production"])
    assert PALETTE_LAB_SELECTED_IDS == flagged
    assert set(PALETTE_LAB_SELECTED_IDS) == set(APPROVED_NAME_MAPPING)


def test_palette_lab_light_shell_flag_set_only_for_selected_dim_candidates():
    from core.palette_lab import PALETTE_LAB_CANDIDATES

    light_shell_ids = {c["id"] for c in PALETTE_LAB_CANDIDATES if c["light_shell"]}
    assert light_shell_ids == {"dim-e", "dim-g", "dim-i"}
    # Every light_shell candidate must also be selected_for_production -- the
    # flag only ever applies to a promoted candidate.
    for candidate in PALETTE_LAB_CANDIDATES:
        if candidate["light_shell"]:
            assert candidate["selected_for_production"] is True


def test_palette_lab_non_selected_candidates_have_no_approved_name():
    from core.palette_lab import PALETTE_LAB_CANDIDATES

    for candidate in PALETTE_LAB_CANDIDATES:
        if candidate["id"] not in APPROVED_NAME_MAPPING:
            assert candidate["selected_for_production"] is False
            assert candidate["approved_name"] is None


@pytest.mark.django_db
def test_theme_calibration_approved_names_render_for_selected_candidates():
    admin = create_admin("calibration-approved-names-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    for candidate_id, approved_name in APPROVED_NAME_MAPPING.items():
        assert approved_name in content
        assert f"{approved_name} ({candidate_id})" in content


@pytest.mark.django_db
def test_theme_calibration_selected_swatch_cards_show_badge():
    admin = create_admin("calibration-selected-badge-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    # "Selected for V1" appears twice per selected candidate: once in
    # the select-option text, once in the swatch-card badge.
    assert content.count("Selected for V1") == 2 * len(APPROVED_NAME_MAPPING)
    assert content.count("palette-lab__swatch-card--selected") == len(APPROVED_NAME_MAPPING)


# Shell foreground comparison -- Dim E/G/I.


@pytest.mark.django_db
def test_theme_calibration_renders_shell_comparison_for_exactly_e_g_i():
    admin = create_admin("calibration-shell-compare-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "Shell foreground" in content
    for candidate_id in ("dim-e", "dim-g", "dim-i"):
        assert (
            f'palette-lab-shell-row--current" data-palette-candidate="{candidate_id}"'
            in content
        )
        assert (
            f'palette-lab-shell-row--light" data-palette-candidate="{candidate_id}"'
            in content
        )
    # Anchored on the shell-compare section's own root attribute rather
    # than heading text, so this stays correct even if the heading copy
    # changes again later.
    shell_compare_html = content.split('data-palette-lab-shell-compare')[1]
    for excluded_id in ("dim-a", "dim-c", "dim-d", "dim-f", "dim-h", "dim-j", "dim-k", "dim-l"):
        assert f'data-palette-candidate="{excluded_id}"' not in shell_compare_html


@pytest.mark.django_db
def test_theme_calibration_shell_comparison_shows_rejected_and_selected_labels():
    """Final Candidate Naming / Selection / Shell-Treatment Pass: the
    comparison is now a design-history record with an explicit winner,
    not an open question -- the old neutral "Current"/"Light
    foreground" wording was replaced with unambiguous
    rejected/selected labels."""
    admin = create_admin("calibration-shell-compare-labels-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "Rejected current fallback" in content
    assert "Selected / approved direction" in content


def test_palette_lab_shell_compare_ids_are_exactly_dim_e_g_i():
    from core.palette_lab import PALETTE_LAB_SHELL_COMPARE_IDS

    assert PALETTE_LAB_SHELL_COMPARE_IDS == ("dim-e", "dim-g", "dim-i")


def test_palette_lab_shell_compare_ids_are_registered_active_candidates():
    from core.palette_lab import PALETTE_LAB_CANDIDATE_IDS, PALETTE_LAB_SHELL_COMPARE_IDS

    for candidate_id in PALETTE_LAB_SHELL_COMPARE_IDS:
        assert candidate_id in PALETTE_LAB_CANDIDATE_IDS


@pytest.mark.django_db
def test_theme_calibration_shell_comparison_treatment_c_does_not_exist():
    """Treatment B (unchanged shell + `--color-text-strong` foreground)
    already clears 4.5:1 for every element on all three candidates, so
    no shell-darkening Treatment C was implemented -- this guards
    against one silently appearing without an explicit authorization."""
    admin = create_admin("calibration-shell-compare-no-c-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert "palette-lab-shell-row--treatment-c" not in content
    assert "Treatment C" not in content


@pytest.mark.django_db
def test_theme_calibration_shell_comparison_does_not_alter_main_preview_default():
    """The main live preview's default candidate/id and its Palette Lab
    controls must be exactly as before this pass -- the shell comparison
    is an additional, independent section."""
    admin = create_admin("calibration-shell-compare-preview-admin")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:theme_calibration"))
    content = response.content.decode()

    assert 'data-palette-lab-preview' in content
    assert 'data-palette-candidate="cool-a"' in content


@pytest.mark.django_db
def test_theme_calibration_palette_lab_candidates_are_not_production_theme_values():
    """Candidate ids must never collide with a real `User.theme` choice
    or otherwise look like a production theme selector -- this is the
    architectural boundary the whole Palette Lab fixture depends on."""
    from core.palette_lab import PALETTE_LAB_CANDIDATE_IDS
    from core.themes import THEME_VALUES

    assert set(PALETTE_LAB_CANDIDATE_IDS).isdisjoint(set(THEME_VALUES))


@pytest.mark.django_db
def test_theme_calibration_forbidden_for_non_admin_still_hides_palette_lab():
    user = create_account("calibration-lab-non-admin")
    client = authenticated_client(user)

    response = client.get(reverse("accounts:theme_calibration"))

    assert response.status_code == 403
    assert b"Palette Lab" not in response.content
