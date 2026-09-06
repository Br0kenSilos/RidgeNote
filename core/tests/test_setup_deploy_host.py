"""Isolated tests for scripts/setup-deploy-host.sh.

All tests run against temporary directories with fake `docker` and `sudo`
executables (core/tests/fixtures/fake_docker.py, fake_sudo.py) placed
first on PATH -- no real Docker daemon, no real privilege escalation, no
real host mutation, and no interaction with any live deployment. Real
`openssl`/`sed`/`grep`/`awk`/`stat`/`chmod`/`mkdir` are used as-is, since
they only ever touch the isolated temporary directory under test.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup-deploy-host.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEPLOY_SOURCE = REPO_ROOT / "deploy"
BASH = shutil.which("bash") or "/bin/bash"

# `scripts/` is never copied into the built application image (see the
# matching comment in core/tests/test_deployment_bundle.py) -- these
# tests exercise the real script file and are only meaningful from a
# full repository checkout. Skip honestly, rather than error, when that
# checkout context is absent.
pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(),
    reason="scripts/setup-deploy-host.sh is not present outside a full repository checkout",
)


def test_script_syntax_is_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_script_never_uses_chmod_777():
    assert "777" not in SCRIPT.read_text()


@pytest.fixture
def fake_bin(tmp_path):
    """A directory containing fake docker/sudo, prepended to PATH."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name, source in [("docker", "fake_docker.py"), ("sudo", "fake_sudo.py")]:
        target = bin_dir / name
        target.write_text((FIXTURES / source).read_text())
        target.chmod(0o755)
    return bin_dir


@pytest.fixture
def deploy_dir(tmp_path):
    """A deployment directory pre-populated with the three bundle files."""
    d = tmp_path / "ridgenote-deploy"
    d.mkdir()
    for name in ("docker-compose.yml", ".env.example", "README.md"):
        shutil.copy(DEPLOY_SOURCE / name, d / name)
    return d


def run_helper(args, fake_bin, extra_env=None, cwd=None):
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_help_exits_zero_and_shows_usage(fake_bin):
    result = run_helper(["--help"], fake_bin)
    assert result.returncode == 0
    assert "--deploy-dir" in result.stdout


def test_missing_required_argument_fails(fake_bin):
    result = run_helper(["--deploy-dir", "/tmp/x"], fake_bin)
    assert result.returncode == 2
    assert "required" in result.stderr


def test_missing_deployment_file_fails(fake_bin, deploy_dir):
    (deploy_dir / "docker-compose.yml").unlink()
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
    )
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()


def test_deploy_directory_symlink_is_rejected(fake_bin, tmp_path, deploy_dir):
    link = tmp_path / "linked-deploy"
    link.symlink_to(deploy_dir)
    result = run_helper(
        [
            "--deploy-dir",
            str(link),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
    )
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_required_file_symlink_is_rejected(fake_bin, deploy_dir, tmp_path):
    real_file = tmp_path / "elsewhere-compose.yml"
    real_file.write_text((deploy_dir / "docker-compose.yml").read_text())
    (deploy_dir / "docker-compose.yml").unlink()
    (deploy_dir / "docker-compose.yml").symlink_to(real_file)
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
    )
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


@pytest.mark.parametrize("dangerous", ["/", "/home", "/var", "/etc", "/usr", "/tmp"])
def test_dangerous_deploy_paths_are_rejected(fake_bin, dangerous):
    result = run_helper(
        [
            "--deploy-dir",
            dangerous,
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
    )
    assert result.returncode != 0
    assert "unsafe" in result.stderr.lower()


def test_missing_docker_fails(deploy_dir, tmp_path):
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)
    result = subprocess.run(
        [
            BASH,
            str(SCRIPT),
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0


def test_compose_v2_unavailable_fails(fake_bin, deploy_dir):
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
        extra_env={"FAKE_DOCKER_NO_COMPOSE": "1"},
    )
    assert result.returncode != 0
    assert "compose" in result.stderr.lower()


def test_unpinned_postgres_image_is_rejected(fake_bin, deploy_dir):
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
        extra_env={"FAKE_DOCKER_BAD_IMAGE": "1"},
    )
    assert result.returncode != 0
    assert "digest" in result.stderr.lower()


def test_malformed_uid_gid_is_rejected(fake_bin, deploy_dir):
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
        extra_env={"FAKE_DOCKER_BAD_UID": "1"},
    )
    assert result.returncode != 0
    assert "numeric" in result.stderr.lower()


class TestHappyPath:
    """Full run against a fully faked, isolated environment."""

    @pytest.fixture
    def result_and_dir(self, fake_bin, deploy_dir, tmp_path):
        docker_log = tmp_path / "docker.log"
        result = run_helper(
            [
                "--deploy-dir",
                str(deploy_dir),
                "--host",
                "192.0.2.1",
                "--external-url",
                "http://192.0.2.1:8000",
            ],
            fake_bin,
            extra_env={"FAKE_DOCKER_LOG": str(docker_log)},
        )
        return result, deploy_dir, docker_log

    def test_succeeds(self, result_and_dir):
        result, _, _ = result_and_dir
        assert result.returncode == 0, result.stderr

    def test_directories_created_with_expected_modes(self, result_and_dir):
        _, deploy_dir, _ = result_and_dir
        data = deploy_dir / "data"
        pg_data = deploy_dir / "data" / "postgres"
        backups = deploy_dir / "backups"
        pg_backups = deploy_dir / "backups" / "postgres"
        assert data.is_dir()
        assert pg_data.is_dir()
        assert backups.is_dir()
        assert pg_backups.is_dir()
        assert stat.S_IMODE(backups.stat().st_mode) == 0o700
        assert stat.S_IMODE(pg_backups.stat().st_mode) == 0o700

    def test_env_created_with_restrictive_mode(self, result_and_dir):
        _, deploy_dir, _ = result_and_dir
        env_file = deploy_dir / ".env"
        assert env_file.is_file()
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    def test_secret_placeholders_are_resolved(self, result_and_dir):
        _, deploy_dir, _ = result_and_dir
        content = (deploy_dir / ".env").read_text()
        assert "replace-with-" not in content

    def test_image_reference_is_deliberately_left_untouched_by_the_helper(self, result_and_dir):
        # RIDGENOTE_IMAGE selection remains the operator's own choice
        # (deploy/README.md's "Image tags" section) -- this helper only
        # prepares directories and secrets, and must not silently invent
        # or alter an image reference; whatever .env.example shipped is
        # carried through unchanged.
        _, deploy_dir, _ = result_and_dir
        example_value = None
        for line in (deploy_dir / ".env.example").read_text().splitlines():
            if line.startswith("RIDGENOTE_IMAGE="):
                example_value = line
                break
        assert example_value is not None
        content = (deploy_dir / ".env").read_text()
        assert example_value in content

    def test_only_one_user_facing_database_password_is_written(self, result_and_dir):
        _, deploy_dir, _ = result_and_dir
        content = (deploy_dir / ".env").read_text()
        values = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
        assert values["RIDGENOTE_DATABASE_PASSWORD"]
        assert "POSTGRES_PASSWORD" not in values

    def test_allowed_hosts_include_supplied_host_and_loopback(self, result_and_dir):
        _, deploy_dir, _ = result_and_dir
        content = (deploy_dir / ".env").read_text()
        values = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
        hosts = values["RIDGENOTE_ALLOWED_HOSTS"].split(",")
        assert "192.0.2.1" in hosts
        assert "127.0.0.1" in hosts

    def test_generated_secret_never_appears_in_output(self, result_and_dir):
        result, deploy_dir, _ = result_and_dir
        content = (deploy_dir / ".env").read_text()
        values = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
        secret = values["RIDGENOTE_SECRET_KEY"]
        db_password = values["RIDGENOTE_DATABASE_PASSWORD"]
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert db_password not in result.stdout
        assert db_password not in result.stderr

    def test_compose_config_quiet_was_invoked(self, result_and_dir):
        _, _, docker_log = result_and_dir
        log = docker_log.read_text()
        assert "--quiet" in log

    def test_no_startup_or_migration_commands_run(self, result_and_dir):
        _, _, docker_log = result_and_dir
        log = docker_log.read_text()
        for line in log.splitlines():
            tokens = line.split()
            if tokens[:1] == ["compose"]:
                assert "up" not in tokens
                assert "migrate" not in " ".join(tokens)

    def test_printed_next_steps_no_longer_instruct_a_manual_migration(self, result_and_dir):
        # Migrations now apply automatically at container startup (see
        # deploy/README.md, "Automatic startup migrations") -- the
        # helper's own printed next-commands must match that, not the
        # old explicit "docker compose run --rm web python manage.py
        # migrate --noinput" step.
        result, _, _ = result_and_dir
        assert "manage.py migrate" not in result.stdout
        assert "docker compose up -d postgres" not in result.stdout
        assert "docker compose up -d web scheduler" not in result.stdout
        assert "docker compose up -d" in result.stdout

    def test_nothing_mutated_outside_deploy_dir(self, result_and_dir, tmp_path):
        _, deploy_dir, _ = result_and_dir
        # Only the fakebin and the deploy dir itself should exist under
        # tmp_path's direct children relevant to this run.
        for child in deploy_dir.parent.iterdir():
            if child == deploy_dir:
                continue
            # fakebin and docker.log are the test's own fixtures, not
            # something the script created.
            assert child.name in {"fakebin", "docker.log"}


def test_existing_env_is_not_overwritten(fake_bin, deploy_dir):
    (deploy_dir / ".env").write_text("RIDGENOTE_SECRET_KEY=already-here\n")
    before = (deploy_dir / ".env").read_text()
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert (deploy_dir / ".env").read_text() == before


def test_validate_only_without_env_fails(fake_bin, deploy_dir):
    result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
            "--validate-only",
        ],
        fake_bin,
    )
    assert result.returncode != 0


def test_validate_only_passes_against_a_freshly_created_env_and_mutates_nothing(
    fake_bin, deploy_dir, tmp_path
):
    docker_log = tmp_path / "docker.log"
    create_result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
        ],
        fake_bin,
        extra_env={"FAKE_DOCKER_LOG": str(docker_log)},
    )
    assert create_result.returncode == 0, create_result.stderr

    # RIDGENOTE_IMAGE needs no further edit before validating -- the
    # shipped .env.example default is already a real, usable image
    # reference (deploy/README.md, "Image tags"), not a placeholder the
    # operator must resolve first.
    env_path = deploy_dir / ".env"
    before_mtime = env_path.stat().st_mtime
    before_content = env_path.read_text()

    validate_result = run_helper(
        [
            "--deploy-dir",
            str(deploy_dir),
            "--host",
            "192.0.2.1",
            "--external-url",
            "http://192.0.2.1:8000",
            "--validate-only",
        ],
        fake_bin,
    )
    assert validate_result.returncode == 0, validate_result.stderr
    assert "Validation passed" in validate_result.stdout
    assert env_path.stat().st_mtime == before_mtime
    assert env_path.read_text() == before_content
