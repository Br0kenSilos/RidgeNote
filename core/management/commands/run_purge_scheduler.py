"""Automatic purge scheduler integration.

A long-running command. When explicitly enabled via
`RIDGENOTE_PURGE_ENABLED`, it periodically calls the independently
shipped `notes.purge.run_purge_cycle()` -- no purge logic lives here.
Disabled by default, so every existing deployment that does not add
the new environment variables keeps its current, fully idle scheduler
behavior.
"""

import logging
import os
import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import Error as DjangoDatabaseError
from django.db import close_old_connections
from notes.purge import PurgeAuditWriteError, run_purge_cycle

logger = logging.getLogger(__name__)

ENV_PURGE_ENABLED = "RIDGENOTE_PURGE_ENABLED"
ENV_PURGE_INTERVAL_SECONDS = "RIDGENOTE_PURGE_INTERVAL_SECONDS"

DEFAULT_PURGE_INTERVAL_SECONDS = 86400
SHUTDOWN_POLL_INTERVAL_SECONDS = 5

NOTE_BATCH_SIZE = 200
FOLDER_BATCH_SIZE = 200


def parse_purge_enabled(raw: str | None) -> bool:
    if raw is None:
        return False
    value = raw.strip()
    if value == "":
        raise ValueError(
            f"{ENV_PURGE_ENABLED} must be 'true' or 'false' (case-insensitive); "
            "an empty value is not allowed."
        )
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"{ENV_PURGE_ENABLED} must be 'true' or 'false' (case-insensitive).")


def parse_purge_interval_seconds(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_PURGE_INTERVAL_SECONDS
    value = raw.strip()
    if value == "":
        raise ValueError(f"{ENV_PURGE_INTERVAL_SECONDS} must be a positive integer.")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{ENV_PURGE_INTERVAL_SECONDS} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{ENV_PURGE_INTERVAL_SECONDS} must be a positive integer.")
    return parsed


class Command(BaseCommand):
    help = (
        "Long-running scheduler that periodically invokes the one-shot final-purge "
        "service when RIDGENOTE_PURGE_ENABLED is explicitly true. Disabled by default."
    )

    def handle(self, *args: object, **options: object) -> None:
        try:
            enabled = parse_purge_enabled(os.environ.get(ENV_PURGE_ENABLED))
            interval_seconds = parse_purge_interval_seconds(
                os.environ.get(ENV_PURGE_INTERVAL_SECONDS)
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self._shutdown_requested = False
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

        logger.info("Purge scheduler started. purge_enabled=%s", enabled)

        if not enabled:
            logger.info("Automatic purge is disabled (RIDGENOTE_PURGE_ENABLED is not true).")
            self._idle_until_shutdown()
            logger.info("Purge scheduler stopped cleanly.")
            return

        logger.info("Automatic purge is enabled. interval_seconds=%s", interval_seconds)

        while not self._shutdown_requested:
            self._run_cycle()
            self._sleep_interruptible(interval_seconds)

        logger.info("Purge scheduler stopped cleanly.")

    def _handle_shutdown_signal(self, signum: int, frame: object) -> None:
        self._shutdown_requested = True
        logger.info(
            "Purge scheduler received signal %s; shutting down after any in-progress cycle.",
            signum,
        )

    def _idle_until_shutdown(self) -> None:
        while not self._shutdown_requested:
            time.sleep(SHUTDOWN_POLL_INTERVAL_SECONDS)

    def _sleep_interruptible(self, interval_seconds: int) -> None:
        remaining = interval_seconds
        while remaining > 0 and not self._shutdown_requested:
            tick = min(SHUTDOWN_POLL_INTERVAL_SECONDS, remaining)
            time.sleep(tick)
            remaining -= tick

    def _run_cycle(self) -> None:
        close_old_connections()
        try:
            result = run_purge_cycle(
                dry_run=False,
                note_batch_size=NOTE_BATCH_SIZE,
                folder_batch_size=FOLDER_BATCH_SIZE,
            )
        except PurgeAuditWriteError:
            logger.critical(
                "Scheduled purge cycle's aggregate audit write failed; any row-level "
                "purges that already committed remain committed.",
                exc_info=True,
            )
            return
        except DjangoDatabaseError:
            logger.error("Scheduled purge cycle failed due to a database error.", exc_info=True)
            close_old_connections()
            return
        except Exception:
            logger.error("Scheduled purge cycle failed unexpectedly.", exc_info=True)
            return

        if not result.lock_acquired:
            logger.info("Scheduled purge cycle skipped: advisory lock held elsewhere.")
            return

        if result.notes_failed or result.folders_failed:
            logger.warning(
                "Scheduled purge cycle completed with isolated row failures. "
                "notes_failed=%s folders_failed=%s",
                result.notes_failed,
                result.folders_failed,
            )

        if result.folders_blocked:
            logger.info(
                "Scheduled purge cycle completed with blocked folders. folders_blocked=%s",
                result.folders_blocked,
            )
