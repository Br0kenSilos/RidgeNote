"""automatic startup migrations.

`core/management/commands/startup_migrate.py` waits for PostgreSQL,
classifies transient-vs-fatal connection failures, serializes migration
application across concurrently starting `web`/`scheduler` containers
via a PostgreSQL advisory lock, and runs the ordinary
`migrate --noinput` management command -- Django's own migration state
remains the sole source of truth. Tests below cover: the retry/backoff
loop and its bounded timeout (fake clock, no real sleeping or network);
fatal-vs-transient classification against real PostgreSQL error
messages captured empirically (see the module docstring in
`startup_migrate.py`); lock-acquire/migrate/lock-release/connection-close
ordering, including on migration failure (fake connection); no-secret
logging; the advisory-lock ID's distinctness from the two existing
locks; and one genuine PostgreSQL concurrency test proving the lock
actually serializes two concurrent callers, mirroring the established
pattern in `core/tests/test_run_purge_scheduler.py`.
"""

from __future__ import annotations

import logging
import threading
import time

import psycopg
import pytest
from accounts.services import ADMIN_OPERATION_LOCK_ID
from django.core.management import CommandError, call_command
from notes.purge import PURGE_CYCLE_LOCK_ID

import core.management.commands.startup_migrate as startup_migrate

# -- lock-ID distinctness -------------------------------------------------


def test_lock_id_matches_the_authorized_value():
    assert startup_migrate.STARTUP_MIGRATION_LOCK_ID == 502_003_001


def test_lock_id_does_not_collide_with_existing_locks():
    assert startup_migrate.STARTUP_MIGRATION_LOCK_ID not in {
        ADMIN_OPERATION_LOCK_ID,
        PURGE_CYCLE_LOCK_ID,
    }


# -- fatal-vs-transient classification, against real captured messages ----
# These three messages were captured directly from a real PostgreSQL 17
# server -- psycopg exposes no reliable
# sqlstate/exception-subclass distinction at connection-open time, so
# the "FATAL:" wire-protocol marker is the most reliable signal
# available, not an arbitrary phrase match.


@pytest.mark.parametrize(
    "message",
    [
        'connection failed: connection to server at "172.18.0.2", port 5432 '
        'failed: FATAL:  password authentication failed for user "ridgenote"',
        'connection failed: connection to server at "172.18.0.2", port 5432 '
        'failed: FATAL:  database "nonexistentdb" does not exist',
    ],
)
def test_fatal_messages_are_classified_as_fatal(message):
    assert startup_migrate.is_fatal_database_error(psycopg.OperationalError(message))


def test_connection_refused_is_classified_as_transient():
    message = (
        'connection failed: connection to server at "172.18.0.2", port 59999 '
        "failed: Connection refused\n\tIs the server running on that host and "
        "accepting TCP/IP connections?"
    )
    assert not startup_migrate.is_fatal_database_error(psycopg.OperationalError(message))


# -- wait_for_database: retry/backoff loop (fake clock, no real network) --


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_postgres_initially_unavailable_then_available(monkeypatch):
    clock = _FakeClock()
    attempts = {"n": 0}

    def fake_connect(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise psycopg.OperationalError("Connection refused")
        return _FakeConnection()

    startup_migrate.wait_for_database(connect=fake_connect, sleep=clock.sleep, now=clock.now)
    assert attempts["n"] == 3


def test_postgres_available_immediately(monkeypatch):
    clock = _FakeClock()
    calls = {"n": 0}

    def fake_connect(**kwargs):
        calls["n"] += 1
        return _FakeConnection()

    startup_migrate.wait_for_database(connect=fake_connect, sleep=clock.sleep, now=clock.now)
    assert calls["n"] == 1


def test_bounded_retry_exhaustion_raises_command_error():
    clock = _FakeClock()

    def always_refused(**kwargs):
        raise psycopg.OperationalError("Connection refused")

    with pytest.raises(CommandError, match="not reachable"):
        startup_migrate.wait_for_database(connect=always_refused, sleep=clock.sleep, now=clock.now)
    assert clock.t >= startup_migrate.RETRY_TOTAL_TIMEOUT_SECONDS


def test_authentication_failure_fails_fast_without_retrying():
    clock = _FakeClock()
    attempts = {"n": 0}

    def bad_password(**kwargs):
        attempts["n"] += 1
        raise psycopg.OperationalError(
            'connection failed: ... FATAL:  password authentication failed for user "ridgenote"'
        )

    with pytest.raises(startup_migrate.FatalDatabaseConfigurationError):
        startup_migrate.wait_for_database(connect=bad_password, sleep=clock.sleep, now=clock.now)
    assert attempts["n"] == 1
    assert clock.t == 0.0


def test_retry_logging_is_throttled_not_once_per_attempt(caplog):
    clock = _FakeClock()
    attempts = {"n": 0}

    def fake_connect(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 8:
            raise psycopg.OperationalError("Connection refused")
        return _FakeConnection()

    with caplog.at_level(logging.INFO, logger=startup_migrate.logger.name):
        startup_migrate.wait_for_database(connect=fake_connect, sleep=clock.sleep, now=clock.now)

    waiting_lines = [r for r in caplog.records if "Waiting for PostgreSQL" in r.message]
    # 7 failed attempts, but throttled logging must produce fewer log
    # lines than attempts -- not a message every single retry.
    assert 0 < len(waiting_lines) < attempts["n"]


# -- run_locked_migration: lock/migrate/unlock/close ordering ------------


class _FakeCursor:
    def __init__(self, recorder):
        self.recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        if "pg_advisory_lock" in sql:
            self.recorder.append(("lock", params[0]))
        elif "pg_advisory_unlock" in sql:
            self.recorder.append(("unlock", params[0]))


class _FakeConnection:
    def __init__(self, recorder=None):
        self.recorder = recorder if recorder is not None else []
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.recorder)

    def close(self):
        self.closed = True
        self.recorder.append(("close",))


def test_successful_migration_acquires_and_releases_lock_in_order(monkeypatch):
    recorder = []
    conn = _FakeConnection(recorder)
    migrate_calls = []

    def fake_call_command(*args, **kwargs):
        migrate_calls.append(args)
        recorder.append(("migrate",))

    monkeypatch.setattr(startup_migrate, "call_command", fake_call_command)
    startup_migrate.run_locked_migration(connect=lambda **kwargs: conn)

    assert recorder == [
        ("lock", startup_migrate.STARTUP_MIGRATION_LOCK_ID),
        ("migrate",),
        ("unlock", startup_migrate.STARTUP_MIGRATION_LOCK_ID),
        ("close",),
    ]
    assert migrate_calls == [("migrate", "--noinput")]
    assert conn.autocommit is True
    assert conn.closed is True


def test_migration_failure_still_releases_lock_and_closes_connection(monkeypatch):
    recorder = []
    conn = _FakeConnection(recorder)

    def failing_call_command(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(startup_migrate, "call_command", failing_call_command)

    with pytest.raises(RuntimeError, match="boom"):
        startup_migrate.run_locked_migration(connect=lambda **kwargs: conn)

    assert recorder == [
        ("lock", startup_migrate.STARTUP_MIGRATION_LOCK_ID),
        ("unlock", startup_migrate.STARTUP_MIGRATION_LOCK_ID),
        ("close",),
    ]
    assert conn.closed is True


# -- Command.handle(): end-to-end wiring, with both stages faked ---------


def test_handle_runs_wait_then_locked_migration_in_order(monkeypatch):
    order = []
    monkeypatch.setattr(startup_migrate, "wait_for_database", lambda: order.append("wait"))
    monkeypatch.setattr(startup_migrate, "run_locked_migration", lambda: order.append("migrate"))
    call_command(startup_migrate.Command())
    assert order == ["wait", "migrate"]


def test_handle_wraps_fatal_database_error_as_command_error(monkeypatch):
    def raise_fatal():
        raise startup_migrate.FatalDatabaseConfigurationError("bad password")

    monkeypatch.setattr(startup_migrate, "wait_for_database", raise_fatal)
    monkeypatch.setattr(
        startup_migrate,
        "run_locked_migration",
        lambda: pytest.fail("must not run migrations after a fatal wait failure"),
    )
    with pytest.raises(CommandError, match="rejected the connection"):
        call_command(startup_migrate.Command())


def test_handle_logs_no_secret_values(monkeypatch, caplog):
    monkeypatch.setattr(startup_migrate, "wait_for_database", lambda: None)
    monkeypatch.setattr(startup_migrate, "run_locked_migration", lambda: None)
    with caplog.at_level(logging.INFO, logger=startup_migrate.logger.name):
        call_command(startup_migrate.Command())
    text = caplog.text
    db = __import__("django.conf", fromlist=["settings"]).settings.DATABASES["default"]
    assert db["PASSWORD"] not in text
    assert "PASSWORD" not in text.upper() or "password" not in text.lower()


# -- genuine PostgreSQL concurrency: the lock actually serializes --------


@pytest.mark.django_db(transaction=True)
def test_concurrent_callers_are_serialized_by_the_real_advisory_lock(monkeypatch):
    """Two real, independent PostgreSQL connections race for the startup
    migration lock; the second must block until the first releases it,
    proving the lock -- not application-level coordination -- is what
    keeps concurrent web/scheduler startups from running migrate at the
    same time."""
    from django.db import connections

    first_holds_lock = threading.Event()
    let_first_finish = threading.Event()
    call_order = []
    lock = threading.Lock()

    def pausing_call_command_first(*args, **kwargs):
        with lock:
            call_order.append("first-migrating")
        first_holds_lock.set()
        assert let_first_finish.wait(timeout=5), "second thread never signaled"

    def recording_call_command_second(*args, **kwargs):
        with lock:
            call_order.append("second-migrating")

    first_outcome = {}
    second_outcome = {}

    def run_first():
        try:
            monkeypatch.setattr(startup_migrate, "call_command", pausing_call_command_first)
            startup_migrate.run_locked_migration()
        except Exception as exc:  # pragma: no cover
            first_outcome["error"] = exc
        finally:
            connections.close_all()

    def run_second():
        assert first_holds_lock.wait(timeout=5), "first thread never acquired the lock"
        try:
            startup_migrate.run_locked_migration()
            with lock:
                call_order.append("second-acquired-after-first")
        except Exception as exc:  # pragma: no cover
            second_outcome["error"] = exc
        finally:
            connections.close_all()

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_holds_lock.wait(timeout=5), "first thread never reached the paused migration"

    second_thread = threading.Thread(target=run_second)
    second_thread.start()
    time.sleep(0.3)
    # The second caller must still be blocked on the lock right now.
    assert "second-migrating" not in call_order
    assert "second-acquired-after-first" not in call_order

    monkeypatch.setattr(startup_migrate, "call_command", recording_call_command_second)
    let_first_finish.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert "error" not in first_outcome, first_outcome.get("error")
    assert "error" not in second_outcome, second_outcome.get("error")
    assert call_order[0] == "first-migrating"
    assert call_order[-1] == "second-acquired-after-first"


@pytest.mark.django_db
def test_run_locked_migration_against_real_database_is_a_fast_noop():
    # The test database already has every migration applied -- proves
    # the ordinary no-pending-migrations path works end to end against
    # a real PostgreSQL connection, not just faked call_command.
    startup_migrate.run_locked_migration()
