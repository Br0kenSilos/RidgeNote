# RidgeNote Purge Operations Runbook

This runbook explains how to operate RidgeNote's Trash retention and
final-purge features as a self-hosted operator. It assumes you can run
Docker Compose commands, edit an environment file, and read container
logs. It does not assume any familiarity with RidgeNote's internal
development history.

It covers two related capabilities that ship together in this version of
RidgeNote:

- **Manual one-shot purge** — an operator-run command that permanently
  deletes eligible Trash content on demand.
- **Automatic purge scheduling** — an optional, disabled-by-default
  background job that runs the same purge logic on a timer.

Both use the exact same underlying purge logic. Nothing described here
changes based on which one you use.

## 1. Purpose and lifecycle overview

RidgeNote's Trash has three time-based stages, measured from the moment
an item (a note or a folder) is trashed:

| Stage | Age | Who can recover it |
|---|---|---|
| Owner-visible Trash | 0–30 days | The owner, from the Trash page |
| Administrator-only recovery | 31–90 days | An administrator only, metadata-level access |
| Final-purge eligible | strictly older than 90 days | Nobody — permanently deleted |

A few precise details matter operationally:

- **Day 90 itself is still administrator-recoverable.** An item becomes
  purge-eligible only once it is *strictly older* than 90 days
  (`trashed_at < now - 90 days`). There is no gap and no overlap between
  the recovery window and purge eligibility.
- **`emptied_at` does not accelerate final purge.** Emptying Trash moves
  an item into the administrator-only recovery stage early, but the
  90-day final-purge clock is always measured from the original
  `trashed_at` timestamp, never from `emptied_at`.
- **Purge is irreversible.** Once an eligible note or folder is purged,
  RidgeNote itself has no way to bring it back. The only way to recover
  purged content is from an infrastructure-level backup containing the
  database state -- for example a PostgreSQL logical backup, or a
  host/VM/container-level backup -- taken before the purge ran (see
  [Section 17](#17-backup-dependency)).

This runbook is operational, not a design document — it tells you what
to run and what to expect, not why the feature was built this way.

## 2. Safety model

Final purge follows a conservative, data-preserving safety model:

- **Notes purge before folders**, in every cycle, always in that order.
- **A folder is purged only when zero `Note` rows reference it**, in any
  lifecycle state whatsoever (active, owner-visible trashed,
  administrator-recoverable, independently trashed, or itself
  purge-eligible but not yet purged).
- **Purge never moves, unfiles, reassigns, or otherwise mutates a
  referencing note** to force a folder through. If a folder still has
  any note pointing at it, that folder is simply left alone and reported
  as **blocked** — never forced, never worked around.
- A **temporarily** blocked folder is normal and expected — for example,
  a younger note that itself isn't purge-eligible yet is still
  legitimately referencing that folder. It resolves on its own once that
  note is restored elsewhere or later becomes eligible and is purged
  itself.
- A folder that stays blocked across many consecutive cycles is an
  operational anomaly worth investigating (see
  [Section 12](#12-blocked-folders)) — not a bug in purge itself.
- **`Tag` rows always survive note purge.** Deleting a note only removes
  that note's own association with its tags; the `Tag` objects
  themselves are never deleted by purge, even if no note uses them
  afterward.

## 3. Manual dry-run

Run a dry-run before enabling automatic purge, and before any manual live
purge, so you know what will happen before anything is deleted:

```sh
docker compose run --rm web python manage.py purge_expired_trash --dry-run
```

Optional batch-size overrides are available:

```sh
docker compose run --rm web python manage.py purge_expired_trash --dry-run --note-batch-size 500 --folder-batch-size 500
```

- Both `--note-batch-size` and `--folder-batch-size` default to **200**
  if omitted.
- Both must be positive integers; a zero, negative, or non-numeric value
  is rejected before anything runs.
- Dry-run performs **zero mutation** — nothing is deleted, nothing is
  changed.
- Dry-run creates **no audit event**.
- Dry-run output is a **point-in-time snapshot**. A later live run may
  purge a different (usually larger) set of rows, since more items may
  have crossed the 90-day boundary, or a restore may have happened, in
  the meantime.

## 4. Manual live execution

```sh
docker compose run --rm web python manage.py purge_expired_trash --execute
```

- Exactly one of `--dry-run` or `--execute` is required. Running the
  command with neither flag (or both) fails immediately with a usage
  error — it can never accidentally purge anything by omission.
- Anything this command purges is **permanently and irreversibly
  deleted**. There is no undo.
- Each invocation processes **one bounded batch** of notes and one
  bounded batch of folders (200 of each by default). If your Trash
  backlog is larger than that, a single invocation will not clear it all.
- It is always safe to **run the command again**. Each run only ever
  considers currently eligible rows under a fresh lock and a fresh
  eligibility check, so repeating it simply continues processing any
  remaining backlog — nothing is double-counted or double-deleted.

## 5. Automatic scheduler enablement

RidgeNote's `scheduler` Compose service can run the same purge logic
automatically on a timer. It is **disabled by default**. Set these two
values in your environment file (`.env`, alongside RidgeNote's other
`RIDGENOTE_*` settings):

```env
RIDGENOTE_PURGE_ENABLED=true
RIDGENOTE_PURGE_INTERVAL_SECONDS=86400
```

Exact behavior:

- If `RIDGENOTE_PURGE_ENABLED` is **absent entirely**, automatic purge is
  **disabled**. This is the default for every existing deployment that
  does not add this variable.
- Only the literal, case-insensitive values `true` or `false` are valid.
  Surrounding whitespace around a real value is tolerated (`" true "` is
  accepted), but an **explicitly blank value is invalid**, and so is
  anything else (`1`, `0`, `yes`, `no`, `on`, `off`, or any other text).
  See [Section 8](#8-configuration-validation) for what happens if you
  get this wrong.
- `RIDGENOTE_PURGE_INTERVAL_SECONDS` must be a positive integer if set at
  all. If absent, it defaults to **86400 seconds (24 hours)**.
- When enabled, the scheduler runs **one live purge cycle immediately**
  on startup, then **exactly one more live cycle per configured
  interval**, indefinitely, until it is stopped.
- A container restart while enabled will trigger another **immediate**
  cycle. This is intentional and safe — see
  [Section 14](#14-graceful-shutdown-and-restart).
- The scheduler **never** runs a dry-run automatically — every automatic
  cycle is a live, real purge cycle. If you want a preview first, run the
  manual dry-run command yourself before enabling.
- The scheduler **never** drains more than one cycle's worth of backlog
  per interval, even if a large backlog remains after one cycle — it
  simply continues on the next scheduled interval.

## 6. Enabling procedure

Follow these steps in order:

1. **Run a manual dry-run** (see [Section 3](#3-manual-dry-run)) and
   confirm the reported counts look reasonable for your deployment.
2. **Review the counts** — how many notes/folders are eligible, and
   whether any folders are already reported as blocked.
3. **Set valid environment values** in your `.env` file:
   ```env
   RIDGENOTE_PURGE_ENABLED=true
   RIDGENOTE_PURGE_INTERVAL_SECONDS=86400
   ```
4. **Recreate the scheduler service** so it picks up the new environment
   values:
   ```sh
   docker compose up -d --no-deps --force-recreate scheduler
   ```
5. **Inspect the scheduler logs**:
   ```sh
   docker compose logs --no-color --tail=80 scheduler
   ```
   Confirm it reports automatic purge as enabled and shows a purge cycle
   starting (see [Section 9](#9-expected-logs) for what this looks like).
6. **Verify the first aggregate purge audit event** was recorded (see
   [Section 10](#10-aggregate-audit-event)).
7. **Confirm the expected rows were purged and any blocked rows remained
   untouched**, matching what the dry-run in step 1 predicted (allowing
   for any Trash activity that happened in between).

## 7. Disabling procedure

```env
RIDGENOTE_PURGE_ENABLED=false
```

Then recreate the scheduler service the same way:

```sh
docker compose up -d --no-deps --force-recreate scheduler
```

- Disabling prevents **future** automatic cycles from running. It cannot
  undo any purge that already completed.
- The manual `purge_expired_trash` command remains available at any time,
  regardless of whether automatic purge is enabled or disabled.

## 8. Configuration validation

| `RIDGENOTE_PURGE_ENABLED` value | Result |
|---|---|
| absent (not set at all) | Disabled — scheduler idles, no purge runs |
| `false` (any case, e.g. `FALSE`) | Disabled — scheduler idles, no purge runs |
| `true` (any case, e.g. `TRUE`) | Enabled — automatic purge runs on schedule |
| explicitly blank (`RIDGENOTE_PURGE_ENABLED=`) | **Invalid** — scheduler fails to start |
| anything else (`1`, `yes`, `on`, typos, etc.) | **Invalid** — scheduler fails to start |

| `RIDGENOTE_PURGE_INTERVAL_SECONDS` value | Result |
|---|---|
| absent | Defaults to `86400` |
| a positive integer (e.g. `3600`) | Used as given |
| zero, negative, blank, or non-numeric | **Invalid** — scheduler fails to start |

**Important:** an invalid value in either of these two variables only
affects the `scheduler` service. It causes the `scheduler` container to
exit with an error immediately at startup (and, under the default
`restart: unless-stopped` policy, to keep restarting and failing in a
loop until you fix the value). The `web` service is completely
unaffected and remains healthy — a scheduler-only configuration mistake
never takes your application down.

To check what's happening:

```sh
docker compose ps scheduler
docker compose logs --no-color --tail=50 scheduler
```

A repeatedly restarting `scheduler` container with a `CommandError`
naming `RIDGENOTE_PURGE_ENABLED` or `RIDGENOTE_PURGE_INTERVAL_SECONDS` in
the logs means one of these two values is invalid — fix it in `.env` and
recreate the service again.

## 9. Expected logs

These are representative, paraphrased examples of what you'll see in
`docker compose logs scheduler` — not literal output, and never
containing real note/folder/owner identifying information.

**Scheduler disabled:**
```
Purge scheduler started. purge_enabled=False
Automatic purge is disabled (RIDGENOTE_PURGE_ENABLED is not true).
```

**Scheduler enabled and starting:**
```
Purge scheduler started. purge_enabled=True
Automatic purge is enabled. interval_seconds=86400
```

**A successful purge cycle:**
```
Purge cycle starting. run_id=<uuid> dry_run=False
Purge cycle cutoff=<timestamp> run_id=<uuid>
Purge cycle row work complete. run_id=<uuid> notes_eligible=N notes_purged=N notes_failed=0 folders_eligible=N folders_purged=N folders_blocked=0 folders_failed=0
Purge cycle complete. run_id=<uuid> duration_seconds=0.05
```

**An advisory-lock skip** (another purge cycle — manual or automatic —
was already running):
```
Purge cycle skipped: advisory lock held elsewhere. run_id=<uuid>
```

**Blocked folders present:**
```
Scheduled purge cycle completed with blocked folders. folders_blocked=N
```

**Isolated row failures** (some rows failed but the cycle still
completed):
```
Scheduled purge cycle completed with isolated row failures. notes_failed=N folders_failed=N
```

**An aggregate audit-write failure:**
```
Scheduled purge cycle's aggregate audit write failed; any row-level purges that already committed remain committed.
```

**A database connection failure:**
```
Scheduled purge cycle failed due to a database error.
```

**Graceful shutdown:**
```
Purge scheduler received signal 15; shutting down after any in-progress cycle.
Purge scheduler stopped cleanly.
```

Every line above is aggregate-only: run IDs, timestamps, and counts.
**None of RidgeNote's purge logging ever includes a note ID, folder ID,
owner/username, title, folder name, path, or content of any kind.** If
you ever see such information in purge-related logs, treat it as a bug
worth reporting, not expected behavior.

## 10. Aggregate audit event

Every completed **live** purge cycle (manual `--execute` or automatic)
records exactly one audit event:

- **Event type:** `trash_purge_completed`
- **Actor:** none — this is a system-triggered action, not tied to any
  administrator account
- **Source:** recorded as a management-command action
- **One event per completed live cycle** — never one per item, per
  folder, or per owner
- **No event at all** for a dry-run
- **No event at all** when a cycle is skipped due to lock contention

The event's details contain only aggregate fields: the cutoff timestamp
used for that cycle, the number of notes/folders purged, the number of
row failures, the number of blocked folders, the cycle duration, and a
non-identifying run ID. **It never contains a note ID, folder ID, owner
ID, username, title, folder name, path, or any content.**

To inspect the most recent purge audit event, use the Django shell (the
same general approach used elsewhere for ad hoc inspection in this
project — there is no dedicated admin UI for this):

```sh
docker compose exec web python manage.py shell -c "
from accounts.models import AuditEvent
event = AuditEvent.objects.filter(event_type=AuditEvent.EVENT_TRASH_PURGE_COMPLETED).order_by('-id').first()
print(event.actor_id, event.target_user_id, event.source, event.details)
"
```

You should see `None None management_command {...}` followed by a
dictionary of aggregate fields only.

## 11. Lock contention

Manual and automatic purge share **one** PostgreSQL session-level
advisory lock — there is no separate lock for each. Only one purge cycle
can ever run at a time, however it was triggered:

- If you run a manual `--execute` while an automatic cycle happens to be
  running (or vice versa), whichever one started first keeps running and
  the other **safely skips** — you'll see the "advisory lock held
  elsewhere" log line and, for the manual command, an on-screen message
  saying another cycle already holds the lock.
- **This is not an error.** It means no work was lost — the skipped
  invocation simply did nothing that cycle; nothing was left half-done.
- PostgreSQL automatically releases this lock as soon as the
  connection/process that held it ends, whether that cycle finished
  normally, failed, or the process was killed outright.
- **No manual lock cleanup should ever normally be required.** If you
  suspect a stuck lock, a normal `docker compose restart scheduler` (or
  simply waiting for any manual invocation to exit) is sufficient — the
  lock cannot outlive the connection that held it.

## 12. Blocked folders

A folder is blocked, not purged, whenever **any** note still references
it — regardless of that note's own lifecycle state (active, still in
owner-visible Trash, in administrator-only recovery, or independently
trashed). This is normal, reported, expected behavior, not a failure.

Common, legitimate causes:

- A younger trashed note in that folder hasn't reached its own 90-day
  boundary yet.
- A note was restored (by the owner or an administrator) back into that
  folder after it was trashed.

**Persistent blocking** — the same folder reported as blocked across many
consecutive purge cycles over an extended period — is unusual and worth
investigating.

To investigate safely, without exposing note content unnecessarily, use
the Django shell to check only counts and IDs — never titles or bodies:

```sh
docker compose exec web python manage.py shell -c "
from notes.models import Note
folder_id = 123  # the blocked folder's ID from your own records
print(Note.objects.filter(folder_id=folder_id).count())
"
```

If that count is unexpectedly nonzero for a folder you believed should be
long-emptied, review which notes reference it (again, by ID/lifecycle
state, not by pulling their content into a shared log or ticket).

**Never mutate a note (reassign its folder, delete it, or otherwise
change it) merely to force a blocked folder to purge.** That defeats the
entire point of the safety model in [Section 2](#2-safety-model) — a
persistently blocked folder means real data still legitimately depends on
it, and that dependency needs to be resolved on its own terms (typically
by the note being restored elsewhere, deleted intentionally by its owner,
or itself becoming purge-eligible), not worked around from the operator
side.

## 13. Failure handling

### Isolated row failure

If an individual note or folder fails to purge for an unexpected reason,
the rest of that cycle's batch continues normally. The failed row simply
remains eligible and will be automatically retried on a later cycle —
manual or automatic. Inspect the logs for the exception detail (type and
traceback only, never content). **Never manually delete database rows**
to work around a failure.

### Aggregate audit-write failure

If the final aggregate audit event fails to write after a live cycle's
row deletions already happened, **do not assume the purge itself was
rolled back — it was not.** Row-level database state is always the
authoritative record of what was actually purged, independent of whether
the audit summary of that cycle was successfully recorded. Inspect the
logs and the audit event table (a missing event for a cycle you know ran
is the symptom), and run a manual dry-run afterward to see current
eligible counts before taking any further action.

### Database unavailable

If PostgreSQL is temporarily unreachable during a scheduled cycle, the
scheduler process itself stays alive and simply retries at its next
configured interval — it does not crash or need manual restart for this
alone. Verify PostgreSQL's own health and connectivity:

```sh
docker compose ps postgres
```

### Repeated failures

If purge cycles keep failing repeatedly:

1. Disable automatic purge (see [Section 7](#7-disabling-procedure)) if
   you want to stop further automatic attempts while investigating.
2. Preserve the relevant logs.
3. Run a manual dry-run to see the current eligible/blocked state without
   risking further mutation.
4. Investigate the underlying cause (commonly a database connectivity or
   resource issue) before re-enabling automatic purge.

There is no built-in automatic retry backoff or alerting — repeated
failures simply repeat at the normal configured interval until the
underlying problem is fixed.

## 14. Graceful shutdown and restart

- Both `SIGTERM` and `SIGINT` (sent by `docker compose stop`) request a
  clean shutdown.
- Any **already-running** purge cycle is always allowed to finish
  naturally — it is never interrupted mid-cycle.
- Once shutdown has been requested, **no new cycle will start**, even if
  the configured interval elapses during shutdown.
- Use the normal Docker Compose stop/restart commands:
  ```sh
  docker compose stop scheduler
  docker compose up -d scheduler
  ```
- A restart while automatic purge is enabled will trigger another
  **immediate** cycle on startup. This is intentional, safe, and
  idempotent — purge logic always revalidates eligibility fresh under
  lock, so an extra cycle after a restart simply finds whatever is
  currently eligible (often nothing new) and completes quickly.

## 15. Verification checklist

Use this after any configuration change:

- [ ] `docker compose ps scheduler` shows the container running (not
      restarting)
- [ ] Scheduler logs show the enablement state you expect
      (`purge_enabled=True` or `purge_enabled=False`)
- [ ] A manual dry-run completes and reports plausible counts
- [ ] A live cycle (manual or automatic) produces exactly one aggregate
      `trash_purge_completed` audit event
- [ ] Notes/folders older than 90 days that were eligible are gone
- [ ] Notes/folders exactly at 90 days or younger are still present
- [ ] Folders with any referencing note remain present and are reported
      as blocked, not purged
- [ ] `Tag` rows remain present after a note purge
- [ ] No note/folder/owner identity, title, name, path, or content
      appears anywhere in the logs or the audit event's `details`
- [ ] With automatic purge disabled, no purge cycle runs at all

## 16. Rollback and emergency stop

To stop automatic purge immediately:

```env
RIDGENOTE_PURGE_ENABLED=false
```

```sh
docker compose up -d --no-deps --force-recreate scheduler
```

Then confirm the scheduler logs show it idling with no further purge
activity.

**This stops future automatic cycles only.** Neither this nor any other
application-level action can undo a purge that already completed —
RidgeNote has no application-level rollback for final purge. RidgeNote's
built-in recovery mechanisms (Library Backup/restore, Trash restore, and
administrator recovery) all operate on data that has not yet been
finally purged; none of them can bring back a note or folder after this
step. If content must be recovered after an unwanted purge, the only
path is restoring from an infrastructure-level backup containing the
database state taken before that purge ran (see
[Section 17](#17-backup-dependency)) — that backup is the operator's
own responsibility, not something RidgeNote manages automatically.

## 17. Backup dependency

**Final purge is permanent and irreversible by design.** RidgeNote's
application logic provides no way to recover a note or folder once it has
been purged. Operating this feature responsibly requires your own
infrastructure-level backup strategy covering the database state (for
example, routine PostgreSQL `pg_dump` snapshots, or a host/VM/
container-level backup of the `postgres-data` volume), taken
independently of RidgeNote itself.

RidgeNote is not a database administration product, and does not
automatically manage whole-instance or database backups. Whole-instance
protection — including recovery from an unwanted final purge — remains
the operator's own responsibility, at the host/VM/container/
infrastructure level appropriate to your environment.
[`docs/RUNBOOK_BACKUP_RESTORE.md`](RUNBOOK_BACKUP_RESTORE.md) documents
PostgreSQL-level logical backup and restore procedures you can use for
this. That documentation is advanced operator guidance, not a
maintainer-supported recovery service — it is not a promise of support
for database corruption or other arbitrary PostgreSQL failures. Do not
treat anything in this runbook as a substitute for having your own
tested backup and restore procedure in place before enabling automatic
purge in a deployment that holds data you cannot afford to lose.

## 18. Fresh deployment defaults

For a brand-new RidgeNote deployment, or any existing deployment that
simply doesn't set the two purge-related variables:

- Automatic purge is **disabled** by default — `RIDGENOTE_PURGE_ENABLED`
  is absent, so the scheduler idles and performs no purge work.
- No scheduler-triggered purge cycle will ever run until you explicitly
  set `RIDGENOTE_PURGE_ENABLED=true`.
- The manual dry-run command is available immediately after deployment,
  with no configuration required, so you can inspect what would be
  eligible at any time.
- No database migration is specific to the purge scheduler — nothing
  about enabling or disabling this feature requires running migrations.
- Every existing installation that upgrades to this version without
  adding these two environment variables continues to behave exactly as
  before: no automatic purge activity of any kind.
