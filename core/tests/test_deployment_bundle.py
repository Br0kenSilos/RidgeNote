"""Static contract checks for the source-free deployment bundle in ``deploy/``,
and for canonical/production Compose-file parity checks that don't belong
solely to one or the other.

These are plain text/string assertions, not a Compose-parsing framework --
narrowly scoped to the two defects corrects: the
packaged health check requiring ``127.0.0.1`` in the shipped allowed-hosts
default, and the deployment Compose file's filename/content contract.
"""

from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parent.parent.parent / "deploy"
ROOT_DIR = DEPLOY_DIR.parent

# The Dockerfile deliberately never copies `deploy/` (or the canonical
# root `docker-compose.yml`) into the built application image (only
# `manage.py`, `ridgenote/`, `accounts/`, `notes/`, `core/` are copied)
# -- these are static-contract checks against the source-free bundle
# and the canonical Compose file, meaningful only from a full repository
# checkout. Skip honestly rather than error when that checkout context
# is absent (e.g. when the suite is collected from inside the built
# image).
pytestmark = pytest.mark.skipif(
    not DEPLOY_DIR.is_dir(),
    reason="deploy/ is not present outside a full repository checkout",
)


ROOT_ENV_EXAMPLE = ROOT_DIR / ".env.example"


def _read(name: str) -> str:
    return (DEPLOY_DIR / name).read_text()


def _read_root(name: str) -> str:
    return (ROOT_DIR / name).read_text()


def _service_block(compose: str, name: str, next_name: str) -> str:
    start = compose.index(f"\n  {name}:")
    end = compose.index(f"\n  {next_name}:", start)
    return compose[start:end]


def _service_block_to_end(compose: str, name: str) -> str:
    start = compose.index(f"\n  {name}:")
    return compose[start:]


def test_docker_compose_yml_exists_and_old_filename_does_not():
    assert (DEPLOY_DIR / "docker-compose.yml").exists()
    assert not (DEPLOY_DIR / "compose.yaml").exists()


def test_env_example_exists_and_old_dotfile_name_does_not():
    # The visible sample env filename is `env.example` -- not hidden
    # from inexperienced Linux users behind a leading dot.
    assert (DEPLOY_DIR / "env.example").exists()
    assert not (DEPLOY_DIR / ".env.example").exists()


def test_root_dev_env_example_is_untouched_by_the_deploy_rename():
    # The separate local-development env sample at the repository root
    # is a different file for a different purpose -- confirm the
    # deploy-bundle rename did not touch it.
    assert ROOT_ENV_EXAMPLE.exists()


def test_env_example_allowed_hosts_includes_loopback_for_healthcheck():
    env_example = _read("env.example")
    for line in env_example.splitlines():
        if line.startswith("RIDGENOTE_ALLOWED_HOSTS="):
            assert "127.0.0.1" in line.split("=", 1)[1].split(",")
            break
    else:
        raise AssertionError("RIDGENOTE_ALLOWED_HOSTS not found in env.example")


def test_env_example_does_not_use_wildcard_host():
    env_example = _read("env.example")
    for line in env_example.splitlines():
        if line.startswith("RIDGENOTE_ALLOWED_HOSTS="):
            assert "*" not in line
            break


def test_healthcheck_target_matches_documented_loopback_host():
    compose = _read("docker-compose.yml")
    assert "http://127.0.0.1:8000/health/" in compose


def test_ridgenote_services_use_the_pinned_v1_image_directly_not_build():
    # LOCKED V1 decision: the canonical deployment Compose hardcodes the
    # official image directly for both web and scheduler -- no
    # ${RIDGENOTE_IMAGE} indirection, and no compose-level image
    # override variable in the normal deployment contract. Pinned to
    # v1.0.2 (the current recommended deployment version -- see release
    # history for prior versions' own historical detail).
    compose = _read("docker-compose.yml")
    assert "${RIDGENOTE_IMAGE}" not in compose
    assert compose.count("image: ghcr.io/br0kensilos/ridgenote:v1.0.2") == 2
    assert "build:" not in compose


def test_postgres_image_is_floating_within_major_17_not_digest_pinned():
    # LOCKED V1 decision: postgres:17-bookworm -- pinned to PostgreSQL
    # major 17, receiving ordinary 17.x image/security refreshes, and
    # deliberately NOT pinned to an exact digest in the shipped
    # deployment Compose (contrast with core/tests/test_settings.py's
    # local-development Compose, which remains digest-pinned).
    compose = _read("docker-compose.yml")
    assert "image: postgres:17-bookworm" in compose
    assert "@sha256:" not in compose
    assert "postgres:latest" not in compose
    assert "postgres:18" not in compose


def test_deploy_services_have_explicit_container_names():
    compose = _read("docker-compose.yml")
    assert "container_name: ridgenote-web" in compose
    assert "container_name: ridgenote-scheduler" in compose
    assert "container_name: ridgenote-postgres" in compose


def test_postgres_bind_mount_path_unchanged():
    compose = _read("docker-compose.yml")
    assert "./data/postgres:/var/lib/postgresql/data" in compose


def test_port_8000_published():
    compose = _read("docker-compose.yml")
    assert '"8000:8000"' in compose


def test_no_visible_compose_migrate_service():
    # Migrations are applied automatically inside the image's own
    # entrypoint (see core/tests/test_startup_migrate.py and
    # core/tests/test_docker_entrypoint.py) -- not as a separate,
    # visible Compose service. Confirm no such service was introduced.
    compose = _read("docker-compose.yml")
    assert "\n  migrate:" not in compose
    assert '"migrate"' not in compose


def test_purge_enabled_by_default():
    # LOCKED V1 decision: automatic Trash purge is enabled by default in
    # the shipped deployment (it was disabled by default before).
    compose = _read("docker-compose.yml")
    assert "RIDGENOTE_PURGE_ENABLED: ${RIDGENOTE_PURGE_ENABLED:-true}" in compose
    assert "RIDGENOTE_PURGE_INTERVAL_SECONDS: ${RIDGENOTE_PURGE_INTERVAL_SECONDS:-86400}" in compose


def test_env_example_contains_no_real_secret():
    env_example = _read("env.example")
    for line in env_example.splitlines():
        if line.startswith("RIDGENOTE_SECRET_KEY=") or line.startswith(
            "RIDGENOTE_DATABASE_PASSWORD="
        ):
            value = line.split("=", 1)[1]
            assert value.startswith("replace-with-a-generated")


def test_env_example_has_exactly_one_user_facing_database_password():
    env_example = _read("env.example")
    assert not any(line.startswith("POSTGRES_PASSWORD=") for line in env_example.splitlines())
    assert (
        sum(line.startswith("RIDGENOTE_DATABASE_PASSWORD=") for line in env_example.splitlines())
        == 1
    )


def test_compose_sources_postgres_password_from_single_ridgenote_variable():
    compose = _read("docker-compose.yml")
    assert "POSTGRES_PASSWORD: ${RIDGENOTE_DATABASE_PASSWORD}" in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}" not in compose


def test_env_example_has_no_independent_postgres_identity_variables():
    # PostgreSQL's own database/user are internal Compose/image keys,
    # derived automatically from RIDGENOTE_DATABASE_NAME/USER -- an
    # installer must never need to set or keep them in sync by hand.
    env_example = _read("env.example")
    lines = env_example.splitlines()
    assert not any(line.startswith("POSTGRES_DB=") for line in lines)
    assert not any(line.startswith("POSTGRES_USER=") for line in lines)
    assert sum(line.startswith("RIDGENOTE_DATABASE_NAME=") for line in lines) == 1
    assert sum(line.startswith("RIDGENOTE_DATABASE_USER=") for line in lines) == 1


def test_compose_sources_postgres_identity_from_ridgenote_variables():
    compose = _read("docker-compose.yml")
    assert "POSTGRES_DB: ${RIDGENOTE_DATABASE_NAME:-ridgenote}" in compose
    assert "POSTGRES_USER: ${RIDGENOTE_DATABASE_USER:-ridgenote}" in compose
    assert "POSTGRES_DB: ${POSTGRES_DB" not in compose
    assert "POSTGRES_USER: ${POSTGRES_USER" not in compose


def test_canonical_compose_sources_postgres_identity_from_ridgenote_variables():
    # The dev-root Compose file must derive PostgreSQL's identity from
    # the same authoritative RIDGENOTE_DATABASE_* settings web/scheduler
    # use, exactly like the deploy bundle -- never an independently
    # configurable POSTGRES_DB/USER/PASSWORD.
    compose = _read_root("docker-compose.yml")
    assert "POSTGRES_DB: ${RIDGENOTE_DATABASE_NAME:-ridgenote}" in compose
    assert "POSTGRES_USER: ${RIDGENOTE_DATABASE_USER:-ridgenote}" in compose
    assert "POSTGRES_PASSWORD: ${RIDGENOTE_DATABASE_PASSWORD:-ridgenote}" in compose
    assert "POSTGRES_DB: ${POSTGRES_DB" not in compose
    assert "POSTGRES_USER: ${POSTGRES_USER" not in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD" not in compose


def test_scheduler_no_longer_depends_on_web_service_started():
    # The shared startup-migration entrypoint makes scheduler
    # self-sufficient for schema readiness -- it no longer needs to
    # wait on web's process starting, only on PostgreSQL being healthy.
    compose = _read("docker-compose.yml")
    assert "condition: service_started" not in compose


def test_both_services_still_depend_on_postgres_healthy():
    compose = _read("docker-compose.yml")
    assert compose.count("condition: service_healthy") == 2


def test_readme_references_current_filename_only():
    readme = _read("README.md")
    assert "docker-compose.yml" in readme
    assert "compose.yaml" not in readme


# -- production pass-through for already-shipped
# invitation-expiry, forwarded-host, and optional SMTP configuration -----


def test_web_receives_invitation_expiry_forwarded_host_and_smtp_pass_through():
    compose = _read("docker-compose.yml")
    web_block = _service_block(compose, "web", "scheduler")
    assert (
        "RIDGENOTE_INVITATION_EXPIRY_MINUTES: ${RIDGENOTE_INVITATION_EXPIRY_MINUTES:-120}"
        in web_block
    )
    assert "RIDGENOTE_USE_X_FORWARDED_HOST: ${RIDGENOTE_USE_X_FORWARDED_HOST:-false}" in web_block
    for var in (
        "RIDGENOTE_SMTP_HOST",
        "RIDGENOTE_SMTP_PORT",
        "RIDGENOTE_SMTP_TLS_MODE",
        "RIDGENOTE_SMTP_USERNAME",
        "RIDGENOTE_SMTP_PASSWORD",
        "RIDGENOTE_SMTP_FROM_EMAIL",
        "RIDGENOTE_SMTP_FROM_NAME",
    ):
        assert f"{var}: ${{{var}:-}}" in web_block


def test_external_url_present_exactly_once_in_web_and_not_duplicated():
    compose = _read("docker-compose.yml")
    web_block = _service_block(compose, "web", "scheduler")
    # Counts the YAML key itself (its own line), not the substring match
    # `${RIDGENOTE_EXTERNAL_URL:-}`'s interpolation also incidentally
    # contains once the default-value syntax is present.
    assert web_block.count("\n      RIDGENOTE_EXTERNAL_URL:") == 1
    assert "RIDGENOTE_EXTERNAL_URL: ${RIDGENOTE_EXTERNAL_URL:-}" in web_block


def test_scheduler_receives_only_what_the_purge_scheduler_actually_needs():
    # The approved V1 Compose architecture narrows scheduler's own
    # environment to exactly what `run_purge_scheduler` needs (identity,
    # debug/logging, database connection, purge settings) -- it does not
    # receive web-request-only settings (invitation expiry, forwarded
    # -host, SMTP) that have no effect on a service that never handles
    # HTTP requests or sends email itself.
    compose = _read("docker-compose.yml")
    scheduler_block = _service_block(compose, "scheduler", "postgres")
    assert "RIDGENOTE_PURGE_ENABLED: ${RIDGENOTE_PURGE_ENABLED:-true}" in scheduler_block
    assert (
        "RIDGENOTE_PURGE_INTERVAL_SECONDS: ${RIDGENOTE_PURGE_INTERVAL_SECONDS:-86400}"
        in scheduler_block
    )
    assert "RIDGENOTE_INVITATION_EXPIRY_MINUTES" not in scheduler_block
    assert "RIDGENOTE_USE_X_FORWARDED_HOST" not in scheduler_block
    assert "RIDGENOTE_SMTP_" not in scheduler_block


def test_env_example_contains_all_nine_new_pass_through_variables():
    env_example = _read("env.example")
    lines = env_example.splitlines()
    for var in (
        "RIDGENOTE_INVITATION_EXPIRY_MINUTES",
        "RIDGENOTE_USE_X_FORWARDED_HOST",
        "RIDGENOTE_SMTP_HOST",
        "RIDGENOTE_SMTP_PORT",
        "RIDGENOTE_SMTP_TLS_MODE",
        "RIDGENOTE_SMTP_USERNAME",
        "RIDGENOTE_SMTP_PASSWORD",
        "RIDGENOTE_SMTP_FROM_EMAIL",
        "RIDGENOTE_SMTP_FROM_NAME",
    ):
        assert any(line.startswith(f"{var}=") for line in lines), var


def test_env_example_smtp_password_placeholder_is_blank():
    env_example = _read("env.example")
    for line in env_example.splitlines():
        if line.startswith("RIDGENOTE_SMTP_PASSWORD="):
            assert line == "RIDGENOTE_SMTP_PASSWORD="
            break
    else:
        raise AssertionError("RIDGENOTE_SMTP_PASSWORD not found in env.example")


# -- bounded Docker log retention, canonical and
# production Compose parity -------------------------------------------

_LOGGING_BLOCK = (
    "logging:\n"
    "      driver: json-file\n"
    "      options:\n"
    '        max-size: "10m"\n'
    '        max-file: "5"'
)


def test_canonical_web_has_expected_logging_retention_policy():
    compose = _read_root("docker-compose.yml")
    web_block = _service_block(compose, "web", "scheduler")
    assert _LOGGING_BLOCK in web_block


def test_canonical_scheduler_has_expected_logging_retention_policy():
    compose = _read_root("docker-compose.yml")
    scheduler_block = _service_block(compose, "scheduler", "postgres")
    assert _LOGGING_BLOCK in scheduler_block


def test_canonical_postgres_has_expected_logging_retention_policy():
    compose = _read_root("docker-compose.yml")
    postgres_block = _service_block_to_end(compose, "postgres")
    assert _LOGGING_BLOCK in postgres_block


def test_deploy_web_has_expected_logging_retention_policy():
    compose = _read("docker-compose.yml")
    web_block = _service_block(compose, "web", "scheduler")
    assert _LOGGING_BLOCK in web_block


def test_deploy_scheduler_has_expected_logging_retention_policy():
    compose = _read("docker-compose.yml")
    scheduler_block = _service_block(compose, "scheduler", "postgres")
    assert _LOGGING_BLOCK in scheduler_block


def test_deploy_postgres_has_expected_logging_retention_policy():
    compose = _read("docker-compose.yml")
    postgres_block = _service_block_to_end(compose, "postgres")
    assert _LOGGING_BLOCK in postgres_block


def test_no_logging_driver_divergence_between_canonical_and_deploy():
    canonical = _read_root("docker-compose.yml")
    production = _read("docker-compose.yml")
    # Exactly one driver is ever named, and it is the same one, in both
    # files -- catches an accidental switch to `local` or any other
    # driver in only one of the two Compose files.
    assert canonical.count("driver: json-file") == 3
    assert production.count("driver: json-file") == 3
    assert "driver:" not in canonical.replace("driver: json-file", "")
    assert "driver:" not in production.replace("driver: json-file", "")


def test_every_service_has_a_logging_block_in_both_compose_files():
    canonical = _read_root("docker-compose.yml")
    production = _read("docker-compose.yml")
    assert canonical.count("logging:") == 3
    assert production.count("logging:") == 3
