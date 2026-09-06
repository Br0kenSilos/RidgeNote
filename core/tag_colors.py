"""The canonical Tag color palette, extracted here so it can be
imported by both `accounts.models` (the per-user default-Tag-color
preference) and `notes.models` (`Tag.color` itself) without
either app importing the other's `models` module directly. A plain,
Django-model-free constants module -- importing it never triggers Django
model registration for any app, so it carries none of the app-loading-
order risk a direct `accounts.models` -> `notes.models` (or vice versa)
import would (INSTALLED_APPS loads `accounts` before `notes`; a bare
`from notes.models import ...` inside `accounts/models.py` would force
`notes`'s `Tag`/`Folder`/`Note` model classes to register with Django's
app registry before `notes`'s own `AppConfig` is ready, a known circular-
app-loading hazard). `notes.models` continues to define
`TAG_COLOR_CHOICES`/`TAG_COLOR_VALUES`/`DEFAULT_TAG_COLOR` as re-exported
names (importing them here), so every existing `from notes.models import
TAG_COLOR_CHOICES`-style call site elsewhere in the codebase is
unaffected -- this move is intentionally invisible to them."""

TAG_COLOR_CHOICES = (
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
)

TAG_COLOR_VALUES = tuple(value for value, _label in TAG_COLOR_CHOICES)

DEFAULT_TAG_COLOR = TAG_COLOR_VALUES[0]
