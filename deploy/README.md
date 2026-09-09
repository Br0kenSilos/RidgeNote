# RidgeNote Quick Deploy

Run RidgeNote from a published container image on a trusted internal
network, without cloning the RidgeNote source repository, installing
Git, or building anything locally. This document is deliberately
short and linear. For topology/CSRF/reverse-proxy/troubleshooting
depth, see `docs/RUNBOOK_DEPLOYMENT.md` in the source repository. For
the full environment-variable reference, see `docs/CONFIGURATION.md`.

## Before you begin

- An amd64 based host running Docker Engine and a current Docker Compose (the
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
  do this by default; I recommend you keep it that way.
- Approximately 2 GiB RAM available is sufficient for RidgeNote plus
  PostgreSQL at this scale. It may run fine on less but has not been
  tested that way.
- You need exactly these three files, and nothing else:
  - `docker-compose.yml`
  - `env.example`
  - `README.md` (this file)

  No RidgeNote source checkout, no Git, and no local image build are
  needed at any point -- the application image is pulled from GHCR
  (GitHub Container Registry), publicly and anonymously.

## Quick Deploy

1. **Create and enter the deployment directory**

   ```sh
   mkdir -p ~/ridgenote
   cd ~/ridgenote
   ```

   The path above is only an example -- use whatever directory makes
   sense on your host. Every command below assumes you are running it
   from this directory unless stated otherwise.

2. **Download the RidgeNote deployment files:**

   ```sh
   curl -fLO https://github.com/Br0kenSilos/RidgeNote/releases/latest/download/docker-compose.yml
   curl -fLO https://github.com/Br0kenSilos/RidgeNote/releases/latest/download/env.example
   curl -fLO https://github.com/Br0kenSilos/RidgeNote/releases/latest/download/README.md
   ```

3. **Prepare `.env`:**

   ```sh
   cp env.example .env
   ```


   Generate the two required secrets. Copy each value somewhere temporarily so you can paste it into .env:


   ```sh
   openssl rand -hex 48
   ```


   ```sh
   openssl rand -hex 32
   ```

   `env.example` is organized into exactly two sections; REQUIRED
   (no safe default included; you must set each one) and OPTIONAL (already has
   a working default). So you can see at a glance what actually
   needs your attention.


   Edit the .env file with your preferred text editor, for example: nano, vim, vi.
   ```sh
   nano .env
   ```

   and set the remaining required values:

   Paste the first value into the  `.env` file as the `RIDGENOTE_SECRET_KEY` value.

   Paste the second value into the  `.env` file as the `RIDGENOTE_DATABASE_PASSWORD`
   value. This one value is used for both RidgeNote's own database connection
   and PostgreSQL's own initialization; `docker-compose.yml` wires them
   together, so there is nothing else to set or match by hand.

   Set `RIDGENOTE_ALLOWED_HOSTS`:
   ```dotenv
   RIDGENOTE_ALLOWED_HOSTS=192.0.2.10,127.0.0.1
   ```

   **You must replace `192.0.2.10` with the actual host name(s) or
   IP address(es) you will use to reach this RidgeNote server** -- the
   example value is a documentation-only placeholder and will
   not match your real deployment. Leaving it unreplaced is the most
   common first-run mistake: RidgeNote will reject the request with
   HTTP 400 the moment you try to open it in a browser. **Keep
   `127.0.0.1` in the list** as the packaged Docker health check always
   requests `http://127.0.0.1:8000/health/` from inside the container,
   and removing `127.0.0.1` makes the web container report unhealthy
   even though RidgeNote itself is running fine. Do not use a wildcard
   host.

   There are two related settings that are `OPTIONAL` and can be left
   blank for this plain-HTTP, direct-LAN topology:

   `RIDGENOTE_CSRF_TRUSTED_ORIGINS` - only matters when RidgeNote will
   be behind a reverse proxy serving RidgeNote over HTTPS.

   `RIDGENOTE_EXTERNAL_URL` - only affects the link inside automatically-sent
   invitation emails.

   See "Email handling" in `docs/CONFIGURATION.md`;
   ordinary browsing, login, and manual invitation links never use it.


   Leave the remaining defaults (`RIDGENOTE_DEBUG=0`,
   `RIDGENOTE_PURGE_ENABLED=true`,
   `RIDGENOTE_TRUST_X_FORWARDED_PROTO=false`) unchanged unless you have
   a specific, understood reason to change them. The latter only
   needs attention behind a reverse proxy; see
   `docs/RUNBOOK_DEPLOYMENT.md` for more info.

4. **Secure the .env file:**

   ```sh
   chmod 600 .env
   ```

5. **Create and prepare PostgreSQL's persistent storage.** The
   published PostgreSQL image runs as a fixed numeric user/group,
   `999:999` -- the bind-mounted data directory must be owned by that
   exact numeric user/group before the container starts, or PostgreSQL
   will fail to initialize:

   ```sh
   mkdir -p data/postgres
   sudo chown 999:999 data/postgres
   sudo chmod 700 data/postgres
   ls -ldn data/postgres
   ```

   The last command's output should show numeric owner and group
   `999 999`. `sudo` here changes the ownership and permissions of
   only the `data/postgres` folder and nothing else on the host
   so the PostgreSQL container can read and write its own data directory,
   while no other user on the host can. Never use `chmod 777`, and
   never apply ownership changes recursively outside `data/postgres`.

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
   ```

   That's it! One command starts PostgreSQL, applies any pending
   database migrations automatically, and starts RidgeNote's web and
   scheduler services. There is no separate manual migration step for an
   ordinary installation.

   To watch startup:

   ```sh
   docker compose logs -f
   ```

   Press Ctrl+C when startup is complete. The containers continue running.
   See `docs/RUNBOOK_DEPLOYMENT.md` if startup fails or for detailed
   startup and migration behavior.

9. **Verify health:**

   ```sh
   curl -fsS http://127.0.0.1:8000/health/
   ```

   Expected response: `{"ok": true, "database": 1}`.
   Then open `http://<HOST-IP-ADDRESS>:8000` in a browser.

10. **Create the first administrator.**

    Visit:    `http://<HOST-IP-ADDRESS>:8000`

    On a fresh installation with no existing users, RidgeNote will automatically redirect you to the first-run administrator setup page.

    Follow the setup flow to create the first administrator account, then log in normally.

---

## NOTES

### Image tags
The shipped `docker-compose.yml` uses a fixed RidgeNote release tag
for both `web` and `scheduler`. This makes the deployment reproducible:
you always know exactly what version is running, and rolling back is a
matter of restoring the previous image tag.

If you deliberately prefer to track the newest published image instead,
change both the web and scheduler `image:` lines to `ghcr.io/br0kensilos/ridgenote:latest`.
This is an optional, manual operator choice. RidgeNote does not ship
any automatic-update tooling, so a `latest` deployment only
updates when you next run `docker compose pull`.

---

### PostgreSQL version
V1 uses `postgres:17-bookworm`. It is pinned to PostgreSQL major version
17, while still receiving ordinary PostgreSQL 17.x image/security
updates on every `docker compose pull`. RidgeNote's own automatic
startup migrations are ordinary application/database schema
migrations and do **not** upgrade PostgreSQL itself. **In-place
PostgreSQL major-version upgrades (e.g. 17 -> 18) are not supported by
this deployment.** A future PostgreSQL major-version change requires
its own documented procedure, which is not covered by this guide. See
`docs/RUNBOOK_DEPLOYMENT.md` §7b in the source repository.
Always back up first; see `docs/RUNBOOK_BACKUP_RESTORE.md`.

---
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
