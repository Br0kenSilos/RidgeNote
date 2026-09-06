"""Isolated tests for docker-entrypoint.sh.

All tests run the real script against a fake `python` executable
(core/tests/fixtures/fake_python.py) placed first on PATH -- no real
Django settings, database, or management-command machinery, and no
interaction with any live deployment. This proves the entrypoint's own
decision logic (when to run the startup-migration check, when to skip
it, and that it always hands off via `exec "$@"`) independently of
`startup_migrate`'s own database/retry/locking behavior, which is
covered separately in `core/tests/test_startup_migrate.py`.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The Dockerfile copies this script to /usr/local/bin/docker-entrypoint.sh
# inside the built image, not to /app/docker-entrypoint.sh (REPO_ROOT here)
# -- these tests exercise the real repo-root copy and are only meaningful
# from a full repository checkout. Skip honestly, rather than error, when
# that checkout-relative path is absent.
pytestmark = pytest.mark.skipif(
    not ENTRYPOINT.is_file(),
    reason="docker-entrypoint.sh is not present at the repo-root checkout path "
    "outside a full repository checkout",
)


def test_entrypoint_syntax_is_valid():
    result = subprocess.run(["bash", "-n", str(ENTRYPOINT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_entrypoint_uses_strict_mode():
    assert "set -euo pipefail" in ENTRYPOINT.read_text()


def test_entrypoint_uses_exec_handoff():
    text = ENTRYPOINT.read_text()
    assert 'exec "$@"' in text


@pytest.fixture
def fake_bin(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    target = bin_dir / "python"
    target.write_text((FIXTURES / "fake_python.py").read_text())
    target.chmod(0o755)
    return bin_dir


def run_entrypoint(args, fake_bin, tmp_path, extra_env=None):
    log_path = tmp_path / "fake-python.log"
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_PYTHON_LOG"] = str(log_path)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(ENTRYPOINT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    calls = log_path.read_text().splitlines() if log_path.exists() else []
    return result, calls


def test_default_command_runs_startup_migration_then_execs(fake_bin, tmp_path):
    # "python manage.py runserver" is not one of the two narrow skip
    # cases, so the automatic check must run first.
    result, calls = run_entrypoint(["python", "manage.py", "runserver"], fake_bin, tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == ["manage.py startup_migrate", "manage.py runserver"]


def test_gunicorn_command_runs_startup_migration_first(fake_bin, tmp_path):
    # The real web CMD is "gunicorn ...", never a "python ..." argv at
    # all -- the fake here stands in for gunicorn just to prove the
    # startup check still runs before *any* non-skipped command.
    gunicorn = fake_bin / "gunicorn"
    gunicorn.write_text((FIXTURES / "fake_python.py").read_text())
    gunicorn.chmod(0o755)
    result, calls = run_entrypoint(["gunicorn", "ridgenote.wsgi:application"], fake_bin, tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == ["manage.py startup_migrate", "ridgenote.wsgi:application"]


def test_explicit_manual_migrate_skips_the_automatic_check(fake_bin, tmp_path):
    result, calls = run_entrypoint(
        ["python", "manage.py", "migrate", "--noinput"], fake_bin, tmp_path
    )
    assert result.returncode == 0, result.stderr
    # Only the explicit invocation ran -- not a second, automatic one.
    assert calls == ["manage.py migrate --noinput"]


def test_direct_startup_migrate_invocation_does_not_recurse(fake_bin, tmp_path):
    result, calls = run_entrypoint(["python", "manage.py", "startup_migrate"], fake_bin, tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == ["manage.py startup_migrate"]


def test_skip_env_var_bypasses_the_automatic_check(fake_bin, tmp_path):
    result, calls = run_entrypoint(
        ["python", "manage.py", "dbshell"],
        fake_bin,
        tmp_path,
        extra_env={"RIDGENOTE_SKIP_STARTUP_MIGRATION": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert calls == ["manage.py dbshell"]


def test_skip_env_var_unset_or_not_one_still_runs_the_check(fake_bin, tmp_path):
    result, calls = run_entrypoint(
        ["python", "manage.py", "dbshell"],
        fake_bin,
        tmp_path,
        extra_env={"RIDGENOTE_SKIP_STARTUP_MIGRATION": "0"},
    )
    assert result.returncode == 0, result.stderr
    assert calls == ["manage.py startup_migrate", "manage.py dbshell"]


def test_migration_failure_prevents_the_real_command_from_starting(fake_bin, tmp_path):
    result, calls = run_entrypoint(
        ["python", "manage.py", "runserver"],
        fake_bin,
        tmp_path,
        extra_env={"FAKE_PYTHON_STARTUP_MIGRATE_EXIT": "1"},
    )
    assert result.returncode != 0
    # Only the failed startup check ran -- "manage.py runserver" was
    # never reached because `set -e` stops the script on that failure.
    assert calls == ["manage.py startup_migrate"]


def test_arbitrary_command_arguments_survive_word_splitting(fake_bin, tmp_path):
    marker = tmp_path / "marker.txt"
    script = tmp_path / "record-args.sh"
    script.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{marker}"\n')
    script.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_PYTHON_LOG"] = str(tmp_path / "fake-python.log")
    result = subprocess.run(
        ["bash", str(ENTRYPOINT), str(script), "an arg with spaces", "another"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text().splitlines() == ["an arg with spaces", "another"]


def test_entrypoint_never_uses_root_specific_constructs():
    text = ENTRYPOINT.read_text()
    assert "sudo" not in text
    assert "chown" not in text
    assert "chmod" not in text
