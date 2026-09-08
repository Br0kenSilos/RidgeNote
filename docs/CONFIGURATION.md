# RidgeNote Configuration

RidgeNote is configured entirely through environment variables
(`RIDGENOTE_*`) -- no other configuration mechanism exists. This is
the authoritative, detailed reference for every variable the shipped
standalone deployment (`deploy/`) uses.

- Normal users start with `deploy/env.example` -- copy it to `.env`
  and edit the values you need. See `deploy/README.md` for the linear
  Quick Deploy procedure.
- **Required** values have no safe default; you must deliberately set
  each one for a real deployment.
- **Optional** values already have a safe, working default; leave them
  alone unless your deployment specifically needs otherwise.
- Infrastructure/runtime settings (host, database credentials, session
  and login-security tuning, Trash purge behavior, and so on) remain
  environment-driven in V1 -- there is no in-app administration screen
  for any of these.

## Variable reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `RIDGENOTE_SECRET_KEY` | Yes | *(none)* | Django's cryptographic secret key. Generate a strong random value; never reuse the development default. |
| `RIDGENOTE_DATABASE_PASSWORD` | Yes | *(none)* | The single database password, used for both RidgeNote's own connection and PostgreSQL's own initialization. |
| `RIDGENOTE_ALLOWED_HOSTS` | Yes | *(none)* | Comma-separated host names/IPs RidgeNote will accept requests for. Must include the exact host(s) clients use, and `127.0.0.1` for the packaged health check. |
| `RIDGENOTE_EXTERNAL_URL` | No | empty | The full external URL (`scheme://host[:port]`, no path) users reach RidgeNote through. Blank is valid for a direct-LAN deployment. |
| `RIDGENOTE_CSRF_TRUSTED_ORIGINS` | Only behind an HTTPS reverse proxy | empty | Comma-separated full origins (scheme + host, e.g. `https://notes.example.com`) Django trusts for CSRF-protected HTTPS requests. |
| `RIDGENOTE_TRUST_X_FORWARDED_PROTO` | No | `false` | Trust a reverse proxy's `X-Forwarded-Proto` header. Enable only when the backend port is reachable exclusively through that proxy. |
| `RIDGENOTE_USE_X_FORWARDED_HOST` | No | `false` | Trust a reverse proxy's `X-Forwarded-Host` header for `request.get_host()`. Most single-hostname, root-path proxies do not need this. |
| `RIDGENOTE_DEBUG` | No | `0` | Django debug mode. Never `1` in a real deployment -- it can expose detailed internal/error information. |
| `RIDGENOTE_LOG_LEVEL` | No | `INFO` | Any standard Python logging level name. Change only for troubleshooting. |
| `RIDGENOTE_DATABASE_NAME` | No | `ridgenote` | PostgreSQL database name. Also derives PostgreSQL's own `POSTGRES_DB` -- nothing to keep in sync by hand. |
| `RIDGENOTE_DATABASE_USER` | No | `ridgenote` | PostgreSQL user name. Also derives PostgreSQL's own `POSTGRES_USER`. |
| `RIDGENOTE_DATABASE_PORT` | No | `5432` | PostgreSQL port, as seen by the `web`/`scheduler` containers. |
| `RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD` | No | `5` | Number of failed login attempts, within the window below, before an account is temporarily locked. |
| `RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS` | No | `900` | The rolling time window failed attempts are counted within. |
| `RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS` | No | `900` | How long an account stays locked once the threshold is reached. |
| `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS` | No | `3600` | How long an authenticated session may sit idle before it expires. |
| `RIDGENOTE_SESSION_WARNING_SECONDS` | No | `300` | How long before idle expiry the in-app warning appears. Must be strictly less than the idle timeout above -- RidgeNote fails to start otherwise. |
| `RIDGENOTE_INVITATION_EXPIRY_MINUTES` | No | `120` | How long an administrator-issued invitation link stays valid before it must be reissued. |
| `RIDGENOTE_PURGE_ENABLED` | No | `true` | Whether the `scheduler` service automatically purges Trash items whose retention window has ended. |
| `RIDGENOTE_PURGE_INTERVAL_SECONDS` | No | `86400` | How often the scheduler *checks* for eligible items (86400 = once per day) -- see "Trash purge" below for why this is not a retention period. |
| `RIDGENOTE_SMTP_HOST` | No | empty | SMTP server hostname. Leave blank (with the rest of the SMTP block) for manual-copy-only invitation delivery. |
| `RIDGENOTE_SMTP_PORT` | No | empty | SMTP port, e.g. `587` (STARTTLS), `465` (SSL). Never inferred from the TLS mode below. |
| `RIDGENOTE_SMTP_TLS_MODE` | No | empty (`none`) | One of `none` / `starttls` / `ssl`. Any other value fails at startup. |
| `RIDGENOTE_SMTP_USERNAME` | No | empty | SMTP username. Leave both this and the password blank for an unauthenticated relay. |
| `RIDGENOTE_SMTP_PASSWORD` | No | empty | SMTP password. Setting only one of username/password is rejected at startup. |
| `RIDGENOTE_SMTP_FROM_EMAIL` | No | empty | The From address for automatically-sent invitation emails. Required once any other SMTP variable is set. |
| `RIDGENOTE_SMTP_FROM_NAME` | No | empty | Optional display name for the From address, e.g. `RidgeNote`. Blank uses the bare From address alone. |

## Secret key

`RIDGENOTE_SECRET_KEY` is Django's cryptographic secret, used for
session signing and other security-sensitive hashing. Generate a
unique, random value for every deployment -- `openssl rand -hex 48`
produces an appropriate one. RidgeNote refuses to start with debug
mode off and no real secret key set.

## Allowed hosts

`RIDGENOTE_ALLOWED_HOSTS` is a comma-separated list of the exact host
names or IP addresses RidgeNote will accept requests for -- any
request whose `Host` header doesn't match is rejected with HTTP 400.
The shipped example value is a documentation-only placeholder; it must
be replaced with the real host name(s)/IP address(es) your users will
actually type into a browser. `127.0.0.1` must remain in the list
regardless -- the packaged Docker health check always requests
`http://127.0.0.1:8000/health/` from inside the container, and
removing it makes the `web` container report unhealthy even though
RidgeNote itself is running fine. Wildcard hosts are not supported.

## External URL

`RIDGENOTE_EXTERNAL_URL` is the full URL (scheme, host, optional port,
no path) users reach RidgeNote through. It is used when RidgeNote
needs to generate an absolute link back to itself -- most notably, the
link inside an automatically-sent invitation email (see "SMTP" below).
Ordinary browsing, login, and the administrator's manual one-time
invitation Copy Link never use this value; it is safe to leave blank
for a direct-LAN deployment that doesn't use automatic email delivery.

## Reverse proxy / CSRF settings

`RIDGENOTE_CSRF_TRUSTED_ORIGINS`, `RIDGENOTE_TRUST_X_FORWARDED_PROTO`,
and `RIDGENOTE_USE_X_FORWARDED_HOST` only matter when RidgeNote sits
behind a reverse proxy terminating HTTPS -- all three can be left at
their defaults for a direct, plain-HTTP LAN deployment. See
`docs/RUNBOOK_DEPLOYMENT.md` §6 for the full reverse-proxy topology,
required proxy contract, and a security warning about
`RIDGENOTE_TRUST_X_FORWARDED_PROTO`.

## Debug / logging

`RIDGENOTE_DEBUG` must stay `0` in any real deployment -- Django's
debug mode can expose detailed internal/error information to anyone
who can trigger an error page. `RIDGENOTE_LOG_LEVEL` accepts any
standard Python logging level name and is not validated against a
fixed list; change it only when troubleshooting.

## Database settings

`RIDGENOTE_DATABASE_NAME`/`_USER` also derive PostgreSQL's own
internal `POSTGRES_DB`/`POSTGRES_USER` in the shipped Compose file --
there is nothing else to set or keep in sync by hand.
`RIDGENOTE_DATABASE_PASSWORD` is likewise the single password used by
both RidgeNote's own connection and PostgreSQL's own initialization.
`RIDGENOTE_DATABASE_PORT` is the port PostgreSQL listens on inside the
Compose network; the host (`RIDGENOTE_DATABASE_HOST`) is fixed to
`postgres` in the shipped Compose file and is not operator-configurable
there.

## Login lockout

`RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD` failed attempts within
`RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS` locks the account for
`RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS`. The shipped defaults (5
attempts / 15-minute window / 15-minute lockout) are safe as-is for
ordinary household/small-team use.

## Session idle timeout / warning

`RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS` (default `3600`, one hour)
controls how long an authenticated session may sit idle before it
expires; only genuine, server-recognized activity (page navigation,
autosave from real typing, form submissions, and similar) extends it
-- background polling does not. `RIDGENOTE_SESSION_WARNING_SECONDS`
(default `300`, five minutes) controls how long before that deadline
the in-app idle warning appears. **`RIDGENOTE_SESSION_WARNING_SECONDS`
must be strictly less than `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS`**
-- RidgeNote raises an error and refuses to start otherwise.

## Trash purge

`RIDGENOTE_PURGE_ENABLED` (default `true`) controls whether the
`scheduler` service automatically purges Trash items once their
retention window has ended. **`RIDGENOTE_PURGE_INTERVAL_SECONDS`
(default `86400`) is how often the scheduler *checks* for eligible
items, not how long an item stays in Trash before it becomes
eligible** -- retention itself is a separate, existing application
behavior this variable does not configure. See
`docs/RUNBOOK_PURGE.md` for full purge operational detail.

## Invitation expiry

`RIDGENOTE_INVITATION_EXPIRY_MINUTES` (default `120`) is how long an
administrator-issued invitation link stays valid before it must be
reissued.

## SMTP settings

Every `RIDGENOTE_SMTP_*` variable is optional. RidgeNote's invitation
workflow never requires SMTP -- an invited account's one-time link can
always be copied from the browser and delivered manually by the
administrator. Leaving all SMTP variables blank (the default) is a
fully normal deployment shape, not a degraded one. Setting **all** of
`RIDGENOTE_SMTP_HOST`, `RIDGENOTE_SMTP_PORT`, `RIDGENOTE_SMTP_FROM_EMAIL`,
and `RIDGENOTE_EXTERNAL_URL` additionally makes an optional
automatic-email control available on Create User (Invitation mode) and
on Reissue/Issue invitation.

`RIDGENOTE_SMTP_USERNAME`/`RIDGENOTE_SMTP_PASSWORD` may both be left
blank for an unauthenticated relay that permits it; setting only one
of the two is rejected at startup as likely-accidental
half-configuration. `RIDGENOTE_SMTP_FROM_EMAIL` is required once any
other SMTP variable is set. See `docs/RUNBOOK_DEPLOYMENT.md` §3a for
deeper operational nuance (manual vs. automatically-emailed link
origins, and the reverse-proxy interaction caveat).
