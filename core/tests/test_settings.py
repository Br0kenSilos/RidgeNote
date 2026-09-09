"""Deployment/forwarded-protocol settings behavior.

`ridgenote/settings.py` computes several module-level values once, at
import time, from environment variables. Because pytest-django loads the
settings module exactly once per test process, exercising different
environment-variable combinations for import-time behavior (in
particular, whether `SECURE_PROXY_SSL_HEADER` is defined at all) requires
a fresh subprocess per scenario rather than an in-process settings
reload -- `django.test.override_settings` only patches an
already-imported settings object and cannot re-run the conditional
assignment itself. Pure helper functions (`env_list`) are still tested
directly and cheaply in-process, since they have no import-time side
effects of their own.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ridgenote.settings import env_list

REPO_ROOT = Path(__file__).resolve().parents[2]

_ABSENT = "__ABSENT__"

# Environment variables whose ambient value (inherited from the test
# runner's own environment) could otherwise leak into a subprocess and
# make a scenario's inputs non-deterministic.
_RELEVANT_VARS = [
    "RIDGENOTE_TRUST_X_FORWARDED_PROTO",
    "RIDGENOTE_USE_X_FORWARDED_HOST",
    "RIDGENOTE_ALLOWED_HOSTS",
    "RIDGENOTE_CSRF_TRUSTED_ORIGINS",
    "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS",
    "RIDGENOTE_SESSION_WARNING_SECONDS",
    "RIDGENOTE_SMTP_HOST",
    "RIDGENOTE_SMTP_PORT",
    "RIDGENOTE_SMTP_TLS_MODE",
    "RIDGENOTE_SMTP_USERNAME",
    "RIDGENOTE_SMTP_PASSWORD",
    "RIDGENOTE_SMTP_FROM_EMAIL",
    "RIDGENOTE_SMTP_FROM_NAME",
    "RIDGENOTE_EXTERNAL_URL",
    "RIDGENOTE_SECRET_KEY",
    "RIDGENOTE_DEBUG",
]

_INSECURE_DEFAULT_SECRET_KEY = "development-only-insecure-secret-key-change-me"


def _safe_env(env_overrides: dict[str, str]) -> dict[str, str]:
    """Strip `_RELEVANT_VARS` from the ambient environment, then apply a
    safe RIDGENOTE_DEBUG=true baseline before layering in the scenario's
    own overrides. The baseline exists so the many pre-existing
    scenarios in this file that don't care about DEBUG/SECRET_KEY at all
    don't spuriously trip the production-SECRET_KEY check added below
    (`RIDGENOTE_DEBUG`/`RIDGENOTE_SECRET_KEY` are deliberately
    included in `_RELEVANT_VARS`, so without this default they would
    otherwise import with DEBUG=False and the insecure default key --
    exactly the combination that check exists to reject). Any test that
    actually wants to exercise DEBUG=False still does so explicitly via
    `env_overrides`, which is applied after this default and wins."""
    env = os.environ.copy()
    for key in _RELEVANT_VARS:
        env.pop(key, None)
    env.setdefault("RIDGENOTE_DEBUG", "true")
    env.update(env_overrides)
    return env


def _settings_attr(env_overrides: dict[str, str], attr: str):
    """Import `ridgenote.settings` in a fresh subprocess with the given
    environment overrides and return `getattr(settings, attr, _ABSENT)`,
    JSON-round-tripped (tuples become lists)."""
    env = _safe_env(env_overrides)
    code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); "
        "from ridgenote import settings; "
        "import json; "
        f"print(json.dumps(getattr(settings, {attr!r}, {_ABSENT!r})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout.strip())


def _settings_import_fails(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = _safe_env(env_overrides)
    code = f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); from ridgenote import settings"
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


# -- Production SECRET_KEY --------------------------------------------------


def test_debug_true_with_default_key_is_allowed():
    completed = _settings_import_fails({"RIDGENOTE_DEBUG": "true"})
    assert completed.returncode == 0
    assert (
        _settings_attr({"RIDGENOTE_DEBUG": "true"}, "SECRET_KEY")
        == _INSECURE_DEFAULT_SECRET_KEY
    )


def test_debug_false_with_default_key_fails_loudly():
    completed = _settings_import_fails({"RIDGENOTE_DEBUG": "false"})
    assert completed.returncode != 0
    assert "RIDGENOTE_SECRET_KEY" in completed.stderr
    assert "RIDGENOTE_DEBUG" in completed.stderr


def test_debug_false_with_blank_key_fails_loudly():
    completed = _settings_import_fails(
        {"RIDGENOTE_DEBUG": "false", "RIDGENOTE_SECRET_KEY": ""}
    )
    assert completed.returncode != 0
    assert "RIDGENOTE_SECRET_KEY" in completed.stderr


def test_debug_false_with_real_key_is_allowed():
    completed = _settings_import_fails(
        {"RIDGENOTE_DEBUG": "false", "RIDGENOTE_SECRET_KEY": "a-real-configured-secret"}
    )
    assert completed.returncode == 0
    assert (
        _settings_attr(
            {"RIDGENOTE_DEBUG": "false", "RIDGENOTE_SECRET_KEY": "a-real-configured-secret"},
            "SECRET_KEY",
        )
        == "a-real-configured-secret"
    )


def test_debug_true_with_blank_key_is_still_allowed():
    # Development convenience must not regress: DEBUG=True tolerates a
    # blank key exactly as it already tolerates the shipped default.
    completed = _settings_import_fails(
        {"RIDGENOTE_DEBUG": "true", "RIDGENOTE_SECRET_KEY": ""}
    )
    assert completed.returncode == 0


def test_insecure_secret_key_error_never_echoes_a_real_configured_value():
    # Mirrors the existing SMTP auth-pair tests' convention of asserting a
    # rejected real value never leaks into the error message -- not
    # applicable to the blank/default cases (there is no secret value to
    # leak), but worth guarding if a future edit ever changed the message
    # to echo SECRET_KEY itself.
    completed = _settings_import_fails(
        {"RIDGENOTE_DEBUG": "false", "RIDGENOTE_SECRET_KEY": _INSECURE_DEFAULT_SECRET_KEY}
    )
    assert completed.returncode != 0
    assert "must be set to a real, non-default value" in completed.stderr


# -- RIDGENOTE_TRUST_X_FORWARDED_PROTO -----------------------------------------


def test_trust_forwarded_proto_absent_leaves_header_unset():
    assert _settings_attr({}, "SECURE_PROXY_SSL_HEADER") == _ABSENT


@pytest.mark.parametrize("raw", ["false", "False", "FALSE", "no", "0", "off", ""])
def test_trust_forwarded_proto_explicit_false_leaves_header_unset(raw):
    result = _settings_attr({"RIDGENOTE_TRUST_X_FORWARDED_PROTO": raw}, "SECURE_PROXY_SSL_HEADER")
    assert result == _ABSENT


@pytest.mark.parametrize("raw", ["true", "True", "TRUE", "1", "yes", "on"])
def test_trust_forwarded_proto_true_variants_enable_exact_header(raw):
    result = _settings_attr({"RIDGENOTE_TRUST_X_FORWARDED_PROTO": raw}, "SECURE_PROXY_SSL_HEADER")
    assert result == ["HTTP_X_FORWARDED_PROTO", "https"]


def test_trust_forwarded_proto_unrecognized_value_is_treated_as_false():
    # env_bool() -- the same helper already used for RIDGENOTE_DEBUG and
    # RIDGENOTE_USE_X_FORWARDED_HOST -- is lenient: any value outside its
    # accepted true-variant set silently resolves to False rather than
    # raising. This differs from RIDGENOTE_PURGE_ENABLED's own dedicated
    # strict parser, which does raise on an unrecognized value. Preserving
    # env_bool()'s existing, unmodified semantics (as the active contract
    # requires) means a garbled value here is silently disabled, not
    # rejected -- documented here as the actual, verified behavior.
    result = _settings_attr(
        {"RIDGENOTE_TRUST_X_FORWARDED_PROTO": "garbage"}, "SECURE_PROXY_SSL_HEADER"
    )
    assert result == _ABSENT


def test_direct_ip_deployment_does_not_trust_forwarded_proto_by_default():
    # A direct IP:PORT deployment that sets no proxy-related variables at
    # all must never trust a client-supplied X-Forwarded-Proto header.
    assert _settings_attr({}, "SECURE_PROXY_SSL_HEADER") == _ABSENT


# -- independence from RIDGENOTE_USE_X_FORWARDED_HOST ---------------------------


def test_enabling_forwarded_proto_does_not_force_forwarded_host():
    result = _settings_attr({"RIDGENOTE_TRUST_X_FORWARDED_PROTO": "true"}, "USE_X_FORWARDED_HOST")
    assert result is False


def test_enabling_forwarded_host_does_not_force_forwarded_proto():
    result = _settings_attr({"RIDGENOTE_USE_X_FORWARDED_HOST": "true"}, "SECURE_PROXY_SSL_HEADER")
    assert result == _ABSENT


def test_both_forwarded_settings_can_be_enabled_independently():
    header = _settings_attr(
        {
            "RIDGENOTE_TRUST_X_FORWARDED_PROTO": "true",
            "RIDGENOTE_USE_X_FORWARDED_HOST": "true",
        },
        "SECURE_PROXY_SSL_HEADER",
    )
    forwarded_host = _settings_attr(
        {
            "RIDGENOTE_TRUST_X_FORWARDED_PROTO": "true",
            "RIDGENOTE_USE_X_FORWARDED_HOST": "true",
        },
        "USE_X_FORWARDED_HOST",
    )
    assert header == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert forwarded_host is True


# -- list-parsing regression: env_list() and the settings that use it ----------


def test_env_list_trims_values_and_skips_empty_entries(monkeypatch):
    monkeypatch.setenv("RIDGENOTE_TEST_LIST_VAR", " alpha , beta ,,  gamma ")
    assert env_list("RIDGENOTE_TEST_LIST_VAR") == ["alpha", "beta", "gamma"]


def test_env_list_missing_variable_uses_provided_default():
    assert env_list("RIDGENOTE_TEST_LIST_VAR_UNSET", "x,y") == ["x", "y"]


def test_env_list_missing_variable_with_no_default_is_empty():
    assert env_list("RIDGENOTE_TEST_LIST_VAR_UNSET_NO_DEFAULT") == []


def test_allowed_hosts_trims_comma_separated_values_via_settings():
    result = _settings_attr(
        {"RIDGENOTE_ALLOWED_HOSTS": " host-one , host-two ,,host-three "},
        "ALLOWED_HOSTS",
    )
    assert result == ["host-one", "host-two", "host-three"]


def test_csrf_trusted_origins_trims_values_via_settings():
    result = _settings_attr(
        {"RIDGENOTE_CSRF_TRUSTED_ORIGINS": " https://a.example.test , https://b.example.test "},
        "CSRF_TRUSTED_ORIGINS",
    )
    assert result == ["https://a.example.test", "https://b.example.test"]


def test_csrf_trusted_origins_empty_remains_empty_list():
    result = _settings_attr({"RIDGENOTE_CSRF_TRUSTED_ORIGINS": ""}, "CSRF_TRUSTED_ORIGINS")
    assert result == []


# -- regression: session idle/warning cross-validation is unaffected -----------


def test_session_warning_below_idle_timeout_still_valid():
    result = _settings_attr(
        {
            "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "120",
            "RIDGENOTE_SESSION_WARNING_SECONDS": "60",
        },
        "RIDGENOTE_SESSION_WARNING_SECONDS",
    )
    assert result == 60


def test_session_warning_not_below_idle_timeout_still_raises_at_import():
    completed = _settings_import_fails(
        {
            "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "60",
            "RIDGENOTE_SESSION_WARNING_SECONDS": "60",
        }
    )
    assert completed.returncode != 0
    assert "RIDGENOTE_SESSION_WARNING_SECONDS must be less than the idle timeout." in (
        completed.stderr
    )


# -- Session idle timeout=0: explicit "never expire" sentinel ----------------


def test_session_idle_timeout_zero_is_accepted():
    result = _settings_attr(
        {"RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "0"},
        "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS",
    )
    assert result == 0


def test_session_idle_timeout_zero_skips_the_warning_comparison():
    # Any positive warning value -- including the default 300, which
    # would otherwise be >= a tiny idle timeout -- must not raise when
    # the idle timeout is the disabled sentinel.
    result = _settings_attr(
        {
            "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "0",
            "RIDGENOTE_SESSION_WARNING_SECONDS": "999999",
        },
        "RIDGENOTE_SESSION_WARNING_SECONDS",
    )
    assert result == 999999


def test_session_idle_timeout_negative_still_rejected():
    completed = _settings_import_fails({"RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "-1"})
    assert completed.returncode != 0
    assert "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS must be zero or greater." in completed.stderr


def test_session_idle_timeout_positive_behavior_unchanged():
    # Same-as-warning is still rejected for any positive idle timeout --
    # only the 0 sentinel skips the comparison.
    completed = _settings_import_fails(
        {
            "RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS": "60",
            "RIDGENOTE_SESSION_WARNING_SECONDS": "60",
        }
    )
    assert completed.returncode != 0
    assert "RIDGENOTE_SESSION_WARNING_SECONDS must be less than the idle timeout." in (
        completed.stderr
    )


# -- Optional SMTP invitation delivery ---------------------------------------


def _smtp_env(**overrides: str) -> dict[str, str]:
    """A fully "configured" SMTP environment (all five minimum-requirement
    values present and valid), so an individual test only needs to
    override the one value it cares about."""
    env = {
        "RIDGENOTE_SMTP_HOST": "smtp.example.test",
        "RIDGENOTE_SMTP_PORT": "587",
        "RIDGENOTE_SMTP_TLS_MODE": "starttls",
        "RIDGENOTE_SMTP_FROM_EMAIL": "ridgenote@example.test",
        "RIDGENOTE_EXTERNAL_URL": "https://ridgenote.example.test",
    }
    env.update(overrides)
    return env


def test_smtp_absent_entirely_is_valid_and_unconfigured():
    assert _settings_attr({}, "RIDGENOTE_SMTP_CONFIGURED") is False


def test_smtp_absent_entirely_does_not_raise():
    completed = _settings_import_fails({})
    assert completed.returncode == 0


def test_smtp_tls_mode_none_maps_both_flags_false():
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="none")
    assert _settings_attr(env, "EMAIL_USE_TLS") is False
    assert _settings_attr(env, "EMAIL_USE_SSL") is False


def test_smtp_tls_mode_starttls_maps_tls_true_ssl_false():
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="starttls")
    assert _settings_attr(env, "EMAIL_USE_TLS") is True
    assert _settings_attr(env, "EMAIL_USE_SSL") is False


def test_smtp_tls_mode_ssl_maps_tls_false_ssl_true():
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="ssl")
    assert _settings_attr(env, "EMAIL_USE_TLS") is False
    assert _settings_attr(env, "EMAIL_USE_SSL") is True


def test_smtp_tls_mode_is_case_insensitive():
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="STARTTLS")
    assert _settings_attr(env, "EMAIL_USE_TLS") is True


def test_smtp_tls_mode_blank_defaults_to_none():
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="")
    assert _settings_attr(env, "RIDGENOTE_SMTP_TLS_MODE") == "none"
    assert _settings_attr(env, "EMAIL_USE_TLS") is False
    assert _settings_attr(env, "EMAIL_USE_SSL") is False


def test_smtp_tls_mode_invalid_value_fails_at_import():
    completed = _settings_import_fails(_smtp_env(RIDGENOTE_SMTP_TLS_MODE="tls1.2"))
    assert completed.returncode != 0
    assert "RIDGENOTE_SMTP_TLS_MODE must be one of" in completed.stderr


def test_smtp_port_blank_is_valid_when_smtp_otherwise_unconfigured():
    assert _settings_attr({}, "RIDGENOTE_SMTP_PORT") is None


def test_smtp_port_nonpositive_value_rejected():
    completed = _settings_import_fails(_smtp_env(RIDGENOTE_SMTP_PORT="0"))
    assert completed.returncode != 0
    assert "RIDGENOTE_SMTP_PORT must be greater than zero." in completed.stderr


def test_smtp_port_non_integer_value_rejected():
    completed = _settings_import_fails(_smtp_env(RIDGENOTE_SMTP_PORT="not-a-port"))
    assert completed.returncode != 0
    assert "RIDGENOTE_SMTP_PORT must be an integer." in completed.stderr


def test_smtp_port_is_not_inferred_from_tls_mode():
    # RIDGENOTE_SMTP_PORT is always read literally -- no "465 because ssl"
    # or "587 because starttls" convenience default exists.
    env = _smtp_env(RIDGENOTE_SMTP_TLS_MODE="ssl", RIDGENOTE_SMTP_PORT="2525")
    assert _settings_attr(env, "EMAIL_PORT") == 2525


def test_smtp_auth_pair_both_blank_is_valid_unauthenticated_relay():
    env = _smtp_env(RIDGENOTE_SMTP_USERNAME="", RIDGENOTE_SMTP_PASSWORD="")
    assert _settings_attr(env, "RIDGENOTE_SMTP_CONFIGURED") is True
    assert _settings_attr(env, "EMAIL_HOST_USER") == ""
    assert _settings_attr(env, "EMAIL_HOST_PASSWORD") == ""


def test_smtp_auth_pair_both_present_is_valid():
    env = _smtp_env(RIDGENOTE_SMTP_USERNAME="relay-user", RIDGENOTE_SMTP_PASSWORD="relay-pass")
    assert _settings_attr(env, "RIDGENOTE_SMTP_CONFIGURED") is True
    assert _settings_attr(env, "EMAIL_HOST_USER") == "relay-user"


def test_smtp_auth_pair_username_only_rejected():
    completed = _settings_import_fails(_smtp_env(RIDGENOTE_SMTP_USERNAME="relay-user"))
    assert completed.returncode != 0
    assert "must either both be set, or both left blank" in completed.stderr
    # The error message must never echo the actual value.
    assert "relay-user" not in completed.stderr


def test_smtp_auth_pair_password_only_rejected():
    completed = _settings_import_fails(_smtp_env(RIDGENOTE_SMTP_PASSWORD="relay-pass"))
    assert completed.returncode != 0
    assert "must either both be set, or both left blank" in completed.stderr
    assert "relay-pass" not in completed.stderr


def test_smtp_from_name_and_email_compose_display_address():
    env = _smtp_env(
        RIDGENOTE_SMTP_FROM_EMAIL="ridgenote@example.test",
        RIDGENOTE_SMTP_FROM_NAME="RidgeNote",
    )
    assert _settings_attr(env, "DEFAULT_FROM_EMAIL") == "RidgeNote <ridgenote@example.test>"


def test_smtp_from_email_alone_when_name_blank():
    env = _smtp_env(RIDGENOTE_SMTP_FROM_EMAIL="ridgenote@example.test", RIDGENOTE_SMTP_FROM_NAME="")
    assert _settings_attr(env, "DEFAULT_FROM_EMAIL") == "ridgenote@example.test"


def test_smtp_missing_from_email_is_not_configured():
    env = _smtp_env(RIDGENOTE_SMTP_FROM_EMAIL="")
    assert _settings_attr(env, "RIDGENOTE_SMTP_CONFIGURED") is False


def test_smtp_missing_host_is_not_configured():
    env = _smtp_env(RIDGENOTE_SMTP_HOST="")
    assert _settings_attr(env, "RIDGENOTE_SMTP_CONFIGURED") is False


def test_smtp_fully_configured_is_configured():
    assert _settings_attr(_smtp_env(), "RIDGENOTE_SMTP_CONFIGURED") is True


# -- RIDGENOTE_EXTERNAL_URL ------------------------------------------------


def test_external_url_blank_is_valid_when_smtp_unused():
    completed = _settings_import_fails({"RIDGENOTE_EXTERNAL_URL": ""})
    assert completed.returncode == 0
    assert _settings_attr({"RIDGENOTE_EXTERNAL_URL": ""}, "RIDGENOTE_EXTERNAL_URL") == ""


def test_external_url_blank_means_smtp_not_configured_even_if_rest_is_set():
    env = _smtp_env(RIDGENOTE_EXTERNAL_URL="")
    assert _settings_attr(env, "RIDGENOTE_SMTP_CONFIGURED") is False


def test_external_url_trailing_slash_normalized_away():
    result = _settings_attr(
        {"RIDGENOTE_EXTERNAL_URL": "https://ridgenote.example.test/"},
        "RIDGENOTE_EXTERNAL_URL",
    )
    assert result == "https://ridgenote.example.test"


def test_external_url_accepts_explicit_port():
    result = _settings_attr(
        {"RIDGENOTE_EXTERNAL_URL": "http://192.0.2.10:8000"},
        "RIDGENOTE_EXTERNAL_URL",
    )
    assert result == "http://192.0.2.10:8000"


def test_external_url_does_not_require_https():
    # RidgeNote's own supported direct-IP topology means a trusted
    # -private-network HTTP deployment is legitimate.
    completed = _settings_import_fails({"RIDGENOTE_EXTERNAL_URL": "http://192.0.2.10:8000"})
    assert completed.returncode == 0


def test_external_url_missing_scheme_rejected():
    completed = _settings_import_fails({"RIDGENOTE_EXTERNAL_URL": "ridgenote.example.test"})
    assert completed.returncode != 0
    assert "RIDGENOTE_EXTERNAL_URL must be a full origin" in completed.stderr


def test_external_url_with_path_rejected():
    completed = _settings_import_fails(
        {"RIDGENOTE_EXTERNAL_URL": "https://ridgenote.example.test/invite"}
    )
    assert completed.returncode != 0
    assert "RIDGENOTE_EXTERNAL_URL must be a full origin" in completed.stderr


def test_external_url_with_query_rejected():
    completed = _settings_import_fails(
        {"RIDGENOTE_EXTERNAL_URL": "https://ridgenote.example.test?x=1"}
    )
    assert completed.returncode != 0


def test_external_url_with_fragment_rejected():
    completed = _settings_import_fails(
        {"RIDGENOTE_EXTERNAL_URL": "https://ridgenote.example.test#top"}
    )
    assert completed.returncode != 0


def test_external_url_unparseable_value_rejected():
    completed = _settings_import_fails({"RIDGENOTE_EXTERNAL_URL": "not a url"})
    assert completed.returncode != 0
