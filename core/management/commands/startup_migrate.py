"""Automatic startup migrations.

Waits for PostgreSQL to become reachable with usable credentials, then
serializes migration application across the `web` and `scheduler`
containers -- which may both invoke this command at roughly the same
time on an ordinary `docker compose up -d` -- using a PostgreSQL
session-level advisory lock, and applies any pending migrations via the
ordinary `migrate --noinput` management command. Django's own migration
state table remains the sole source of truth; no separate schema marker
is introduced here.

Invoked automatically by `docker-entrypoint.sh` before the container's
real command starts. See that script for the narrow, documented cases
where invoking this command automatically is intentionally skipped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# Distinct from accounts.services.ADMIN_OPERATION_LOCK_ID (502_001_001)
# and notes.purge.PURGE_CYCLE_LOCK_ID (502_002_001).
STARTUP_MIGRATION_LOCK_ID = 502_003_001

CONNECT_TIMEOUT_SECONDS = 5
RETRY_INITIAL_DELAY_SECONDS = 1
RETRY_MAX_DELAY_SECONDS = 5
RETRY_TOTAL_TIMEOUT_SECONDS = 90
RETRY_LOG_THROTTLE_SECONDS = 15


class FatalDatabaseConfigurationError(Exception):
    """The PostgreSQL server was reached but rejected the connection
    outright (bad credentials, unknown database, or similar) -- retrying
    cannot help, so this is raised instead of continuing to retry."""


def connection_kwargs() -> dict[str, object]:
    db = settings.DATABASES["default"]
    return {
        "host": db["HOST"],
        "port": db["PORT"],
        "dbname": db["NAME"],
        "user": db["USER"],
        "password": db["PASSWORD"],
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }


def is_fatal_database_error(exc: psycopg.OperationalError) -> bool:
    # psycopg does not reliably expose a distinct SQLSTATE or exception
    # subclass for connection-open-time failures -- verified directly
    # against a real PostgreSQL 17 server: a bad password, an unknown
    # database, and a plain connection-refused all raise the same
    # psycopg.OperationalError with sqlstate left unset. A "FATAL:"
    # marker in the message is PostgreSQL's own wire-protocol signal
    # that the server was reached and actively rejected the connection
    # (bad credentials, unknown database, ...), as opposed to a
    # lower-level networking failure (connection refused, timeout)
    # where no server response was ever received. This is a structural
    # signal from the protocol, not an arbitrary phrase match -- and no
    # more reliable signal is available at this stage.
    return "FATAL:" in str(exc)


def wait_for_database(
    *,
    connect: Callable[..., object] = psycopg.connect,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    deadline = now() + RETRY_TOTAL_TIMEOUT_SECONDS
    delay = RETRY_INITIAL_DELAY_SECONDS
    last_log: float | None = None
    attempt = 0
    while True:
        attempt += 1
        try:
            conn = connect(**connection_kwargs())
        except psycopg.OperationalError as exc:
            if is_fatal_database_error(exc):
                raise FatalDatabaseConfigurationError(str(exc)) from exc
            remaining = deadline - now()
            if remaining <= 0:
                raise CommandError(
                    f"PostgreSQL was not reachable after "
                    f"{RETRY_TOTAL_TIMEOUT_SECONDS} seconds: {exc}"
                ) from exc
            if last_log is None or (now() - last_log) >= RETRY_LOG_THROTTLE_SECONDS:
                logger.info(
                    "Waiting for PostgreSQL (attempt %s, %.0fs remaining)...",
                    attempt,
                    remaining,
                )
                last_log = now()
            sleep(min(delay, max(remaining, 0)))
            delay = min(delay * 2, RETRY_MAX_DELAY_SECONDS)
            continue
        else:
            conn.close()
            logger.info("PostgreSQL reachable.")
            return


def run_locked_migration(*, connect: Callable[..., object] = psycopg.connect) -> None:
    lock_conn = connect(**connection_kwargs())
    lock_conn.autocommit = True
    try:
        logger.info("Waiting for migration lock...")
        with lock_conn.cursor() as cursor:
            cursor.execute("select pg_advisory_lock(%s)", [STARTUP_MIGRATION_LOCK_ID])
        logger.info("Migration lock acquired.")
        try:
            logger.info("Applying database migrations...")
            call_command("migrate", "--noinput")
            logger.info("Migrations complete.")
        finally:
            with lock_conn.cursor() as cursor:
                cursor.execute("select pg_advisory_unlock(%s)", [STARTUP_MIGRATION_LOCK_ID])
            logger.info("Migration lock released.")
    finally:
        lock_conn.close()


class Command(BaseCommand):
    help = (
        "Wait for PostgreSQL, then apply any pending Django migrations under a "
        "PostgreSQL advisory lock. Runs automatically at container startup via "
        "docker-entrypoint.sh, before the real web/scheduler command starts."
    )

    def handle(self, *args: object, **options: object) -> None:
        logger.info("Startup migration check beginning.")
        try:
            wait_for_database()
        except FatalDatabaseConfigurationError as exc:
            raise CommandError(
                "PostgreSQL rejected the connection -- check the "
                "RIDGENOTE_DATABASE_* settings (this is not retried, since a "
                f"bad credential or unknown database cannot resolve itself): {exc}"
            ) from exc
        run_locked_migration()
        logger.info("Startup migration check complete.")
