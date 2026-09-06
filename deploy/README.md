# RidgeNote Quick Deploy

Run RidgeNote from a published container image on a trusted internal
network, without cloning the RidgeNote source repository, installing
Git, or building anything locally. This document is deliberately
short and linear. For topology/CSRF/reverse-proxy/troubleshooting
depth, see `docs/RUNBOOK_DEPLOYMENT.md` in the source repository.

## Before you begin

- A host running Docker Engine and a current Docker Compose (the
  `docker compose` plugin, not the legacy standalone `docker-compose`).
  This bundle was validated against Docker `29.6.1` / Compose `v5.3.1`
  on Ubuntu 24.04 LTS; any reasonably current Docker installation is
  expected to work. `linux/amd64` only for V1 -- see "Supported
  Platforms" in the project `README.md`.
- The deploying user is a member of the `docker` group.
- Direct LAN access only for this topology -- no reverse proxy, no
  public exposure. See `docs/RUNBOOK_DEPLOYMENT.md` if you plan to
  place a reverse proxy in front of RidgeNote, or for any broader
  public-exposure hardening -- both are out of scope here. Either way,
  never publish the PostgreSQL service port itself to an untrusted
  network or the public Internet -- the shipped Compose file does not
  do this by default; keep it that way.
- Approximately 2 GiB RAM available is sufficient for RidgeNote plus
  PostgreSQL at this scale.
- You need exactly these three files, and nothing else:
  - `docker-compose.yml`
  - `.env.example`
  - `README.md` (this file)

  No RidgeNote source checkout, no Git, and no local image build are
  needed at any point -- the application image is pulled from the
  configured registry reference. An optional fourth file,
  `setup-deploy-host.sh`, can automate part of the sequence below --
  see "Optional: automate steps 3-6" after the sequence. It is a
  convenience, never a requirement.

## Quick Deploy

1. **Create the deployment directory** and copy the three files above
   into it:

   ```sh
   mkdir -p /opt/ridgenote
   cd /opt/ridgenote
   ```

   The path above is only an example -- use whatever directory makes
   sense on your host. Every command below assumes you are running it
   from this directory unless stated otherwise.

2. **Prepare `.env`:**

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

   `.env.example` is organized into exactly two sections -- REQUIRED
   (no safe default; you must set each one) and OPTIONAL (already has
   a working default) -- so you can see at a glance what actually
   needs your attention.

   Generate the two required secrets:

   ```sh
   openssl rand -hex 48
   ```

   Paste that value into `.env` as `RIDGENOTE_SECRET_KEY`.

   ```sh
   openssl rand -hex 32
   ```

   Paste that value into `.env` as `RIDGENOTE_DATABASE_PASSWORD` --
   this one value is used for both RidgeNote's own database connection
   and PostgreSQL's own initialization; `docker-compose.yml` wires them
   together, so there is nothing else to set or match by hand.

   Then edit `.env` and set the two remaining required values:

   ```dotenv
   RIDGENOTE_IMAGE=<the image reference you want to deploy -- see "Image tags" below>
   RIDGENOTE_ALLOWED_HOSTS=192.0.2.10,127.0.0.1
   ```

   Replace `192.0.2.10` with your own host's actual LAN address, but
   **keep `127.0.0.1` in `RIDGENOTE_ALLOWED_HOSTS`** -- the packaged
   Docker health check always requests
   `http://127.0.0.1:8000/health/` from inside the container, and
   removing `127.0.0.1` makes the web container report unhealthy even
   though RidgeNote itself is running fine. Do not use a wildcard host.

   Two related settings are OPTIONAL and can be left blank for this
   plain-HTTP, direct-LAN topology: `RIDGENOTE_CSRF_TRUSTED_ORIGINS`
   (only matters behind a reverse proxy serving RidgeNote over HTTPS)
   and `RIDGENOTE_EXTERNAL_URL` (only affects the link inside
   automatically-sent invitation emails -- see "SMTP invitation
   delivery" in `.env.example`; ordinary browsing, login, and manual
   invitation links never use it).

   If the image package is private, authenticate before pulling:

   ```sh
   docker login registry.example.com
   ```

   Use a registry access token with package-read scope as the
   password, not your account password. `docker logout
   registry.example.com` afterward if this is a shared or temporary
   session. If anonymous pull already works on your network, this
   step is unnecessary.

   Leave the remaining shipped defaults (`RIDGENOTE_DEBUG=0`,
   `RIDGENOTE_PURGE_ENABLED=false`,
   `RIDGENOTE_TRUST_X_FORWARDED_PROTO=false`) unchanged unless you have
   a specific, understood reason to change them -- the latter two only
   need attention behind a reverse proxy; see
   `docs/RUNBOOK_DEPLOYMENT.md`.

3. **Create persistent directories:**

   ```sh
   mkdir -p data/postgres backups/postgres
   chmod 755 data
   chmod 700 backups backups/postgres
   ```

4. **Discover PostgreSQL's numeric UID/GID.** The pinned PostgreSQL
   image's data directory must be owned by the exact numeric user/
   group the container itself runs PostgreSQL as -- **never assume
   this is `999:999`**; discover it directly from the image actually
   pinned in `docker-compose.yml`:

   ```sh
   POSTGRES_IMAGE="$(docker compose --env-file .env config --images postgres)"
   echo "$POSTGRES_IMAGE"

   docker run --rm --entrypoint /usr/bin/id "$POSTGRES_IMAGE" -u postgres
   docker run --rm --entrypoint /usr/bin/id "$POSTGRES_IMAGE" -g postgres
   ```

   Both commands print a plain number. These numeric values are what
   matter for the bind-mounted data directory's ownership -- your
   host's own local account names are irrelevant here, even if `id` or
   `ls -la` later displays an unrelated name next to that UID.

5. **Apply ownership and permissions,** using the two numbers from the
   previous step:

   ```sh
   POSTGRES_UID="$(docker run --rm --entrypoint /usr/bin/id "$POSTGRES_IMAGE" -u postgres)"
   POSTGRES_GID="$(docker run --rm --entrypoint /usr/bin/id "$POSTGRES_IMAGE" -g postgres)"

   sudo chown "$POSTGRES_UID:$POSTGRES_GID" data/postgres
   sudo chmod 700 data/postgres
   ```

   `sudo` here changes the ownership and permissions of exactly
   `data/postgres` -- nothing else on the host -- so the PostgreSQL
   container (which runs as that numeric user) can read and write its
   own data directory, while no other user on the host can. Never use
   `chmod 777`, and never apply ownership changes recursively outside
   `data/postgres`.

6. **Validate the Compose configuration:**

   ```sh
   docker compose config --quiet
   ```

   A clean exit with no output means the configuration is valid. You
   can also confirm the expected services are present without printing
   any secret values:

   ```sh
   docker compose config --services
   ```

   Expected output: `postgres`, `web`, `scheduler`.

7. **Pull images:**

   ```sh
   docker compose pull
   ```

8. **Start RidgeNote:**

   ```sh
   docker compose up -d
   docker compose logs -f
   ```

   That's it -- one command starts PostgreSQL, waits for it internally,
   applies any pending database migrations automatically, and then
   starts `web` and `scheduler`. There is no separate manual migration
   step for an ordinary install. Watch `docker compose logs -f` for
   lines like `Startup migration check beginning`, `PostgreSQL
   reachable`, `Applying database migrations`, `Migrations complete`,
   and `Migration lock released` from `web` (and, redundantly and
   harmlessly, from `scheduler` too) before Gunicorn's own startup
   lines appear -- see `docs/RUNBOOK_DEPLOYMENT.md` for exactly how
   this works and what to do if it fails. Press Ctrl+C to stop
   following logs once things look healthy -- the containers keep
   running.

9. **Verify health:**

   ```sh
   curl -fsS http://127.0.0.1:8000/health/
   ```

   Expected response: `{"ok": true, "database": 1}`. Then open
   `RIDGENOTE_EXTERNAL_URL` (your configured LAN URL) in a browser.

10. **Create the first administrator.** Visit
    `<RIDGENOTE_EXTERNAL_URL>/setup/` in a browser and follow the
    first-run setup flow to create the first administrator account,
    then log in normally. This is RidgeNote's existing, unchanged
    bootstrap mechanism -- there is no separate management command,
    and visiting the base URL does not automatically redirect you to
    `/setup/`.

### Optional: automate steps 3-6

> Review the script before running it. It does not install Docker,
> pull or start RidgeNote, run migrations, or create an administrator.

`setup-deploy-host.sh`, if you have a copy of it alongside this
bundle, performs steps 3 through 6 for you:

```sh
./setup-deploy-host.sh \
  --deploy-dir /opt/ridgenote \
  --host 192.0.2.10 \
  --external-url http://192.0.2.10:8000
```

It creates `data/postgres` and `backups/postgres`, derives
PostgreSQL's numeric UID/GID the same way step 4 above does, sets
ownership and restrictive modes, generates a fresh `.env` from
`.env.example` with locally generated secrets (never printed to the
terminal), and validates the Compose configuration. It refuses to run
as root, refuses to overwrite an existing `.env`, and never starts any
service. You still need to set `RIDGENOTE_IMAGE` in `.env` yourself
afterward (step 2 above), and then continue from step 7.

Re-run with `--validate-only` at any time to verify an already-prepared
directory without making any change:

```sh
./setup-deploy-host.sh \
  --deploy-dir /opt/ridgenote \
  --host 192.0.2.10 \
  --external-url http://192.0.2.10:8000 \
  --validate-only
```

This helper is entirely optional -- the manual steps above are the
complete, authoritative installation procedure on their own.

## Image tags

`RIDGENOTE_IMAGE` can be a moving tag such as `latest` (convenient --
always the newest published build, no manual tracking) or a fixed
version/commit-derived tag (reproducible -- you always know exactly
what is running, and rolling back is a matter of switching the tag
back). Either is a reasonable choice for a home/trusted-internal
deployment; pick whichever fits how much you want to track updates
yourself.

## PostgreSQL version

V1 is pinned to PostgreSQL 17. RidgeNote's own automatic startup
migrations are ordinary application/database schema migrations and do
**not** upgrade PostgreSQL itself -- **in-place PostgreSQL
major-version upgrades (e.g. 17 -> 18) are not supported by this
deployment.** A future PostgreSQL major-version change requires its
own documented procedure, not covered by this guide -- see
`docs/RUNBOOK_DEPLOYMENT.md` §7b in the source repository. Always back
up first (see below) before any database-engine upgrade work.

## Back up your deployment

Persistent PostgreSQL data lives entirely under `data/postgres` in
this deployment directory, as a **host bind mount** (not a
Docker-managed named volume). **A truly fresh installation has nothing
to back up yet.** Before any upgrade, database-engine maintenance, or
other maintenance on an existing deployment -- including changing
`RIDGENOTE_DATABASE_NAME`/`_USER`/`_PASSWORD` -- back up first:

```sh
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/ridgenote-backup.dump'
docker compose cp \
  postgres:/tmp/ridgenote-backup.dump \
  "./backups/postgres/ridgenote-backup-$(date +%Y%m%d-%H%M%S).dump"
chmod 600 ./backups/postgres/ridgenote-backup-*.dump
docker compose exec -T postgres rm -f /tmp/ridgenote-backup.dump
```

Confirm each command's exit status before running the next one. This
is a **logical** backup (`pg_dump`, a consistent snapshot taken by
PostgreSQL itself) and does **not** require stopping `web` or
`scheduler`. A raw filesystem copy of `data/postgres` while PostgreSQL
is actively running is a different thing entirely and must not be
treated as equivalent to this logical backup -- it is not documented
or supported here.

**What is and isn't destructive, precisely:**

- `docker compose down` is safe and non-destructive -- it stops the
  containers; `data/postgres` (a host path, not something Compose
  manages) is untouched.
- `docker compose down -v` is **also safe for `data/postgres`
  specifically** in this deployment -- this Compose file defines no
  named Docker volumes at all, only the `data/postgres` bind mount, and
  `-v` only ever removes Docker-managed volumes, never a host bind
  mount. (This differs from RidgeNote's own local-development Compose
  file, which does use a named volume -- don't assume the two behave
  identically.)
- **Manually deleting `data/postgres`** (e.g. `rm -rf data/postgres`),
  or otherwise reinitializing/replacing it, **is destructive** and
  permanently deletes every note, folder, and account. Never do this
  casually, and never without a current backup taken first.

This backup covers RidgeNote's entire database -- it is a different,
lower-level mechanism from the in-app **Library Backup** feature (a
per-user export/restore tool for migration and self-service recovery,
not a substitute for this infrastructure-level backup, and not an
administrator disaster-recovery mechanism). Full backup, restore, and
disaster-recovery procedures -- including how to restore into a fresh
deployment -- live in `docs/RUNBOOK_BACKUP_RESTORE.md` in the source
repository.

## More information

- **Full deployment/operator reference** (topology, complete
  environment-variable reference, reverse-proxy setup, upgrade/
  rollback, troubleshooting): `docs/RUNBOOK_DEPLOYMENT.md`.
- **Backup, restore, and disaster recovery:**
  `docs/RUNBOOK_BACKUP_RESTORE.md`.
- **Trash purge operations (manual and automatic):**
  `docs/RUNBOOK_PURGE.md`.
- **Logs:** `docker compose logs --tail=100 <web|scheduler|postgres>`.
  See `docs/RUNBOOK_DEPLOYMENT.md` for what to look for when
  troubleshooting.
