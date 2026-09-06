from core.themes import DEFAULT_THEME, THEME_CHOICES
from django.conf import settings


def session_policy(request):
    return {
        "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": settings.RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS,
        "RIDGENOTE_SESSION_WARNING_SECONDS": settings.RIDGENOTE_SESSION_WARNING_SECONDS,
    }


def theme_choices(request):
    """Exposes the named-theme registry, plus
    the single resolved, authoritative theme value for this request, to
    every template -- `base.html`'s top-bar quick-selector is rendered on
    every authenticated page, not just Preferences, so a context processor
    (matching `session_policy` above) is the smallest way to reach it
    without threading the same constant through every view.

    `RIDGENOTE_CURRENT_THEME` is
    computed exactly once here and consumed identically by both
    `base.html`'s `html_attrs` and `body_attrs` blocks, so there is only
    ever one server-side theme resolution -- never two independently
    -computed values that could drift apart."""
    user = getattr(request, "user", None)
    current_theme = user.theme if user is not None and user.is_authenticated else DEFAULT_THEME
    return {
        "RIDGENOTE_THEME_CHOICES": THEME_CHOICES,
        "RIDGENOTE_CURRENT_THEME": current_theme,
    }
