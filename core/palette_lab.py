"""Theme Calibration / Palette Lab.

Candidate palette registry consumed only by the internal, admin-only
Theme Calibration page (`/admin/theme-calibration/`). These are
design/calibration candidates, never production themes: none of these
ids is a valid `accounts.User.theme` value, none appears in
`core.themes` or the frontend production theme registry
(`core/static/core/src/theme.ts`), and none is selectable from
Preferences or the top-bar Appearance selector. Candidate CSS token
overrides live in `core/static/core/css/app.css`, scoped entirely under
`.palette-lab-scope[data-palette-candidate="..."]` -- never a bare
`[data-theme="..."]` block -- so they can never apply outside the
Palette Lab's own preview/swatch elements. This module is the single
source of truth for candidate ids/labels/categories/descriptions
rendered into the Lab's selector and swatch strip, the same reuse
pattern `TAG_COLOR_CHOICES` already establishes for the tag palette
fixture below it on the same page.

Every candidate carries three small metadata fields (`None`/`False`
where not applicable) so template logic never needs sparse-key checks:

- `approved_name`: the production-facing name (e.g.
  "Glacier" for `cool-a`), or `None` for a still-exploratory reference
  candidate. This is a *display* name only -- the internal `id` is the
  identity used everywhere else (CSS scoping, the registry, tests) and
  is deliberately NOT renamed to match.
- `selected_for_production`: `True` for exactly the six candidates
  promoted to real production themes (see
  `core/themes.py`).
- `light_shell`: `True` only for the three selected Dim candidates
  (Blue Dusk/Midnight Ridge/Nightfall), whose shell-foreground
  direction is the light/near-white `--color-text-strong` family (see
  `PALETTE_LAB_SHELL_COMPARE_IDS` below). The three
  selected Cool candidates need no such flag -- their shell foreground
  question was never raised; their existing default (dark-on-light)
  treatment is unaffected.
"""

PALETTE_LAB_CATEGORIES = (
    ("cool", "Cool Light"),
    ("dim", "Soft/Dim Dark"),
)

PALETTE_LAB_CANDIDATES = (
    {
        "id": "cool-a",
        "label": "Cool A",
        "category": "cool",
        "category_label": "Cool Light",
        "description": "Blue-steel -- crisp cool neutral, steel-blue accent",
        "approved_name": "Glacier",
        "selected_for_production": True,
        "light_shell": False,
    },
    {
        "id": "cool-c",
        "label": "Cool C",
        "category": "cool",
        "category_label": "Cool Light",
        "description": "Slate-blue -- low-saturation neutral gray-blue",
        "approved_name": "Granite",
        "selected_for_production": True,
        "light_shell": False,
    },
    {
        "id": "cool-d",
        "label": "Cool D",
        "category": "cool",
        "category_label": "Cool Light",
        "description": "Soft gray-blue -- lighter, airier periwinkle accent",
        "approved_name": "Alpine Mist",
        "selected_for_production": True,
        "light_shell": False,
    },
    {
        "id": "dim-a",
        "label": "Dim A",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Charcoal with muted blue accent",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-c",
        "label": "Dim C",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Warm ember-gray -- charcoal with amber/brown accent",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-d",
        "label": "Dim D",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Cool violet-gray -- charcoal with desaturated violet accent",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-e",
        "label": "Dim E",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Navy Charcoal -- very dark navy base, restrained medium-blue accent",
        "approved_name": "Blue Dusk",
        "selected_for_production": True,
        "light_shell": True,
    },
    {
        "id": "dim-f",
        "label": "Dim F",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Graphite Blue -- neutral graphite surfaces, steel/slate-blue accent",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-g",
        "label": "Dim G",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Blue-Black -- deep near-black base, cool blue accents, brighter text",
        "approved_name": "Midnight Ridge",
        "selected_for_production": True,
        "light_shell": True,
    },
    {
        "id": "dim-h",
        "label": "Dim H",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Slate Night -- muted desaturated blue-gray, soft and understated",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-i",
        "label": "Dim I",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Midnight Navy -- rich navy base, restrained medium-blue accent",
        "approved_name": "Nightfall",
        "selected_for_production": True,
        "light_shell": True,
    },
    {
        "id": "dim-j",
        "label": "Dim J",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Steel Graphite -- neutral graphite base, steel-blue structural accents",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-k",
        "label": "Dim K",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Soft Blue-Gray -- lighter, low-fatigue, restrained blue-gray",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
    {
        "id": "dim-l",
        "label": "Dim L",
        "category": "dim",
        "category_label": "Soft/Dim Dark",
        "description": "Indigo Slate -- dark slate with a subtle indigo cast",
        "approved_name": None,
        "selected_for_production": False,
        "light_shell": False,
    },
)

PALETTE_LAB_CANDIDATE_IDS = tuple(candidate["id"] for candidate in PALETTE_LAB_CANDIDATES)

DEFAULT_PALETTE_LAB_CANDIDATE = PALETTE_LAB_CANDIDATE_IDS[0]

# The six production-theme candidates, in lineup order
# (Cool then Dim). Derived, not hand-duplicated, so it can never drift
# from `PALETTE_LAB_CANDIDATES` itself.
PALETTE_LAB_SELECTED_IDS = tuple(
    candidate["id"] for candidate in PALETTE_LAB_CANDIDATES if candidate["selected_for_production"]
)

# The three Dim candidates retained in the Palette Lab for shell-foreground
# comparison, alongside Cool A/C/D -- not yet promoted to production.
# Sourced by id from the registry above rather than duplicating labels
# by hand, so the comparison section can never drift out of sync with
# a candidate's real label.
PALETTE_LAB_SHELL_COMPARE_IDS = ("dim-e", "dim-g", "dim-i")
