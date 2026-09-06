"""One-shot final purge.

A dedicated module (kept separate from the already large `notes/services.py`)
implementing the narrow, manually invoked final-purge cycle: a fixed
cutoff computed once per cycle, a
PostgreSQL session-level advisory lock held across the whole cycle, and
independent top-level transactions per candidate row so that no cycle-wide
transaction is ever used. No purge logic belongs in the management command
that calls `run_purge_cycle()`.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.db import connection, transaction
from django.utils import timezone

from notes.models import Folder, Note
from notes.services import TRASH_RECOVERABLE_MAX_AGE

logger = logging.getLogger(__name__)

# Distinct from accounts.services.ADMIN_OPERATION_LOCK_ID (502_001_001).
PURGE_CYCLE_LOCK_ID = 502_002_001


@dataclass(frozen=True)
class PurgeCycleResult:
    run_id: str
    cutoff: datetime
    dry_run: bool
    lock_acquired: bool
    notes_eligible: int
    notes_purged: int
    notes_failed: int
    folders_eligible: int
    folders_purged: int
    folders_blocked: int
    folders_failed: int
    duration_seconds: float


def _try_acquire_purge_lock() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("select pg_try_advisory_lock(%s)", [PURGE_CYCLE_LOCK_ID])
        return cursor.fetchone()[0]


def _release_purge_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("select pg_advisory_unlock(%s)", [PURGE_CYCLE_LOCK_ID])


def _is_purge_eligible(*, trashed_at, cutoff: datetime) -> bool:
    return trashed_at is not None and trashed_at < cutoff


def _select_note_candidates(*, cutoff: datetime, batch_size: int) -> list[tuple[int, int]]:
    # candidate selection is only a coarse
    # prefilter -- it runs unlocked, while the owner may still be active,
    # and its `owner__deletion_scheduled_at__isnull=True` filter can go
    # stale the instant this query returns. `owner_id` is carried
    # alongside `id` so the per-candidate locked transaction below can
    # lock the owning User first without an extra unlocked fetch.
    return list(
        Note.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff)
        .filter(owner__deletion_scheduled_at__isnull=True)
        .order_by("trashed_at", "id")[:batch_size]
        .values_list("id", "owner_id")
    )


def _lock_user_for_purge(*, owner_id: int) -> User | None:
    return User.objects.select_for_update().filter(pk=owner_id).first()


def _lock_note_for_purge(*, note_id: int) -> Note | None:
    try:
        return Note.objects.select_for_update().get(pk=note_id)
    except Note.DoesNotExist:
        return None


def _purge_eligible_notes(
    *, cutoff: datetime, batch_size: int, dry_run: bool
) -> tuple[int, int, int]:
    candidates = _select_note_candidates(cutoff=cutoff, batch_size=batch_size)
    eligible = len(candidates)
    purged = 0
    failed = 0

    for note_id, owner_id in candidates:
        try:
            with transaction.atomic():
                # explicit User -> Note lock
                # order, never a single joined `SELECT ... FOR UPDATE`. The
                # owning User is locked and its pending-deletion state
                # re-checked first; a candidate whose owner has since
                # scheduled account deletion is skipped without ever
                # locking the Note row. This serializes naturally against
                # `accounts.services.schedule_user_deletion()`/
                # `cancel_user_deletion()`, which lock the same User row
                # under the same order -- whichever transaction commits
                # first determines what the other observes.
                owner = _lock_user_for_purge(owner_id=owner_id)
                if owner is None or owner.deletion_scheduled_at is not None:
                    continue
                note = _lock_note_for_purge(note_id=note_id)
                if note is None:
                    continue
                if note.owner_id != owner_id:
                    continue
                if not _is_purge_eligible(trashed_at=note.trashed_at, cutoff=cutoff):
                    continue
                if dry_run:
                    continue
                note.delete()
                purged += 1
        except Exception:
            failed += 1
            logger.error("Note purge candidate failed unexpectedly.", exc_info=True)

    return eligible, purged, failed


def _folder_is_referenced(*, folder_id: int) -> bool:
    return Note.objects.filter(folder_id=folder_id).exists()


def _select_folder_candidates(*, cutoff: datetime, batch_size: int) -> list[tuple[int, int]]:
    # See `_select_note_candidates()`'s matching comment -- same coarse
    # prefilter, same reason `owner_id` is carried alongside `id`.
    return list(
        Folder.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff)
        .filter(owner__deletion_scheduled_at__isnull=True)
        .order_by("trashed_at", "id")[:batch_size]
        .values_list("id", "owner_id")
    )


def _purge_eligible_folders(
    *, cutoff: datetime, batch_size: int, dry_run: bool
) -> tuple[int, int, int, int]:
    candidates = _select_folder_candidates(cutoff=cutoff, batch_size=batch_size)
    eligible = len(candidates)
    purged = 0
    blocked = 0
    failed = 0

    for folder_id, owner_id in candidates:
        try:
            with transaction.atomic():
                # same explicit User ->
                # Folder lock order and reasoning as
                # `_purge_eligible_notes()` above.
                owner = _lock_user_for_purge(owner_id=owner_id)
                if owner is None or owner.deletion_scheduled_at is not None:
                    continue
                try:
                    folder = Folder.objects.select_for_update().get(pk=folder_id)
                except Folder.DoesNotExist:
                    continue
                if folder.owner_id != owner_id:
                    continue
                if not _is_purge_eligible(trashed_at=folder.trashed_at, cutoff=cutoff):
                    continue
                if _folder_is_referenced(folder_id=folder.pk):
                    blocked += 1
                    continue
                if dry_run:
                    continue
                folder.delete()
                purged += 1
        except Exception:
            failed += 1
            logger.error("Folder purge candidate failed unexpectedly.", exc_info=True)

    return eligible, purged, blocked, failed


class PurgeAuditWriteError(Exception):
    """Raised when the aggregate audit write fails after row purges already
    committed independently. `result` carries the already-computed row
    counts so the caller can still report an accurate summary."""

    def __init__(self, *, result: PurgeCycleResult):
        super().__init__("Failed to record the aggregate purge audit event.")
        self.result = result


def run_purge_cycle(
    *, dry_run: bool, note_batch_size: int, folder_batch_size: int
) -> PurgeCycleResult:
    run_id = str(uuid.uuid4())
    cutoff = timezone.now() - TRASH_RECOVERABLE_MAX_AGE
    started_at = time.monotonic()

    logger.info("Purge cycle starting. run_id=%s dry_run=%s", run_id, dry_run)
    logger.info("Purge cycle cutoff=%s run_id=%s", cutoff.isoformat(), run_id)

    lock_acquired = _try_acquire_purge_lock()
    if not lock_acquired:
        logger.info("Purge cycle skipped: advisory lock held elsewhere. run_id=%s", run_id)
        duration_seconds = time.monotonic() - started_at
        return PurgeCycleResult(
            run_id=run_id,
            cutoff=cutoff,
            dry_run=dry_run,
            lock_acquired=False,
            notes_eligible=0,
            notes_purged=0,
            notes_failed=0,
            folders_eligible=0,
            folders_purged=0,
            folders_blocked=0,
            folders_failed=0,
            duration_seconds=duration_seconds,
        )

    try:
        notes_eligible, notes_purged, notes_failed = _purge_eligible_notes(
            cutoff=cutoff, batch_size=note_batch_size, dry_run=dry_run
        )
        folders_eligible, folders_purged, folders_blocked, folders_failed = _purge_eligible_folders(
            cutoff=cutoff, batch_size=folder_batch_size, dry_run=dry_run
        )

        duration_seconds = time.monotonic() - started_at

        logger.info(
            "Purge cycle row work complete. run_id=%s notes_eligible=%s notes_purged=%s "
            "notes_failed=%s folders_eligible=%s folders_purged=%s folders_blocked=%s "
            "folders_failed=%s",
            run_id,
            notes_eligible,
            notes_purged,
            notes_failed,
            folders_eligible,
            folders_purged,
            folders_blocked,
            folders_failed,
        )

        result = PurgeCycleResult(
            run_id=run_id,
            cutoff=cutoff,
            dry_run=dry_run,
            lock_acquired=True,
            notes_eligible=notes_eligible,
            notes_purged=notes_purged,
            notes_failed=notes_failed,
            folders_eligible=folders_eligible,
            folders_purged=folders_purged,
            folders_blocked=folders_blocked,
            folders_failed=folders_failed,
            duration_seconds=duration_seconds,
        )

        if not dry_run:
            try:
                with transaction.atomic():
                    account_services.record_audit_event(
                        AuditEvent.EVENT_TRASH_PURGE_COMPLETED,
                        actor=None,
                        target_user=None,
                        source=AuditEvent.SOURCE_MANAGEMENT_COMMAND,
                        details={
                            "run_id": run_id,
                            "cutoff": cutoff.isoformat(),
                            "notes_purged": notes_purged,
                            "notes_failed": notes_failed,
                            "folders_purged": folders_purged,
                            "folders_blocked": folders_blocked,
                            "folders_failed": folders_failed,
                            "duration_seconds": duration_seconds,
                        },
                    )
            except Exception:
                logger.critical(
                    "Purge cycle audit write failed after row purges already committed. run_id=%s",
                    run_id,
                    exc_info=True,
                )
                raise PurgeAuditWriteError(result=result) from None

        logger.info(
            "Purge cycle complete. run_id=%s duration_seconds=%.3f", run_id, duration_seconds
        )

        return result
    finally:
        _release_purge_lock()
