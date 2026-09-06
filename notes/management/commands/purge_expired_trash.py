from django.core.management.base import BaseCommand, CommandError

from notes.purge import PurgeAuditWriteError, PurgeCycleResult, run_purge_cycle

DEFAULT_BATCH_SIZE = 200


def _validate_positive_int(*, name: str, value: int) -> int:
    if value <= 0:
        raise CommandError(f"{name} must be greater than zero.")
    return value


class Command(BaseCommand):
    help = (
        "Permanently purge notes and folders that have passed the 90-day "
        "administrator-recoverable boundary. Requires exactly one of "
        "--dry-run or --execute."
    )

    def add_arguments(self, parser):
        mode_group = parser.add_mutually_exclusive_group(required=True)
        mode_group.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be purged without deleting anything.",
        )
        mode_group.add_argument(
            "--execute",
            action="store_true",
            help="Permanently delete eligible notes and folders.",
        )
        parser.add_argument(
            "--note-batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help="Maximum number of notes to process in this invocation (default: 200).",
        )
        parser.add_argument(
            "--folder-batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help="Maximum number of folders to process in this invocation (default: 200).",
        )

    def _print_summary(self, *, result: PurgeCycleResult) -> None:
        if not result.lock_acquired:
            self.stdout.write("Skipped: another purge cycle already holds the advisory lock.")
            return

        self.stdout.write(f"run_id: {result.run_id}")
        self.stdout.write(f"cutoff: {result.cutoff.isoformat()}")
        self.stdout.write(
            f"notes: eligible={result.notes_eligible} "
            f"purged={result.notes_purged} failed={result.notes_failed}"
        )
        self.stdout.write(
            f"folders: eligible={result.folders_eligible} "
            f"purged={result.folders_purged} blocked={result.folders_blocked} "
            f"failed={result.folders_failed}"
        )
        self.stdout.write(f"duration_seconds: {result.duration_seconds:.3f}")
        if result.dry_run:
            self.stdout.write(
                "This is a point-in-time dry-run result. A later --execute run "
                "may see a different outcome due to concurrent restores or "
                "other Trash activity in the meantime."
            )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        note_batch_size = _validate_positive_int(
            name="--note-batch-size", value=options["note_batch_size"]
        )
        folder_batch_size = _validate_positive_int(
            name="--folder-batch-size", value=options["folder_batch_size"]
        )

        try:
            result = run_purge_cycle(
                dry_run=dry_run,
                note_batch_size=note_batch_size,
                folder_batch_size=folder_batch_size,
            )
        except PurgeAuditWriteError as exc:
            self._print_summary(result=exc.result)
            raise CommandError(
                "The aggregate purge audit event could not be recorded. Row-level "
                "purges that already committed remain committed."
            ) from exc

        self._print_summary(result=result)
        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
        else:
            self.stdout.write(self.style.SUCCESS("Purge cycle complete."))
