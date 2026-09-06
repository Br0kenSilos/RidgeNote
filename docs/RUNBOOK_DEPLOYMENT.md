# RidgeNote Deployment Runbook

## 1. Purpose and supported topology

This runbook covers deploying RidgeNote (Django 5.2 / PostgreSQL 17 /
Docker Compose, `web` / `postgres` / `scheduler` services) for Version
One. RidgeNote V1 documents two primary deployment topologies:

- **Direct HTTP `IP:PORT`** on a trusted private network -- no reverse
  proxy in front of RidgeNote.
- **Reverse proxy** at the root path of a dedicated hostname.

Explicitly, for Version One:

- **Path-prefix deployment is not supported.** RidgeNote must be
  reachable at a host's root path (`/`), never behind a URL prefix such
  as `https://example.test/notes/`.
- **A permanent reverse-proxy container is not part of RidgeNote's
  required stack.** The Compose file ships exactly `web`, `postgres`,
  and `scheduler`; any reverse proxy is the operator's own
  infrastructure, external to this repository.
- **WhiteNoise serves static assets in both topologies.** RidgeNote does
  not need, and this runbook does not add, a separate static-file
  server or CDN step.
- **The direct `IP:PORT` topology assumes a trusted-internal network**
  (plain HTTP, no TLS termination). **The reverse-proxy topology (section
  6) is the documented path for HTTPS and broader/external exposure** --
  it covers the required CSRF/forwarded-header configuration and a
  proxy contract operators can implement with their own TLS-terminating
  reverse proxy. RidgeNote includes practical application-level
  protections (account lockouts, session handling, ownership isolation,
  CSRF protection, and related safeguards), but broader Internet-facing
  hardening beyond a properly configured reverse proxy -- general rate
  limiting, perimeter/WAF controls, and similar -- was not a primary V1
  design goal and remains the operator's own responsibility.

### 1a. Service overview

The shipped Compose file runs exactly three services:

- **`web`** -- the RidgeNote application itself; handles all HTTP
  requests.
- **`scheduler`** -- background Trash retention/purge processing; the
  same image as `web`, running `python manage.py run_purge_scheduler`
  instead. See section 8 for full detail. By default
  (`RIDGENOTE_PURGE_ENABLED=false`) it starts, logs that automatic
  purge is disabled, and idles -- it never touches the database and
  its temporary downtime has no effect on ordinary RidgeNote use.
- **`postgres`** -- the database. PostgreSQL is RidgeNote's only data
  store: it holds all application data (notes, folders, tags,
  accounts) and is relied on for the transactions, constraints, and
  locking that keep concurrent edits/restores/purges safe, as well as
  RidgeNote's full-text search. There is no separate cache, queue, or
  search service to run alongside it.

Both `web` and `scheduler` apply pending database migrations
automatically at startup before doing anything else -- see section 4a.

## 2. Prerequisites

- Docker Engine and Docker Compose (the version already pinned by this
  repository's `docker-compose.yml`).
- Persistent storage for PostgreSQL data -- do not run PostgreSQL
  against ephemeral container storage for a real deployment.
  `deploy/docker-compose.yml` (the standalone deployment bundle) uses a
  host **bind mount** at `data/postgres`, with no named Docker volume
  at all; the root, local-development `docker-compose.yml` instead
  uses a named Docker volume (`postgres-data`). The two are not
  interchangeable and behave differently under `docker compose down
  -v` -- see section 9a.
- **Do not publish the PostgreSQL service port to untrusted networks or
  the public Internet.** The shipped Compose file does not publish it
  externally by default -- keep it that way.
- A strong, unique `RIDGENOTE_SECRET_KEY` -- never the
  `development-only-change-me` default from `.env.example`.
- A recent backup taken before any upgrade (see
  `docs/RUNBOOK_BACKUP_RESTORE.md`) -- automatic startup migrations
  (section 4a) do not make this optional.
- DNS, hostname, and (if using one) reverse-proxy planning done before
  first deployment. `RIDGENOTE_ALLOWED_HOSTS` must be set to the exact
  host(s) the deployment will be reached at; `RIDGENOTE_CSRF_TRUSTED_ORIGINS`
  is required only behind an HTTPS reverse proxy; `RIDGENOTE_EXTERNAL_URL`
  is optional but, when set, should match how users actually reach the
  deployment.
- If `RIDGENOTE_TRUST_X_FORWARDED_PROTO=true` will be used, firewall or
  network-level restriction ensuring RidgeNote's backend port is reached
  only through the trusted proxy -- see section 5's security warning.

## 3. Environment reference

All configuration is environment-variable driven (`RIDGENOTE_*`), following
this project's existing convention -- no other configuration mechanism
is introduced by this runbook.

| Variable | Required? | Safe default | Direct-IP example | Proxy example | Notes |
|---|---|---|---|---|---|
| `RIDGENOTE_SECRET_KEY` | Required (production) | insecure dev key | strong random value | strong random value | Never reuse the shipped default outside local development. |
| `RIDGENOTE_DEBUG` | Optional | `0` (false) | `0` | `0` | Never `1` in a real deployment. |
| `RIDGENOTE_ALLOWED_HOSTS` | Required (production) | `localhost,127.0.0.1,0.0.0.0` | `192.0.2.10` | `notes.example.test` | Comma-separated; must include the exact host clients will use. |
| `RIDGENOTE_CSRF_TRUSTED_ORIGINS` | Required for proxy HTTPS | empty | empty | `https://notes.example.test` | Must include the scheme; Django rejects a bare hostname here. |
| `RIDGENOTE_EXTERNAL_URL` | Optional (recommended) | empty | `http://192.0.2.10:8000` | `https://notes.example.test` | Blank is valid for a direct-LAN deployment. Used for absolute links; becomes necessary once automatic SMTP invitation delivery is configured (section 3a). When set, should match how users actually reach the deployment. |
| `RIDGENOTE_TRUST_X_FORWARDED_PROTO` | Optional | `false` | `false` | `true` | See section 5 -- enable only when reachable exclusively through a trusted proxy. |
| `RIDGENOTE_USE_X_FORWARDED_HOST` | Optional | `false` | `false` | `false` unless the proxy contract requires it | Independent of the above; enabling it trusts `X-Forwarded-Host` for `request.get_host()`. |
| `RIDGENOTE_SMTP_HOST` | Optional | empty | empty (manual-copy only) | `smtp.example.com` | Leave blank for manual-copy-only invitation delivery -- see section 3a. |
| `RIDGENOTE_SMTP_PORT` | Optional | empty | empty | `587` | Positive integer; never inferred from TLS mode; required once any other SMTP variable is set. |
| `RIDGENOTE_SMTP_TLS_MODE` | Optional | `none` | `none` | `starttls` | One of `none`/`starttls`/`ssl`; any other value fails at startup. |
| `RIDGENOTE_SMTP_USERNAME` / `RIDGENOTE_SMTP_PASSWORD` | Optional | both empty | both empty | both set | Leave *both* blank for an unauthenticated relay; setting only one fails at startup. |
| `RIDGENOTE_SMTP_FROM_EMAIL` | Optional | empty | empty | `ridgenote@example.com` | Required once any other SMTP variable is set. |
| `RIDGENOTE_SMTP_FROM_NAME` | Optional | empty | empty | `RidgeNote` | Optional display name; blank uses the bare From address alone. |
| `RIDGENOTE_DATABASE_NAME` / `_USER` / `_PASSWORD` / `_HOST` / `_PORT` | Required (production) | dev-friendly values | real credentials | real credentials | `_HOST` is `postgres` in the shipped Compose file, not operator-configurable there. |
| `RIDGENOTE_LOG_LEVEL` | Optional | `INFO` | `INFO` | `INFO` | Any standard Python logging level name; not validated against a fixed list. |
| `RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD` | Optional | 5 | 5 | 5 | Strict positive integer; malformed values fail at startup. |
| `RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS` | Optional | 900 | 900 | 900 | Strict positive integer; malformed values fail at startup. |
| `RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS` | Optional | 900 | 900 | 900 | Strict positive integer; malformed values fail at startup. |
| `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS` | Optional | 3600 | 3600 | 3600 | Strict positive integer; must exceed the warning value below. |
| `RIDGENOTE_SESSION_WARNING_SECONDS` | Optional | 300 | 300 | 300 | Strict positive integer; must be less than the idle timeout above. |
| `RIDGENOTE_INVITATION_EXPIRY_MINUTES` | Optional | 120 | 120 | 120 | Strict positive integer; how long an administrator-issued invitation link stays valid before it must be reissued. |
| `RIDGENOTE_PURGE_ENABLED` | Optional | `false` | `false` | `false` | Strict `true`/`false`; scheduler-only, validated in the management command, not shared settings. |
| `RIDGENOTE_PURGE_INTERVAL_SECONDS` | Optional | 86400 | 86400 | 86400 | Strict positive integer; scheduler-only. |

There are no separate item-purge batch-size environment variables
today -- `notes/purge.py`'s per-cycle batch sizes (`NOTE_BATCH_SIZE =
200`, `FOLDER_BATCH_SIZE = 200`) are fixed constants in
`core/management/commands/run_purge_scheduler.py`, not
environment-driven.

### 3a. Optional email delivery

RidgeNote's administrator-managed invitation workflow never requires
SMTP -- an invited account's one-time link can always be copied from the
browser and delivered manually by the administrator, exactly as it was
before this feature existed. Setting **all** of `RIDGENOTE_SMTP_HOST`,
`RIDGENOTE_SMTP_PORT`, `RIDGENOTE_SMTP_FROM_EMAIL`, and
`RIDGENOTE_EXTERNAL_URL` additionally makes an optional automatic-email
control available on Create User (Invitation mode) and on Reissue/Issue
invitation. Leaving any of the SMTP variables unset is a fully normal,
unwarned deployment shape -- it is not a degraded state.

**Unauthenticated relay support:** `RIDGENOTE_SMTP_USERNAME` and
`RIDGENOTE_SMTP_PASSWORD` may both be left blank if your SMTP relay
permits sending without authentication (common for an internal-only
relay). Setting only one of the two is rejected at startup as
likely-accidental half-configuration -- either set both, or leave both
blank.

**Manual link vs. emailed link, deliberately different origins:** the
administrator-facing one-time Copy Link in the browser always reflects
the request's own origin (`request.build_absolute_uri()`), regardless of
SMTP configuration. An *automatically emailed* invitation link instead
always uses `RIDGENOTE_EXTERNAL_URL`. These are not expected to match in
every topology -- an administrator reaching RidgeNote via a direct,
internal address while `RIDGENOTE_EXTERNAL_URL` is set to the
public-facing reverse-proxy hostname a recipient will actually use is
the expected, correct shape, not a bug to reconcile.

**Reverse-proxy caveat, restated:** if you use the direct-IP path for
administration while a reverse proxy separately serves
`RIDGENOTE_EXTERNAL_URL`'s hostname for recipients, remember that
`RIDGENOTE_TRUST_X_FORWARDED_PROTO=true` (section 5's security warning)
is safe only when the backend port is reached *exclusively* through the
trusted proxy for whichever traffic is supposed to be trusted as HTTPS.
A temporary dual-access acceptance/testing topology (administrator via
direct IP, recipients via the proxy) is not the same thing as a
recommended production layout -- restrict direct backend access before
treating either topology as your permanent deployment shape.

**Credentials never belong in a committed file.** `RIDGENOTE_SMTP_PASSWORD`
(and any other secret in this table) belongs only in your local,
already-gitignored `.env` -- never in `.env.example`, never in
`docker-compose.yml`, never in a commit of any kind.

## 4. Initial deployment sequence

1. Create or update `.env` from `.env.example`, setting real values for
   the required variables in section 3 (most others are optional and
   already have a safe default -- see the table's "Required?" column).
2. Build (or pull) the image: `docker compose build`.
3. Start everything: `docker compose up -d`. There is no separate
   manual migration step for an ordinary install -- `web` and
   `scheduler` each apply any pending database migrations
   automatically at startup, before serving traffic or beginning purge
   processing. See section 4a for exactly how this works and what to
   do if it fails.
4. Watch startup: `docker compose logs -f`. Expect
   `Startup migration check beginning`, `PostgreSQL reachable`,
   `Applying database migrations`, `Migrations complete`, and
   `Migration lock released` from `web` (and, redundantly and
   harmlessly, from `scheduler` too) before Gunicorn's own startup
   lines appear. Press Ctrl+C once things look healthy -- the
   containers keep running.
5. Verify `/health/` -- e.g. `curl -fsS http://<host>:8000/health/`
   (direct) or `curl -fsS https://<hostname>/health/` (proxy) -- expect
   `{"ok": true, "database": 1}`.
6. Inspect `web` logs: `docker compose logs --no-color --tail=80 web`.
7. Inspect `scheduler` logs: `docker compose logs --no-color --tail=80
   scheduler` -- confirm it logged its enabled/disabled state cleanly
   (see section 8).
8. Perform a browser smoke test: log in, create a note, confirm it
   saves and reloads correctly.

## 4a. Automatic startup migrations

Both `web` and `scheduler` run a brief startup check, before their
real process starts, that: waits for PostgreSQL to accept connections
with the configured credentials; acquires a PostgreSQL advisory lock
so that `web` and `scheduler` starting at the same time never apply
migrations concurrently (whichever container reaches the lock first
performs the real migration; the other waits, then finds nothing left
to do); runs `python manage.py migrate --noinput`, Django's own
ordinary migration command against its own migration-state table --
no separate schema marker is used; then releases the lock and starts
Gunicorn (`web`) or `run_purge_scheduler` (`scheduler`). Neither
container serves traffic or begins purge processing until this check
succeeds. An ordinary restart with nothing pending is fast -- the
check confirms there is nothing to do and moves on immediately.

If PostgreSQL is not yet reachable, the check retries for roughly
60-90 seconds (a short initial wait, capped backoff, throttled logging
-- not a line every fraction of a second) before giving up. If
PostgreSQL actively rejects the connection (wrong password, unknown
database), it fails immediately without retrying, since retrying
cannot fix a configuration error. If a migration itself fails, the
check logs the failure and exits without starting `web` or
`scheduler` -- the container will not silently serve an outdated or
partially migrated schema. Docker's existing `restart: unless-stopped`
policy retries the container afterward; each attempt logs the same
sequence again, so `docker compose logs -f` always shows what is
happening, never a silent hang.

**Automatic migrations do not make every upgrade risk-free.** They
apply ordinary, backward-compatible schema changes only. A future
release containing a breaking or operator-intervention-required
database change will say so explicitly in that release's release
notes and upgrade documentation -- including whether a backup is
required beforehand, a minimum supported source version, or any
rollback limitation -- rather than silently auto-applying it. Once
RidgeNote is formally published, read the release notes before
upgrading.

A manual migration command remains available for troubleshooting --
for example, to check what `migrate` would do without starting any
service:

```sh
docker compose run --rm web python manage.py migrate --noinput
```

This is not part of the normal install or upgrade flow; the automatic
startup check already runs it for you. `docker-entrypoint.sh`
recognizes this exact invocation (and `manage.py startup_migrate`) and
skips running the automatic check a second time in front of it.

## 5. Direct IP:PORT deployment

Example (documentation-safe address, not a real network):

```text
http://192.0.2.10:8000
```

- **Host binding:** the shipped `web` service already binds Gunicorn to
  `0.0.0.0:8000` inside the container and publishes it via Compose's
  `ports: ["8000:8000"]`; no code or Compose change is needed for this
  topology.
- `RIDGENOTE_ALLOWED_HOSTS=192.0.2.10` (or the deployment's real
  host/IP).
- `RIDGENOTE_EXTERNAL_URL=http://192.0.2.10:8000`.
- `RIDGENOTE_TRUST_X_FORWARDED_PROTO=false` -- there is no proxy to
  supply a trustworthy `X-Forwarded-Proto`, so this must stay disabled.
- `RIDGENOTE_USE_X_FORWARDED_HOST=false` -- same reasoning.
- **Trusted-network assumption:** plain HTTP is only appropriate because
  this topology assumes a private, trusted network between clients and
  the server (for example, a home or office LAN). Do not expose this
  configuration directly to the public internet.
- **Firewall considerations:** restrict inbound access to port `8000` to
  the trusted network only; there is no proxy or TLS termination in this
  topology to add a second layer of protection.
- **Static assets** are served by RidgeNote itself through WhiteNoise --
  no separate web server or CDN step is required.
- **`/health/`** is reachable at `http://192.0.2.10:8000/health/`.
- **`/health/` vs. `/health-status/`:** `/health/` (JSON) is the
  recommended endpoint for automated monitoring (HAProxy backend
  checks, Uptime Kuma, Docker healthchecks, and any other machine
  probe) -- it is what the shipped Compose healthcheck already uses.
  `/health-status/` (HTML) is a human-readable status page for an
  administrator browsing to it directly; it is not intended for
  automated monitoring. The bare root path (`/`) is the application
  front door -- it redirects an anonymous visitor to Login and renders
  the normal workspace for an authenticated one; it is not a status
  endpoint of any kind.

## 6. Reverse-proxy deployment

Example (documentation-safe hostname, not a real domain):

```text
https://notes.example.test
```

Required values:

```text
RIDGENOTE_ALLOWED_HOSTS=notes.example.test
RIDGENOTE_CSRF_TRUSTED_ORIGINS=https://notes.example.test
RIDGENOTE_TRUST_X_FORWARDED_PROTO=true
```

`RIDGENOTE_EXTERNAL_URL` is not strictly required, but is recommended
here so absolute links (and, if configured, automatically emailed
invitation links -- section 3a) point at the correct public hostname:

```text
RIDGENOTE_EXTERNAL_URL=https://notes.example.test
```

`RIDGENOTE_USE_X_FORWARDED_HOST` is a separate setting from
`RIDGENOTE_TRUST_X_FORWARDED_PROTO` and defaults to `false`; only set it
to `true` if the specific proxy contract in use actually relies on
`X-Forwarded-Host` (most single-hostname, root-path reverse-proxy setups
do not need it, since the proxy already forwards the correct `Host`
header unchanged).

**Proxy contract, regardless of vendor:**

- The proxy must preserve or correctly set the external `Host` header
  RidgeNote should see.
- The proxy must **overwrite**, not append or blindly forward, the
  `X-Forwarded-Proto` header with the actual external scheme (`https`)
  -- see the security warning below.
- The proxy routes `/health/` through to RidgeNote like any other path;
  RidgeNote does not require a separate internal health-check path.
- The proxy talks to RidgeNote at `web:8000` (the Compose service name
  and port); RidgeNote itself is never reconfigured to know it is behind
  a proxy beyond the environment variables above.
- Root path only -- no path-prefix rewriting is supported.
- No WebSocket support is required; RidgeNote is a conventional
  request/response Django application with no WebSocket endpoints.
- Reasonable upload and timeout limits are a proxy-configuration choice;
  this runbook does not assert a specific required limit, since none is
  currently enforced or documented as a fixed RidgeNote requirement.

**Security warning -- forwarded-protocol trust:** enabling
`RIDGENOTE_TRUST_X_FORWARDED_PROTO=true` means RidgeNote will trust a
client-supplied `X-Forwarded-Proto` header as reported by whatever sits
directly in front of it. This is only safe when RidgeNote's backend port
is reachable **exclusively** through the trusted proxy -- if the backend
port is also reachable directly (bypassing the proxy), a client could
forge this header. When this setting is enabled, restrict or firewall
direct access to the backend port so all traffic is forced through the
proxy.

Below is one concise, non-normative example per proxy family. Neither
is part of RidgeNote's required stack; both assume the proxy runs as
separate infrastructure the operator manages.

**Nginx-style example (non-normative):**

```nginx
server {
    listen 443 ssl;
    server_name notes.example.test;

    location / {
        proxy_pass http://web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

**Caddy-style example (non-normative):**

```caddyfile
notes.example.test {
    reverse_proxy web:8000 {
        header_up X-Forwarded-Proto https
    }
}
```

## 7. Upgrade procedure

1. Verify a recent backup exists (`docs/RUNBOOK_BACKUP_RESTORE.md`) --
   automatic startup migrations (section 4a) do not make this
   optional.
2. Record the current image tag/commit/version in use (`docker compose
   config --images`), for rollback reference.
3. Build or pull the new image.
4. Recreate `web` and `scheduler`: `docker compose up -d`. There is no
   separate manual migration step -- both containers apply any pending
   migration automatically at startup (section 4a).
5. Watch startup with `docker compose logs -f` and confirm the startup
   migration check completes cleanly (section 4a), then verify
   `/health/` returns `{"ok": true, "database": 1}`.
6. Inspect `web` and `scheduler` logs for errors.
7. Perform a browser smoke test.
8. Retain the recorded prior version (step 2) in case a rollback is
   needed -- see section 7a.

### 7a. Rollback

Rolling back the **application image** (set the image reference back
to the prior tag recorded in step 2 above, then `docker compose pull
&& docker compose up -d`) is straightforward. **Rolling back the
database schema is not automatically supported** -- this runbook does
not promise or document a database downgrade path. If a migration must
be reverted, restore the pre-upgrade backup taken in step 1 above
instead of attempting an in-place schema downgrade.

### 7b. PostgreSQL major-version upgrades

V1 is pinned to PostgreSQL 17 (`postgres:17.10-bookworm`, by digest,
in the shipped Compose file). The automatic startup migrations
described in section 4a are ordinary Django application/database
schema migrations -- they never upgrade PostgreSQL itself. **In-place
PostgreSQL major-version upgrades (e.g. 17 -> 18) are not supported by
the current V1 Compose deployment.** A future PostgreSQL major-version
change requires its own documented migration procedure, not covered by
this runbook -- the general mechanism will likely involve a fresh
PostgreSQL container of the new major version plus a logical
migration such as `pg_dump`/`pg_restore` (`docs/RUNBOOK_BACKUP_RESTORE.md`
already documents this tooling for backup/restore, but not yet as a
major-version upgrade procedure), not an in-place binary upgrade of
the existing data directory. Do not attempt a PostgreSQL major-version
change without a verified, current backup taken first.

## 8. Scheduler operations

- **Disabled by default:** `RIDGENOTE_PURGE_ENABLED` defaults to
  `false`; a fresh deployment performs no automatic item purge until an
  operator deliberately enables it.
- **Strict parsing:** both `RIDGENOTE_PURGE_ENABLED` (case-insensitive
  `true`/`false`, blank rejected) and `RIDGENOTE_PURGE_INTERVAL_SECONDS`
  (positive integer) fail the `scheduler` container's startup clearly on
  a malformed value -- this validation is scheduler-local and does not
  affect `web`.
- **Immediate startup cycle:** when enabled, the scheduler runs one
  purge cycle immediately at startup, then waits the configured interval
  before the next cycle.
- **No persisted last-run timestamp, no replay of missed intervals:**
  the scheduler does not track when it last ran across restarts. A
  restart simply runs one fresh cycle evaluating whatever is currently
  eligible -- it never tries to "catch up" on every interval it missed
  while stopped.
- **Batch-limited backlog clearing:** each cycle processes a bounded
  batch of eligible notes and folders; a large backlog is cleared
  gradually across multiple cycles, not all at once.
- **Restart safety:** because eligibility is re-evaluated fresh each
  cycle and already-purged rows are simply no longer eligible, starting,
  stopping, or restarting the scheduler container at any time is safe
  and never causes duplicate purge attempts.
- **Advisory-lock protection:** all purge cycles (scheduler-driven or
  the manual `purge_expired_trash` command) contend for the same
  PostgreSQL advisory lock (`PURGE_CYCLE_LOCK_ID`), so overlapping
  cycles from any source safely skip rather than double-processing.
- **Graceful shutdown:** `SIGTERM`/`SIGINT` are handled between cycles;
  an in-progress purge transaction is always allowed to finish before
  the process exits.
- **Logs are the Version One observability mechanism.** There is no
  scheduler dashboard, metrics endpoint, or persisted run-history table
  -- `docker compose logs scheduler` is how an operator confirms the
  scheduler is enabled, what a cycle did, and whether anything failed.
- **No scheduler Docker healthcheck exists.** Unlike `web` and
  `postgres`, the `scheduler` service has no `healthcheck:` block in
  `docker-compose.yml`. `restart: unless-stopped` still recovers from an
  outright process crash. A missed or delayed purge cycle is a
  delay-only condition, not a data-loss or note-safety risk, since
  purge-eligible items simply remain safely in Trash until the next
  successful cycle.
- **No automatic account purge exists or is planned for Version One.**
  Account purge (as opposed to note/folder purge) remains a manual,
  administrator-triggered, typed-confirmation action only -- a
  deliberate safety choice, since permanently deleting a user's entire
  account and library on an unattended timer carries materially higher
  risk than automatically purging already-trashed note/folder content.

### 8a. Log retention

RidgeNote bounds each container's Docker logs by default -- `web`,
`scheduler`, and `postgres` all use:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

- **Driver remains `json-file`** -- the standard Docker default, fully
  compatible with `docker compose logs <service>`/`docker logs
  <container>` for normal inspection.
- **Maximum retained history is approximately 50 MB per container**
  (an active file up to 10 MB, plus up to 4 older rotated files of the
  same size) -- an operational ceiling, not an exact byte guarantee.
- **Oldest rotated logs are discarded automatically** once the limit is
  reached; nothing is silently truncated mid-file.
- **Adjust in Compose if you want more or less retained history** --
  edit the `max-size`/`max-file` values for one or all three services in
  whichever Compose file you deploy from.
- **Changing logging options requires container recreation** -- editing
  the Compose file alone does not retroactively apply to an already
  -running container.
- **This is not long-term archival or centralized logging.** If your
  deployment needs history beyond what fits on the host, or aggregation
  across multiple hosts, that remains a separate, unimplemented future
  concern -- forward `docker logs` output to your own external logging
  infrastructure if you need it.

## 9. Restore safety

Full backup and restore procedures live in
`docs/RUNBOOK_BACKUP_RESTORE.md`; full purge operational detail lives in
`docs/RUNBOOK_PURGE.md`. This section does not duplicate either --
it states only the cross-cutting requirement that applies whenever a
restore has just happened:

- Keep the scheduler disabled (`RIDGENOTE_PURGE_ENABLED=false`) during a
  restore and its immediate review.
- Inspect the restored database's Trash state before considering item
  purge safe to resume -- a restored backup can reintroduce rows whose
  age no longer reflects when they were actually trashed relative to the
  live system's history.
- Only re-enable item purge after that operator review is complete.
- Account purge remains a separate, manual, administrator-triggered
  action regardless of restore state -- it is never automated by a
  restore or by re-enabling the scheduler.

### 9a. Destructive actions and data safety

Precise, deployment-specific behavior -- do not rely on generic Docker
assumptions:

- **`docker compose down` is safe and non-destructive** in both the
  standalone deployment bundle (`deploy/docker-compose.yml`) and local
  development -- it stops the containers only. In the deployment
  bundle, `data/postgres` is a host bind mount, not something Compose
  manages, so it is entirely unaffected.
- **`docker compose down -v`, run against the standalone deployment
  bundle, is also safe for `data/postgres`.** That Compose file defines
  no named Docker volumes at all -- `-v` only removes Docker-managed
  volumes, never a host bind mount, so there is nothing for it to
  remove. **This is different from local development**, where the root
  `docker-compose.yml` uses a named volume (`postgres-data`) that
  `down -v` *would* remove -- do not assume the two Compose files
  behave identically.
- **Manually deleting `data/postgres`** (e.g. `rm -rf`), or otherwise
  reinitializing or replacing it, **is destructive** and permanently
  deletes every note, folder, and account. Take a backup first (see
  `docs/RUNBOOK_BACKUP_RESTORE.md`) before any action that touches this
  path directly.
- **Changing `RIDGENOTE_DATABASE_NAME`, `_USER`, or `_PASSWORD` on an
  already-initialized `data/postgres` is not equivalent to
  provisioning a fresh database.** PostgreSQL's own `POSTGRES_DB`/
  `POSTGRES_USER`/`POSTGRES_PASSWORD` (derived from these three
  RidgeNote-facing variables) are consulted only when PostgreSQL
  initializes a genuinely empty data directory. Changing them later,
  against an existing data directory, does not rename, recreate, or
  re-encrypt anything already there -- it only changes what RidgeNote
  itself tries to connect as, which will mismatch what already exists
  and surface as an authentication or "database does not exist" error,
  not data loss by itself. Password changes specifically also require
  updating the password *inside* PostgreSQL (e.g. via `ALTER ROLE`) to
  match, since changing the environment variable alone does not change
  the already-stored credential. Back up first, and treat any such
  change as a deliberate, understood migration -- not a casual `.env`
  edit -- following `docs/RUNBOOK_BACKUP_RESTORE.md` rather than
  guessing.

## 10. Troubleshooting

- **First-run setup (`/setup/`) not behaving as expected:** check
  `docker compose ps` for all three services healthy/running; inspect
  `docker compose logs web`; verify `/health/` returns `{"ok": true,
  "database": 1}`; and confirm the startup-migration-check logs
  (section 4a) completed successfully -- first-run setup cannot
  proceed correctly if the database schema is not yet migrated.
- **`DisallowedHost`:** the request's `Host` header does not match
  `RIDGENOTE_ALLOWED_HOSTS`. Add the exact host/IP being used to reach
  RidgeNote.
- **CSRF trusted-origin failure:** `RIDGENOTE_CSRF_TRUSTED_ORIGINS` is
  missing the origin, or is missing its scheme (`https://` /
  `http://`). Django requires the full scheme+host, not a bare hostname.
- **Incorrect scheme/redirect behavior:** usually means
  `RIDGENOTE_TRUST_X_FORWARDED_PROTO` is set incorrectly for the actual
  topology -- `true` with no real proxy in front, or `false` with a
  proxy that expects scheme-aware behavior.
- **`X-Forwarded-Proto` trust mistakes:** confirm the proxy
  *overwrites* the header rather than passing through whatever the
  client sent; a proxy that blindly forwards a client-supplied value
  defeats the purpose of enabling trust at all.
- **Missing static assets:** confirm `collectstatic` ran during the
  image build (it runs automatically in the `Dockerfile`) and that
  `STATIC_ROOT`/WhiteNoise's manifest storage found the built frontend
  bundle; rebuilding the image resolves a stale or missing bundle.
- **Startup migration check failing or not appearing in logs:** confirm
  PostgreSQL is reachable with the configured credentials (see section
  4a's retry/failure behavior); a container that never logs `Startup
  migration check beginning` at all was launched with
  `RIDGENOTE_SKIP_STARTUP_MIGRATION=1` or via a command
  `docker-entrypoint.sh` recognizes as an explicit `manage.py migrate`
  invocation, both of which intentionally skip the automatic check. To
  check what `migrate` would do without starting any service: `docker
  compose run --rm web python manage.py migrate --noinput`.
- **Unhealthy `web` container:** check `docker compose logs web`; the
  Compose healthcheck calls `/health/` directly, so a failing check
  usually means the database is unreachable or the process crashed at
  startup.
- **Scheduler disabled when purge was expected:** confirm
  `RIDGENOTE_PURGE_ENABLED=true` is actually set for the `scheduler`
  service specifically (not just `web`), and that the scheduler
  container was recreated after the change.
- **Invalid scheduler configuration:** the `scheduler` container will
  fail to start with a clear error naming the malformed variable; `web`
  is unaffected, since this validation is scheduler-local.
- **Advisory-lock skip:** a log line noting the lock was "held
  elsewhere" is a normal, non-error outcome when two purge cycles
  (scheduler and/or manual) overlap -- it is not a failure.
- **Database unavailable:** `/health/` will return an unhandled-error
  response (HTTP 500) rather than a graceful `{"ok": false}` body, since
  the endpoint does not currently catch database failures -- see this
  runbook's own honest statement of that behavior in section 4.
- **Direct backend exposure while proxy trust is enabled:** if
  `RIDGENOTE_TRUST_X_FORWARDED_PROTO=true` and the backend port is still
  reachable directly (bypassing the proxy), a client can forge
  `X-Forwarded-Proto`. Firewall or network-restrict direct access to the
  backend port whenever this setting is enabled.

## 11. Administrator password recovery

If the sole administrator (or any administrator) cannot log in and no
other administrator can reset their password through the normal admin
UI, use the `recover_admin_password` management command
(`accounts/management/commands/recover_admin_password.py`) directly on
the host:

```sh
docker compose exec web python manage.py recover_admin_password <username>
```

If `web` is not currently running, use `docker compose run --rm web
python manage.py recover_admin_password <username>` instead -- either
form works, since the command only needs a working database
connection, not a healthy running `web` process.

- **Arguments:** exactly one, the target account's username.
- **Interactive only:** the command prompts twice for the new password
  (`New password:` / `Confirm new password:`) via a hidden,
  non-echoing prompt -- the password is never passed as a command-line
  argument or visible in shell history. The two entries must match and
  must pass RidgeNote's normal password validation.
- **Who it can target:** an *existing* account with the Administrator
  role that has already completed first-run/invitation setup. It
  refuses to run against a non-administrator account or an
  account that has not yet completed setup (that account's invitation
  should be managed instead) -- it cannot be used to create a new
  administrator or bootstrap first-run setup.
- **What it changes:** sets the new password immediately (the account
  is not forced to change it again on next login) and invalidates
  every one of that account's existing sessions. It does not touch any
  other account field, and it does not affect Trash, notes, or any
  other data.
- **Audited:** the reset is recorded as a normal `AuditEvent`
  (attributed to the management command, not a web request), the same
  as an in-app administrator password reset.
- **Security caveat:** this command requires host/container access
  (the ability to run `docker compose exec`/`run` against the
  deployment) -- protect that access the same way you protect the
  host itself. It is a legitimate recovery path, not a bypass; treat
  it with the same care as direct database access.

## 12. Building from source

Normal V1 deployment uses a published container image -- see
`deploy/README.md`. Maintainers or operators who intentionally want to
build RidgeNote from source instead can use `scripts/build-image.sh
<registry-image-name> [--push]` from a clean Git working tree; it
builds the image, tags it with a commit-derived `sha-<short-commit>`
tag (and an additional `vX.Y.Z` tag when `HEAD` is exactly an
annotated release tag), and pushes only when `--push` is passed
explicitly. Like the published V1 image, this script currently targets
`linux/amd64` only (see "Supported platforms" in the project
`README.md`). Final registry coordinates for any image this script
produces remain an operator/release decision -- this runbook does not
prescribe one.
