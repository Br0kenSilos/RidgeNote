"""The canonical named-theme registry, mirroring `core/tag_colors.py`'s
established shape -- a plain, Django-model-free constants module
importable by `accounts.models` without any circular app-loading risk.
`core/static/core/src/theme.ts` maintains its own small mirrored copy
of this same value set (the same pattern `tag-management.ts`'s
`tagColorLabel()` already uses for `TAG_COLOR_CHOICES`); each side is
covered by its own tests, not a shared build-time source, per project
convention.

The six named themes below `warm-light`/`dark` are the production
names selected via the internal Palette Lab (`core/palette_lab.py`) --
this module remains intentionally separate from that one: Palette Lab
ids (`cool-a`, `dim-e`, etc.) are never valid values here, and this
registry never imports from `core.palette_lab` at runtime. Source
mapping, preserved here only as a code comment since Palette
Lab ids have no meaning to this module: `glacier` from `cool-a`,
`granite` from `cool-c`, `alpine-mist` from `cool-d`, `blue-dusk` from
`dim-e`, `midnight-ridge` from `dim-g`, `nightfall` from `dim-i`.
"""

THEME_CHOICES = (
    ("warm-light", "Warm Light"),
    ("dark", "Dark"),
    ("glacier", "Glacier"),
    ("granite", "Granite"),
    ("alpine-mist", "Alpine Mist"),
    ("blue-dusk", "Blue Dusk"),
    ("midnight-ridge", "Midnight Ridge"),
    ("nightfall", "Nightfall"),
)

THEME_VALUES = tuple(value for value, _label in THEME_CHOICES)

# An explicit literal, not `THEME_VALUES[0]` -- the default must not
# silently depend on `THEME_CHOICES`'s tuple order, or reordering the
# tuple for any reason (e.g. alphabetizing) would silently change the
# default theme assigned to every new account. Must always name a
# value that is actually a member of THEME_CHOICES (see
# core/tests/test_themes.py).
DEFAULT_THEME = "warm-light"
