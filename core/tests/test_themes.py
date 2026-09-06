"""`DEFAULT_THEME` is an explicit literal rather than
`THEME_VALUES[0]`, so it cannot silently change if `THEME_CHOICES` is
ever reordered. Full non-order
-dependence isn't mechanically provable without actually reordering the
tuple in-test (which would just restate the implementation), so the
meaningful assertions here are the ones that would actually catch a
regression: the value is pinned exactly, and it is a real, still
-registered production theme -- not merely "whatever happens to be
first."
"""

from core.themes import DEFAULT_THEME, THEME_CHOICES, THEME_VALUES


def test_default_theme_is_explicit_warm_light():
    assert DEFAULT_THEME == "warm-light"


def test_default_theme_is_a_registered_production_theme_value():
    assert DEFAULT_THEME in THEME_VALUES


def test_default_theme_has_a_display_label_in_theme_choices():
    # Confirms DEFAULT_THEME is a real, labeled entry in the registry
    # users actually see (Preferences/Appearance), not just a bare
    # string that happens to match one half of a THEME_VALUES tuple.
    labels = dict(THEME_CHOICES)
    assert DEFAULT_THEME in labels
    assert labels[DEFAULT_THEME] == "Warm Light"
