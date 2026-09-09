"""Django settings for RidgeNote."""

from __future__ import annotations

import os
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def env_nonnegative_int(name: str, default: int) -> int:
    """Like `env_positive_int`, but 0 is accepted as a meaningful value
    rather than rejected -- used only for
    `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS`, where 0 is the explicit
    "never expire" sentinel (see the validation just below this
    function). Negative values are still rejected."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater.")
    return value


def env_optional_positive_int(name: str) -> int | None:
    """Like `env_positive_int`, but a blank/absent value means "not
    configured" rather than falling back to a numeric default -- used for
    `RIDGENOTE_SMTP_PORT`, which has no sensible default of
    its own when SMTP is unconfigured."""
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


SMTP_TLS_MODES = ("none", "starttls", "ssl")


def env_smtp_tls_mode(name: str) -> str:
    raw_value = os.environ.get(name, "").strip().lower()
    if not raw_value:
        return "none"
    if raw_value not in SMTP_TLS_MODES:
        raise ValueError(f"{name} must be one of: {', '.join(SMTP_TLS_MODES)}.")
    return raw_value


def env_external_url(name: str) -> str:
    """A full origin (scheme + host + optional port) with no
    path, query string, or fragment -- trailing slash accepted and
    normalized away. Blank is valid (the automatic-email feature is then
    simply unavailable); a present-but-malformed value fails fast here,
    at settings-import time, rather than silently producing a broken or
    misleading emailed link later."""
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return ""
    normalized = raw_value.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{name} must be a full origin (scheme://host[:port]) with no path, "
            "query string, or fragment."
        )
    return normalized


_INSECURE_DEFAULT_SECRET_KEY = "development-only-insecure-secret-key-change-me"

SECRET_KEY = os.environ.get("RIDGENOTE_SECRET_KEY", _INSECURE_DEFAULT_SECRET_KEY)
DEBUG = env_bool("RIDGENOTE_DEBUG", False)

# A blank or still-default
# SECRET_KEY is fine in development (DEBUG=True) -- that is exactly the
# convenience the default exists for -- but must never reach a real
# deployment silently. Checked here, at import time, rather than via
# Django's separate system-checks framework, to match every other
# operator-configuration validation already in this file (SMTP,
# RIDGENOTE_EXTERNAL_URL, RIDGENOTE_SESSION_WARNING_SECONDS): raising here
# fails loudly before `manage.py startup_migrate` (invoked by
# `docker-entrypoint.sh` ahead of every normal container command,
# including gunicorn) can get anywhere near serving a request. Never
# generates, substitutes, or persists a key -- the operator must set
# RIDGENOTE_SECRET_KEY themselves.
if not DEBUG and SECRET_KEY in ("", _INSECURE_DEFAULT_SECRET_KEY):
    raise ValueError(
        "RIDGENOTE_SECRET_KEY must be set to a real, non-default value when "
        "RIDGENOTE_DEBUG is false. Set RIDGENOTE_SECRET_KEY in your "
        "environment or .env file before running RidgeNote outside of "
        "local development."
    )

ALLOWED_HOSTS = env_list("RIDGENOTE_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")
CSRF_TRUSTED_ORIGINS = env_list("RIDGENOTE_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "core",
    "notes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "notes.middleware.LibraryBackupUploadGuardMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.SessionSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ridgenote.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.session_policy",
                "accounts.context_processors.theme_choices",
            ],
        },
    },
]

WSGI_APPLICATION = "ridgenote.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("RIDGENOTE_DATABASE_NAME", "ridgenote"),
        "USER": os.environ.get("RIDGENOTE_DATABASE_USER", "ridgenote"),
        "PASSWORD": os.environ.get("RIDGENOTE_DATABASE_PASSWORD", "ridgenote"),
        "HOST": os.environ.get("RIDGENOTE_DATABASE_HOST", "localhost"),
        "PORT": os.environ.get("RIDGENOTE_DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Kentucky/Louisville"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "core" / "static" / "core" / "dist"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
if env_bool("RIDGENOTE_TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("RIDGENOTE_USE_X_FORWARDED_HOST", False)
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.auth_backends.CanonicalUsernameModelBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD = env_positive_int("RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD", 5)
RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS = env_positive_int(
    "RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS",
    15 * 60,
)
RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS = env_positive_int(
    "RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS",
    15 * 60,
)
RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS = env_nonnegative_int(
    "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS",
    60 * 60,
)
RIDGENOTE_SESSION_WARNING_SECONDS = env_positive_int("RIDGENOTE_SESSION_WARNING_SECONDS", 5 * 60)
# 0 is the explicit "never expire" sentinel -- RIDGENOTE_SESSION_WARNING_SECONDS
# is meaningless (and never enforced) in that case, so this comparison is
# skipped entirely rather than rejecting every positive warning value.
if (
    RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS > 0
    and RIDGENOTE_SESSION_WARNING_SECONDS >= RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS
):
    raise ValueError("RIDGENOTE_SESSION_WARNING_SECONDS must be less than the idle timeout.")

RIDGENOTE_INVITATION_EXPIRY_MINUTES = env_positive_int("RIDGENOTE_INVITATION_EXPIRY_MINUTES", 120)

# Optional generic SMTP invitation delivery.
# Every value here is optional; a deployment that sets none of them
# continues to work with manual-copy invitation delivery only. Only a
# *present-but-malformed* value fails
# at startup -- a blank one never does.
RIDGENOTE_SMTP_HOST = os.environ.get("RIDGENOTE_SMTP_HOST", "").strip()
RIDGENOTE_SMTP_PORT = env_optional_positive_int("RIDGENOTE_SMTP_PORT")
RIDGENOTE_SMTP_TLS_MODE = env_smtp_tls_mode("RIDGENOTE_SMTP_TLS_MODE")
RIDGENOTE_SMTP_USERNAME = os.environ.get("RIDGENOTE_SMTP_USERNAME", "")
RIDGENOTE_SMTP_PASSWORD = os.environ.get("RIDGENOTE_SMTP_PASSWORD", "")
RIDGENOTE_SMTP_FROM_EMAIL = os.environ.get("RIDGENOTE_SMTP_FROM_EMAIL", "").strip()
RIDGENOTE_SMTP_FROM_NAME = os.environ.get("RIDGENOTE_SMTP_FROM_NAME", "").strip()

# Authoritative only for automatically emailed invitation links -- the
# manual one-time Copy Link continues to use
# `request.build_absolute_uri()` and is untouched by this setting;
# these two URL sources are deliberately never unified (see
# `docs/RUNBOOK_DEPLOYMENT.md`'s "Optional email delivery" section).
RIDGENOTE_EXTERNAL_URL = env_external_url("RIDGENOTE_EXTERNAL_URL")

# Half-populated credentials are never a silent "unauthenticated relay"
# -- that would downgrade security on what is far more likely a typo/
# omission than a deliberate choice. Both blank, or both set, are the
# only two valid shapes.
if bool(RIDGENOTE_SMTP_USERNAME) != bool(RIDGENOTE_SMTP_PASSWORD):
    raise ValueError(
        "RIDGENOTE_SMTP_USERNAME and RIDGENOTE_SMTP_PASSWORD must either both be "
        "set, or both left blank for an unauthenticated relay."
    )

# The single authoritative "is SMTP configured" flag, consulted
# everywhere else in this codebase (form-field visibility, view-level
# send eligibility, `accounts/mail.py`'s own guard) instead of each call
# site re-deriving it independently. Username/password are deliberately
# excluded from this condition -- both blank is a fully valid,
# "configured" unauthenticated-relay shape.
RIDGENOTE_SMTP_CONFIGURED = bool(
    RIDGENOTE_SMTP_HOST
    and RIDGENOTE_SMTP_PORT
    and RIDGENOTE_SMTP_FROM_EMAIL
    and RIDGENOTE_EXTERNAL_URL
)

# Django's own SMTP backend already does exactly the right thing for an
# unauthenticated relay: `EmailBackend.open()` only calls
# `connection.login(...)` `if self.username and self.password` --
# leaving `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` blank when both
# `RIDGENOTE_SMTP_USERNAME`/`RIDGENOTE_SMTP_PASSWORD` are blank requires
# no extra logic here.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = RIDGENOTE_SMTP_HOST
EMAIL_PORT = RIDGENOTE_SMTP_PORT or 25
EMAIL_USE_TLS = RIDGENOTE_SMTP_TLS_MODE == "starttls"
EMAIL_USE_SSL = RIDGENOTE_SMTP_TLS_MODE == "ssl"
EMAIL_HOST_USER = RIDGENOTE_SMTP_USERNAME
EMAIL_HOST_PASSWORD = RIDGENOTE_SMTP_PASSWORD
DEFAULT_FROM_EMAIL = (
    formataddr((RIDGENOTE_SMTP_FROM_NAME, RIDGENOTE_SMTP_FROM_EMAIL))
    if RIDGENOTE_SMTP_FROM_NAME and RIDGENOTE_SMTP_FROM_EMAIL
    else RIDGENOTE_SMTP_FROM_EMAIL
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("RIDGENOTE_LOG_LEVEL", "INFO"),
    },
}
