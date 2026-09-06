# RidgeNote Backup and Restore Runbook

This runbook explains how to back up and restore RidgeNote's PostgreSQL
database as a self-hosted operator. It assumes you can run Docker Compose
commands, edit an environment file, and read container logs. It does not
assume any familiarity with RidgeNote's internal development history or
with Django.

The core backup, validation, and disposable-restore-rehearsal commands
in this runbook (Sections 4-9) have been live-verified against a real
RidgeNote Docker Compose environment, using disposable resources only.
No command here was ever run against, nor at any point overwrote,
dropped, or reset, the live application database. The advanced,
destructive existing-database-replacement procedure (Section 10) and
the new-host disaster-recovery sequence (Section 11) are documented,
not independently rehearsed end to end -- see those sections for their
own scope notes.

**This is advanced, PostgreSQL-level operator guidance, distinct from
RidgeNote's built-in recovery features.** RidgeNote's in-app **Library
Backup** (a per-user, library-scoped export/import tool), Trash restore,
and administrator recovery are separate mechanisms, documented
elsewhere, that require no PostgreSQL knowledge. This runbook instead
covers whole-database backup and restore at the PostgreSQL level — the
operator's own responsibility, not something RidgeNote manages
automatically. The commands here are documented and were verified as
described above; their presence is not a promise of maintainer support
for database corruption or other arbitrary PostgreSQL failures, and
RidgeNote is not a database administration product.

## 1. Purpose and scope

This runbook covers:

- **Logical backup** of the RidgeNote PostgreSQL database using
  `pg_dump` in custom format.
- **Backup validation**, so you know a backup is trustworthy before you
  ever need it.
- **Restore**, including a disposable rehearsal restore you can safely
  practice at any time, a fresh-deployment restore, and a strongly
  advisory-only procedure for replacing an existing database.
- **Restore safety**, in particular why automatic Trash purge must stay
  disabled until you have reviewed restored data.

This runbook does **not** cover: backup automation or scheduling (you are
responsible for running these commands, or your own external scheduling
of them); encryption or off-host synchronization tooling (use your own);
PostgreSQL bind-mount or physical-volume conversion; or any change to
RidgeNote's application code, tests, or migrations. See
[Section 15](#15-retention-guidance) for a minimal, advisory starting
point on how often to do this.

## 2. Current data inventory

In this version of RidgeNote:

- **PostgreSQL is the sole authoritative store for application data.**
  Every user, note, folder, tag, and audit event lives in the `postgres`
  Compose service's database. In the standalone deployment bundle
  (`deploy/docker-compose.yml`), that data lives in the host bind mount
  `data/postgres`; in local development (the root `docker-compose.yml`),
  it lives in the named Docker volume `postgres-data`. The commands in
  this runbook work identically either way, since `pg_dump`/`pg_restore`
  operate through PostgreSQL itself, not the underlying storage
  mechanism.
- There are **no uploaded files, attachments, or media directories**
  anywhere in RidgeNote. A database backup is a complete backup of all
  user-generated content.
- Generated note JSON exports and collected static assets are fully
  reproducible from the database and the application image. They are not
  backup-worthy data in their own right.
- Application/container logs are optional operational evidence, not
  recovery data.
- The `scheduler` service holds no persistent state between purge cycles;
  there is nothing scheduler-specific to back up.
- Application configuration and secrets (your `.env` file) are **not**
  stored in the database and must be preserved **separately** — see
  [Section 13](#13-secrets-and-configuration).

## 3. Prerequisites

- A running RidgeNote Docker Compose deployment, with the `postgres`
  service healthy (`docker compose ps postgres`).
- Enough free host disk space to hold at least one dump (typically a
  small multiple of your live database's data size — inspect your own
  deployment's actual data volume to judge this).
- Shell access to run `docker compose` commands on the host.

No special client tooling needs to be installed on the host. Every
command in this runbook runs PostgreSQL's own `pg_dump`/`pg_restore`/
`psql` binaries **inside** the existing `postgres` container, so tool
versions always match the server exactly.

## 4. Logical backup procedure

Run each step in order. **Check the exit status of each command before
running the next one.** Never chain these together in a way that would
run a later step after an earlier one failed.

**Step 1 — create the dump inside the container:**

```sh
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/ridgenote-backup.dump'
```

Using the container's own `$POSTGRES_USER`/`$POSTGRES_DB` environment
variables (rather than expanding them in your host shell) means this
exact command works unmodified regardless of your own `.env` values, and
never exposes a password on the command line. Confirm this command exits
`0` before proceeding. `-Fc` produces PostgreSQL's custom archive format:
compressed, restorable selectively, and inspectable with `pg_restore
--list` (see [Section 6](#6-backup-validation)).

**Step 2 — copy the completed dump to the host:**

```sh
docker compose cp \
  postgres:/tmp/ridgenote-backup.dump \
  "./ridgenote-backup-$(date +%Y%m%d-%H%M%S).dump"
```

Confirm this command exits `0` **and** that the resulting file exists on
the host and is nonempty before proceeding to step 3:

```sh
ls -la ./ridgenote-backup-*.dump
```

**Step 3 — apply restrictive permissions to the host copy:**

```sh
chmod 600 ./ridgenote-backup-*.dump
```

A database dump is exactly as sensitive as the live database — see
[Section 14](#14-privacy-and-security).

**Step 4 — remove the temporary in-container file, only after step 2 has
been confirmed successful:**

```sh
docker compose exec -T postgres rm -f /tmp/ridgenote-backup.dump
```

**Never run step 4 if step 2 failed or you have not confirmed the host
file exists.** If the host copy failed, the in-container dump is your
only copy — leave it in place and retry the copy instead of deleting it.

This exact four-step sequence was live-verified end to end against a real
deployment: each step returned exit status `0`, the host file appeared
with nonzero size, and the in-container temporary file was confirmed
absent afterward.

## 5. Backup metadata and version capture

A dump file's name alone does not tell you what RidgeNote version or
database state it represents. Alongside every backup, record (as a small
adjacent text file, or your own operator log — RidgeNote does not invent
or require a release-tagging system):

```sh
# Exact RidgeNote source commit this backup corresponds to
git rev-parse HEAD

# PostgreSQL server version
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version();"'

# Backup timestamp and timezone (UTC recommended)
date -u +"%Y-%m-%dT%H:%M:%SZ"

# Deployment hostname/identity
hostname

# Whether automatic purge was enabled at backup time
docker compose exec -T scheduler sh -c 'echo "RIDGENOTE_PURGE_ENABLED=$RIDGENOTE_PURGE_ENABLED"'
```

Record the Git commit SHA specifically because it identifies the exact
application code that produced this backup, which is more precise than
a release version alone when tracking compatibility across upgrades.
Record whether purge was enabled because it tells you, later, whether
this backup could already contain partially-purged Trash state.

## 6. Backup validation

**A successful `pg_dump` exit status alone does not prove a backup is
usable.** At minimum, confirm all of:

- exit status `0` from `pg_dump`;
- the host file exists and is nonempty (`ls -la`);
- the file size is plausible for your deployment (a dump that is
  suspiciously tiny relative to your usual data volume is worth
  investigating before you trust it);
- a successful archive listing via `pg_restore --list` (below);
- periodic successful disposable restore rehearsal — this runbook's
  authoring performed one such rehearsal; repeating it periodically going
  forward is your own ongoing responsibility, not something RidgeNote
  automates.

To run `pg_restore --list`, use the PostgreSQL container's own tooling —
never a host-installed `pg_restore`, which may not exist on your host or
may be a different major version than your server. The verified
mechanism is: copy the host dump back into the container, then list it
from there:

```sh
docker compose cp ./ridgenote-backup-YYYYMMDD-HHMMSS.dump postgres:/tmp/ridgenote-backup-verify.dump
docker compose exec -T postgres pg_restore --list /tmp/ridgenote-backup-verify.dump
docker compose exec -T postgres rm -f /tmp/ridgenote-backup-verify.dump
```

A valid archive's listing begins with a header block reporting the
dump's creation time, source database name, PostgreSQL dump/server
version, and total TOC (table-of-contents) entry count, followed by one
line per database object. Confirm the listing command exits `0` and
produces this kind of output. This exact sequence was live-verified:
`pg_restore --list` exited `0` and reported a well-formed header (correct
`ridgenote` database name, matching PostgreSQL 17.10 version information,
and a plausible TOC entry count for this deployment's schema).

## 7. Restore safety and scheduler/purge controls

Before performing any restore — rehearsal or otherwise — these rules are
**mandatory, not optional**:

- **Do not start the `scheduler` service** during restore preparation or
  validation.
- **Set `RIDGENOTE_PURGE_ENABLED=false` explicitly**, regardless of its
  value before the restore.
- **Never run `purge_expired_trash --execute`** during a recovery window.
  A dry-run (`--dry-run`) may be used afterward, only to inspect what
  would currently be eligible — never to actually purge anything as part
  of recovery.
- **Keep automatic purge disabled** until you have personally inspected
  and confirmed the restored data is correct.

This matters because an old restored database can genuinely contain
Trash items that are, by their own recorded timestamps, already older
than the 90-day purge-eligibility boundary described in
[the purge operations runbook](RUNBOOK_PURGE.md). If automatic purge were
enabled immediately after such a restore, the very first scheduler cycle
could permanently purge newly-restored data before you ever had a chance
to review it. This is a real risk, not a hypothetical one. Re-enabling
automatic purge after a restore is always a separate, deliberate,
post-validation step — never bundle it into the restore procedure
itself.

## 8. Disposable restore rehearsal

The primary, recommended way to prove a backup actually works is to
restore it into a **disposable, differently-named database on your
existing PostgreSQL instance**, never into (nor overwriting) your live
`ridgenote` database. This runbook's own authoring performed exactly this
rehearsal, using the name `ridgenote_restore_rehearsal`.

**Step 1 — confirm the rehearsal database does not already exist:**

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -l' | grep -w ridgenote_restore_rehearsal
```

If this prints a matching line, **stop** — a database with this name already
exists unexpectedly, and you must resolve that before proceeding (do not
assume it is safe to reuse or drop without understanding why it exists).
An empty result confirms it is safe to create.

**Step 2 — create it with the correct owner:**

```sh
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE ridgenote_restore_rehearsal OWNER \"$POSTGRES_USER\";"'
```

**Step 3 — copy the dump into the container, if it is not already
there:**

```sh
docker compose cp ./ridgenote-backup-YYYYMMDD-HHMMSS.dump postgres:/tmp/ridgenote-restore-rehearsal.dump
```

**Step 4 — restore into the rehearsal database:**

```sh
docker compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d ridgenote_restore_rehearsal --no-owner /tmp/ridgenote-restore-rehearsal.dump'
```

Confirm this exits `0`. **Never add `--clean` to a `pg_restore` invocation
that targets the live `ridgenote` database — this rehearsal procedure
intentionally targets only the disposable rehearsal database, never the
live one, so `--clean` is not needed here.**

**Step 5 — verify restored row counts against counts taken from the live
database before restore** (see [Section 8a](#8a-verification-queries)
below).

**Step 6 — run application-level checks** (see
[Section 9](#9-restore-into-a-fresh-deployment)).

**Step 7 — clean up** (see [Section 8b](#8b-cleanup)).

### 8a. Verification queries

Before restoring, capture safe **aggregate counts only** from the live
database — never copy identifying content (titles, bodies, usernames)
into a runbook, report, or ticket:

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM accounts_user;"'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM notes_note;"'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM notes_folder;"'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM notes_tag;"'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM accounts_auditevent;"'
```

Then repeat the identical commands with `-d ridgenote_restore_rehearsal`
in place of `-d "$POSTGRES_DB"` after restore, and compare. At minimum,
confirm matching counts for: users, notes, folders, tags, and audit
events. Also compare Trash lifecycle metadata counts (again once against
`-d "$POSTGRES_DB"` and once against `-d ridgenote_restore_rehearsal`):

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FILTER (WHERE trashed_at IS NOT NULL), count(*) FILTER (WHERE emptied_at IS NOT NULL) FROM notes_note;"'
```

And confirm the expected number of tables exist in the restored database
(the schema name `public` is passed as a dollar-quoted PostgreSQL string
literal, `$$public$$`, so it needs no shell-level quote nesting beyond
escaping the two `$` pairs from the container's own shell):

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d ridgenote_restore_rehearsal -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema=\$\$public\$\$;"'
```

This exact verification was performed at authoring time: users, notes,
folders, tags, audit events, and Trash lifecycle (`trashed_at`/
`emptied_at`) counts matched exactly between the live database and the
restored rehearsal database, and the expected set of application tables
was present.

### 8b. Cleanup

After verification succeeds:

```sh
# Terminate any remaining connections to the rehearsal database only
# (the database name is passed as a dollar-quoted literal, $$...$$, to
# avoid nested shell quoting; \$\$ escapes it past the container's own
# shell so psql receives a literal $$ridgenote_restore_rehearsal$$)
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=\$\$ridgenote_restore_rehearsal\$\$ AND pid <> pg_backend_pid();"'

# Drop only the rehearsal database
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP DATABASE ridgenote_restore_rehearsal;"'

# Remove the temporary in-container dump copy
docker compose exec -T postgres rm -f /tmp/ridgenote-restore-rehearsal.dump
```

**Never target the live `ridgenote` database with `pg_terminate_backend`
or `DROP DATABASE`.** Remove the host copy of the rehearsal dump too,
unless you are deliberately retaining it as verification evidence.
Afterward, confirm the live database's own counts are unchanged, `web`
still reports healthy (`curl -fsS http://127.0.0.1:8000/health/`), and
the scheduler is still in its original disabled state — all three were
confirmed unchanged at authoring time.

A fully isolated second Docker Compose project (its own `-p` project
name, its own volume and network) is a more rigorous option for true
disaster-recovery drills, and can be described to your own team as a
future enhancement, but is not required for routine rehearsal — a
differently-named database on the existing instance, as above, is
sufficient.

## 9. Restore into a fresh deployment

To verify a restored database is actually usable by the application,
without touching your persistent `.env` or Compose configuration, run
one-shot commands against the rehearsal (or a genuinely fresh) database
by overriding `RIDGENOTE_DATABASE_NAME` on the command line only:

```sh
docker compose run --rm -e RIDGENOTE_DATABASE_NAME=ridgenote_restore_rehearsal -e RIDGENOTE_PURGE_ENABLED=false web python manage.py check
docker compose run --rm -e RIDGENOTE_DATABASE_NAME=ridgenote_restore_rehearsal -e RIDGENOTE_PURGE_ENABLED=false web python manage.py migrate --noinput
```

At minimum, also run safe aggregate-only Django-shell checks:

```sh
docker compose run --rm -e RIDGENOTE_DATABASE_NAME=ridgenote_restore_rehearsal -e RIDGENOTE_PURGE_ENABLED=false web python manage.py shell -c "
from accounts.models import User, AuditEvent
from notes.models import Note, Folder, Tag
print('users', User.objects.count())
print('notes', Note.objects.count())
print('folders', Folder.objects.count())
print('tags', Tag.objects.count())
print('audit_events', AuditEvent.objects.count())
"
```

**Do not run the normal scheduler against a rehearsal or restored
database.** A second browser-facing `web` instance/port is not required
— these one-shot application commands are sufficient to prove the
restored schema and data are usable.

At authoring time, `manage.py check` reported zero issues, `migrate
--noinput` reported no migrations to apply (a clean no-op, as expected
when the restored dump already matches the current schema version), and
the shell-based counts matched the live database exactly.

For a genuinely fresh deployment (new host, empty PostgreSQL instance,
nothing to preserve), the same restore sequence applies directly to the
target database — see [Section 11](#11-disaster-recovery-onto-a-new-host)
for that full sequence end to end.

## 10. Existing-database replacement (advanced — not rehearsed)

**This section is documented for reference only. It was not rehearsed
against this deployment's live database, and you should always restore
into a disposable database first (Sections 8-9) before ever considering
this procedure.**

**This is a separate mechanism from RidgeNote's user-facing Library
Backup feature (personal library download/restore inside the
application).** Library Backup already supports moving one user's
library into a different account through the application's own
restore workflow, entirely independent of this section's
PostgreSQL-level, whole-database procedure -- the two are not
substitutes for each other. This section remains an advanced, optional
operator technique for anyone comfortable managing PostgreSQL
directly; it is not the expected recovery path for RidgeNote's
supported deployment model, and it is not a maintainer-supported
recovery workflow.

Conceptually, replacing an existing, in-use database with a restored
backup requires:

1. Stop `web` and `scheduler` (do not leave them running against a
   database mid-replacement).
2. Confirm `RIDGENOTE_PURGE_ENABLED=false`.
3. Terminate active sessions on the target database.
4. Drop and recreate (or otherwise safely clean) the intended target
   database — **verify the exact target database name explicitly, out
   loud, before running any destructive command; a single wrong `-d`
   argument here can destroy the wrong database.**
5. Restore the dump into it.
6. Run `manage.py migrate --noinput`.
7. Verify the restored data (as in Sections 8a/9) **before** restarting
   `web` or `scheduler`.
8. Only then restart application services, with purge still disabled
   until you have reviewed the result.

Do not attempt this procedure without a fresh, verified backup of the
*current* state of the database you are about to replace, taken
immediately beforehand — you cannot undo step 4.

## 11. Disaster recovery onto a new host

**This sequence composes the individually-verified backup (Section 4)
and restore (Section 8) procedures; the specific scenario of a
completely new host has not been independently rehearsed end to end.
Validate carefully in your own environment before relying on it.**

For recovering onto a completely new host (for example, after total loss
of the original machine):

1. Obtain the RidgeNote repository at the commit recorded alongside your
   backup (see [Section 5](#5-backup-metadata-and-version-capture)), or a
   compatible newer commit.
2. Recover your `.env` file from your own secure, separate storage — it
   is never included in a database dump (see
   [Section 13](#13-secrets-and-configuration)).
3. Use a compatible PostgreSQL major version (matching the version
   recorded with the backup wherever practical).
4. Start only `postgres` first: `docker compose up -d postgres`.
5. Create the target database if it does not already exist, then restore
   the dump into it (as in [Section 4](#4-logical-backup-procedure) and
   [Section 8](#8-disposable-restore-rehearsal), adapted to the real
   target database name instead of the rehearsal name).
6. Run `manage.py migrate --noinput`.
7. Validate the restored data (Sections 8a/9) before proceeding.
8. Start `web`: `docker compose up -d web`.
9. Leave `scheduler` stopped, or `RIDGENOTE_PURGE_ENABLED=false`, until
   you have explicitly reviewed the restored data.
10. Only after that review, enable the scheduler if desired.

This runbook does not create or require any release-packaging or
image-publishing workflow — use whatever image/commit you already have
available, provided it is compatible with the backup's recorded version
metadata.

## 12. Migration and version compatibility

- A custom-format (`-Fc`) dump embeds the source PostgreSQL server
  version in its own archive header (visible via `pg_restore --list`,
  see [Section 6](#6-backup-validation)).
- Restore using the same PostgreSQL **major** version whenever
  practical.
- Check the RidgeNote Git commit SHA recorded alongside the backup (see
  [Section 5](#5-backup-metadata-and-version-capture)). Restore using
  application code at that commit, or a compatible **newer** one —
  **never older**. Do not assume a newer database schema is safe to read
  with older application code.
- Run `python manage.py migrate --noinput` after **every** restore, as
  standard practice — a clean no-op when the restored dump already
  matches current code (as verified in
  [Section 9](#9-restore-into-a-fresh-deployment)), or a normal forward
  migration when restoring an older dump under newer, compatible code.
- **Never document or perform a reverse migration as a normal recovery
  technique.** If you find yourself needing to run an older schema
  against newer data, treat that as an exceptional situation requiring
  its own careful, non-routine handling — not something this runbook
  covers.

## 13. Secrets and configuration

**A database dump and your `.env` file must always be stored separately,
never bundled into the same archive.** Preserve, securely and separately
from any database dump:

- `RIDGENOTE_SECRET_KEY`
- PostgreSQL/database credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
  `RIDGENOTE_DATABASE_*`)
- `RIDGENOTE_ALLOWED_HOSTS` / `RIDGENOTE_CSRF_TRUSTED_ORIGINS`
- Session/lockout configuration
- Purge enablement and interval settings
- Any other deployment-specific `.env` value

Changing `RIDGENOTE_SECRET_KEY` invalidates existing sessions (every
logged-in user is logged out) but does **not** make note content
unreadable — RidgeNote does not currently encrypt note content using
`SECRET_KEY`.

A database dump must never contain a copied `.env` file. If you want a
single encrypted deployment archive covering both configuration and
data, that is your own separate operational choice, entirely outside
what this runbook documents. RidgeNote does not require or invent a
secrets-manager integration.

## 14. Privacy and security

A database dump contains full note titles, bodies, user records,
lifecycle metadata, and audit data. **It is exactly as sensitive as the
live database itself.** Treat it accordingly:

- Never commit a dump to Git.
- Never leave a dump world-readable — apply restrictive permissions
  (`chmod 600`) immediately after creating it, as shown in
  [Section 4](#4-logical-backup-procedure).
- Store dumps on access-controlled storage.
- Encrypt stored or off-host copies using your own established tooling —
  RidgeNote does not provide or require a specific one.
- Transfer copies only over secure channels.
- Securely dispose of obsolete copies once they are no longer needed.
- Never expose passwords or `RIDGENOTE_SECRET_KEY` in logs, commands,
  screenshots, or runbook examples. Every command in this runbook avoids
  placing a password on the command line by relying on the `postgres`
  container's own environment variables.

## 15. Retention guidance

This is advisory, not mandatory — adapt it to your own storage, risk
tolerance, and any external backup system you already use:

- Daily logical backups as a reasonable starting point.
- Roughly 14 daily restore points retained.
- A periodic off-host copy (encrypted, per
  [Section 14](#14-privacy-and-security)).
- Repeat the disposable restore rehearsal (Section 8) after initial
  setup, and again after any major schema change.

RidgeNote does not build or provide any rotation or deletion automation
for backups — retention is entirely your own responsibility to manage.

## 16. Troubleshooting

**`pg_dump` fails or exits non-zero:** confirm `postgres` is healthy
(`docker compose ps postgres`) and that `POSTGRES_USER`/`POSTGRES_DB` are
set correctly in the container's own environment. Do not proceed to copy
or delete anything if this step failed.

**`docker compose cp` fails or the host file is empty/missing:** do not
run the in-container cleanup step. Leave the in-container dump in place
and retry the copy.

**`pg_restore --list` fails or reports an error:** treat the dump as
unverified — do not rely on it for recovery. Investigate whether the
copy into the container completed correctly, or re-create the dump from
scratch.

**Restore into the rehearsal database fails partway through:** inspect
`pg_restore`'s own error output; a non-zero exit status means the
rehearsal database's state should not be trusted. Drop and re-create the
rehearsal database (never the live one) and retry.

**A database named `ridgenote_restore_rehearsal` already exists
unexpectedly:** stop. Do not drop or reuse it without first
understanding why it exists — it may be leftover from an earlier
incomplete rehearsal (check whether it is safe to drop) or, in an
unusual case, something unrelated using the same name.

**Application checks against the rehearsal database report unexpected
counts:** do not treat the restore as validated. Re-check that you
restored the intended dump file, and re-run the source-vs-restored
comparison in [Section 8a](#8a-verification-queries).

## 17. Backup checklist

- [ ] `pg_dump` inside the `postgres` container exits `0`
- [ ] Host copy (`docker compose cp`) exits `0` and the file exists,
      nonempty
- [ ] Restrictive permissions (`chmod 600`) applied to the host file
- [ ] In-container temporary dump removed, only after the host copy was
      confirmed
- [ ] `pg_restore --list` against the dump exits `0` and reports a
      plausible header and TOC entry count
- [ ] Git commit SHA, PostgreSQL version, timestamp/timezone, deployment
      identity, and purge-enabled state recorded alongside the backup
- [ ] The dump file is stored with restrictive permissions on
      access-controlled storage, separate from `.env`

## 18. Restore checklist

- [ ] `RIDGENOTE_PURGE_ENABLED=false` confirmed before restore
- [ ] `scheduler` is not started during restore preparation or
      validation
- [ ] Target database confirmed disposable (or, for an existing-database
      replacement, the target name explicitly verified) before any
      destructive command
- [ ] `pg_restore` into the target database exits `0`
- [ ] Source-vs-restored aggregate counts match for users, notes,
      folders, tags, and audit events
- [ ] Trash lifecycle metadata (`trashed_at`/`emptied_at`) counts match
- [ ] `manage.py check` and `manage.py migrate --noinput` succeed against
      the restored database
- [ ] No `purge_expired_trash --execute` was run against the restored
      database during recovery
- [ ] Rehearsal/disposable resources fully cleaned up afterward (database
      dropped, in-container temp files removed)
- [ ] Live database, `web` health, and scheduler's original disabled
      state confirmed unchanged after rehearsal
- [ ] Automatic purge remains disabled until the restored data has been
      explicitly reviewed and approved
