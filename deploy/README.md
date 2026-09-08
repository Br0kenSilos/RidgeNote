# RidgeNote Quick Deploy

Run RidgeNote from a published container image on a trusted internal
network, without cloning the RidgeNote source repository, installing
Git, or building anything locally. This document is deliberately
short and linear. For topology/CSRF/reverse-proxy/troubleshooting
depth, see `docs/RUNBOOK_DEPLOYMENT.md` in the source repository. For
the full environment-variable reference, see `docs/CONFIGURATION.md`.

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
  - `env.example`
  - `README.md` (this file)

  No RidgeNote source checkout, no Git, and no local image build are
  needed at any point -- the application image is pulled from GHCR
  (GitHub Container Registry), publicly and anonymously. An
  optional fourth file, `setup-deploy-host.sh`, can automate part of
  the sequence below -- see "Optional: automate steps 4-5" after the
  sequence. It is a convenience, never a requirement.

## Quick Deploy

1. **Create the deployment directory** and obtain the three files
   above into it. `docker-compose.yml`, `env.example`, and this
   `README.md` are distributed together as the RidgeNote deployment
   bundle -- download or copy all three from wherever you obtained
   RidgeNote (for example, the assets attached to a RidgeNote release
   on the project's GitHub page) into a single directory:

   ```sh
   mkdir -p /opt/ridgenote
   cd /opt/ridgenote
   ```

   The path above is only an example -- use whatever directory makes
   sense on your host. Every command below assumes you are running it
   from this directory unless stated otherwise.

2. **Prepare `.env`:**

   ```sh
   cp env.example .env
   ```

   `env.example` is organized into exactly two sections -- REQUIRED
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

   Then edit `.env` and set the remaining required value:

   ```dotenv
   RIDGENOTE_ALLOWED_HOSTS=192.0.2.10,127.0.0.1
   ```

   **You must replace `192.0.2.10` with the actual host name(s) or
   IP address(es) you will use to reach this RidgeNote server** -- the
   shipped example value is a documentation-only placeholder and will
   not match your real deployment. Leaving it unreplaced is the most
   common first-run mistake: RidgeNote will reject the request with
   HTTP 400 the moment you try to open it in a browser. **Keep
   `127.0.0.1` in the list** -- the packaged Docker health check always
   requests `http://127.0.0.1:8000/health/` from inside the container,
   and removing `127.0.0.1` makes the web container report unhealthy
   even though RidgeNote itself is running fine. Do not use a wildcard
   host.

   Two related settings are OPTIONAL and can be left blank for this
   plain-HTTP, direct-LAN topology: `RIDGENOTE_CSRF_TRUSTED_ORIGINS`
   (only matters behind a reverse proxy serving RidgeNote over HTTPS)
   and `RIDGENOTE_EXTERNAL_URL` (only affects the link inside
   automatically-sent invitation emails -- see "Email handling" in
   `docs/CONFIGURATION.md`; ordinary browsing, login, and manual
   invitation links never use it).

   RidgeNote's published image is public and does not require registry
   authentication; the step below applies only if you are using your
   own private mirror, fork, or other private image. If the image
   package is private, authenticate before pulling:

   ```sh
   docker login registry.example.com
   ```

   Use a registry access token with package-read scope as the
   password, not your account password. `docker logout
   registry.example.com` afterward if this is a shared or temporary
   session. If anonymous pull already works on your network, this
   step is unnecessary.

   Leave the remaining shipped defaults (`RIDGENOTE_DEBUG=0`,
   `RIDGENOTE_PURGE_ENABLED=true`,
   `RIDGENOTE_TRUST_X_FORWARDED_PROTO=false`) unchanged unless you have
   a specific, understood reason to change them -- the latter only
   needs attention behind a reverse proxy; see
   `docs/RUNBOOK_DEPLOYMENT.md`.

3. **Secure the file:**

   ```sh
   chmod 600 .env
   ```

4. **Create and prepare PostgreSQL's persistent storage.** The
   published PostgreSQL image runs as a fixed numeric user/group,
   `999:999` -- the bind-mounted data directory must be owned by that
   exact numeric user/group before the container starts, or PostgreSQL
   will fail to initialize:

   ```sh
   mkdir -p data/postgres backups/postgres
   chmod 755 data
   chmod 700 backups backups/postgres

   sudo chown 999:999 data/postgres
   sudo chmod 700 data/postgres
   ls -ldn data/postgres
   ```

   The last command's output should show numeric owner and group
   `999 999`. `sudo` here changes the ownership and permissions of
   exactly `data/postgres` -- nothing else on the host -- so the
   PostgreSQL container can read and write its own data directory,
   while no other user on the host can. Never use `chmod 777`, and
   never apply ownership changes recursively outside `data/postgres`.

5. **Validate the Compose configuration:**

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

6. **Pull images:**

   ```sh
   docker compose pull
   ```

7. **Start RidgeNote:**

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

8. **Verify health:**

   ```sh
   curl -fsS http://127.0.0.1:8000/health/
   ```

   Expected response: `{"ok": true, "database": 1}`. Then open
   `RIDGENOTE_EXTERNAL_URL` (your configured LAN URL) in a browser.

9. **Create the first administrator.** Visit
   `<RIDGENOTE_EXTERNAL_URL>/setup/` in a browser and follow the
   first-run setup flow to create the first administrator account,
   then log in normally. This is RidgeNote's existing, unchanged
   bootstrap mechanism -- there is no separate management command,
   and visiting the base URL does not automatically redirect you to
   `/setup/`.

### Optional: automate steps 4-5

> Review the script before running it. It does not install Docker,
> pull or start RidgeNote, run migrations, or create an administrator.

`setup-deploy-host.sh`, if you have a copy of it alongside this
bundle, performs steps 4 and 5 for you:

```sh
./setup-deploy-host.sh \
  --deploy-dir /opt/ridgenote \
  --host 192.0.2.10 \
  --external-url http://192.0.2.10:8000
```

It creates `data/postgres` and `backups/postgres`, applies the
`999:999` PostgreSQL ownership and restrictive modes, generates a
fresh `.env` from `env.example` with locally generated secrets (never
printed to the terminal), and validates the Compose configuration. It
refuses to run as root, refuses to overwrite an existing `.env`, and
never starts any service. Continue from step 6 afterward.

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

The shipped `docker-compose.yml` uses `ghcr.io/br0kensilos/ridgenote:v1.0.1`
directly for both `web` and `scheduler` -- a fixed, reproducible
version tag: you always know exactly what is running, and rolling
back is a matter of restoring the prior `docker-compose.yml`. If you
deliberately prefer to track the newest published image instead,
change both `image:` lines to `ghcr.io/br0kensilos/ridgenote:latest`.
This is an optional, manual operator choice -- RidgeNote does not ship
any automatic-update tooling, so a `latest` deployment only ever
updates when you next run `docker compose pull`.

## PostgreSQL version

V1 uses `postgres:17-bookworm` -- pinned to PostgreSQL major version
17, while still receiving ordinary PostgreSQL 17.x image/security
updates on every `docker compose pull`. RidgeNote's own automatic
startup migrations are ordinary application/database schema
migrations and do **not** upgrade PostgreSQL itself -- **in-place
PostgreSQL major-version upgrades (e.g. 17 -> 18) are not supported by
this deployment.** A future PostgreSQL major-version change requires
its own documented procedure, not covered by this guide -- see
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

- **Environment/configuration variable reference:**
  `docs/CONFIGURATION.md`.
- **Full deployment/operator reference** (topology, reverse-proxy
  setup, upgrade/rollback, troubleshooting): `docs/RUNBOOK_DEPLOYMENT.md`.
- **Backup, restore, and disaster recovery:**
  `docs/RUNBOOK_BACKUP_RESTORE.md`.
- **Trash purge operations (manual and automatic):**
  `docs/RUNBOOK_PURGE.md`.
- **Logs:** `docker compose logs --tail=100 <web|scheduler|postgres>`.
  See `docs/RUNBOOK_DEPLOYMENT.md` for what to look for when
  troubleshooting.
