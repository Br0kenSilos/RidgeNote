# RidgeNote V1 Release Notes

## Release status

RidgeNote v1.0.0 was the first public release, tagged `v1.0.0` and
built from commit `12d948e`. RidgeNote v1.0.1, tagged `v1.0.1` and
built from commit `257eee0`, fixed an authoritative
session-idle/session-expiry defect found during real deployment
rehearsal (v1.0.0 did not include this fix) and included the finalized
standalone deployment package (`deploy/` Compose bundle, Quick Deploy
procedure, and [`docs/CONFIGURATION.md`](CONFIGURATION.md)).

**RidgeNote v1.0.2 is the next upcoming V1 patch release**, currently
being finalized from this repository state. It tightens editor
typography/paragraph spacing toward a more compact, working-notepad
feel, and adds an explicit `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS=0`
("never expire") sentinel with corresponding configuration-reference
updates. It requires no migration or manual database action and
introduces no breaking deployment changes. It is not yet published as
of this text.

The canonical public source repository is
[`https://github.com/Br0kenSilos/RidgeNote`](https://github.com/Br0kenSilos/RidgeNote).
The canonical release image is `ghcr.io/br0kensilos/ridgenote:v1.0.1`,
with equivalent currently published tags `sha-257eee0` (immutable
per-build provenance) and `latest` (current stable convenience tag).
All three currently resolve to digest
`sha256:f115caff1b9e41ea000881ff2cc26cad1925878ab47595a6fd1b78a99da7c517`,
built for `linux/amd64`. Anonymous (unauthenticated) GHCR pull access
has been verified for this image.

A GitHub Release for v1.0.1 is published at
[`github.com/Br0kenSilos/RidgeNote/releases/tag/v1.0.1`](https://github.com/Br0kenSilos/RidgeNote/releases/tag/v1.0.1)
and is marked as the repository's current/latest release. It carries
five assets -- `docker-compose.yml`, `env.example`, `README.md`,
`ridgenote-v1.0.1-deploy.zip`, and `SHA256SUMS` -- reflecting Quick
Deploy as finalized after two successful Docker2 rehearsal
deployments; the assets were refreshed from that finalized package and
verified by downloading them back and confirming checksum and
byte-for-byte identity against the published source.

**The source repository is now public.** Anonymous, unauthenticated
access has been verified end to end: the repository and this release
itself, the `/releases/latest/download/docker-compose.yml`,
`/releases/latest/download/env.example`, and
`/releases/latest/download/README.md` asset URLs, and anonymous GHCR
pull access for the `v1.0.1`, `sha-257eee0`, and `latest` tags (all
three still resolving to digest
`sha256:f115caff1b9e41ea000881ff2cc26cad1925878ab47595a6fd1b78a99da7c517`).
A final fresh install using only the public Quick Deploy path --
downloading the release assets anonymously rather than using
working-tree copies -- completed successfully end to end. **Public V1
publication and deployment acceptance is complete.**

**Release immutability has deliberately not been enabled yet.** The
owner plans to revisit enabling it only after an upcoming production
installation, so this remains an intentional, still-open step -- not
an oversight.

## What RidgeNote V1 is

RidgeNote is a self-hosted, browser-based, multi-user notes application
built on Django, PostgreSQL, and Docker Compose. It is intentionally a
lightweight scratch-pad/note application -- fast, simple, and private by
default -- not a document-versioning system.

## Major capabilities

- Rich-text note creation and editing, with autosave, owner-scoped
  freshness checks, and version-aware conflict-safe saves.
- One-level folders and a workspace tree with expand/collapse and
  filtering.
- Home, Recent notes, Quick Switch, and owner-scoped Global Search.
- A Dedicated Tag System: creation, semantic default colors, an
  autocomplete picker, a Tag Manager, and per-user tag preferences.
- A dedicated, filterable **Full Search** page (text, tag, folder, and
  lifecycle scopes), backed by PostgreSQL full-text search.
- **Trash and recovery**: owner restore, an administrator recovery view
  for items past the owner-visible window, and automatic scheduled
  final purge.
- **Account administration**: invitations and onboarding (with optional
  SMTP delivery), role management, account disable/restore, scheduled
  and cancelable deletion, and permanent purge.
- Per-user timezone preferences and eight built-in themes (Warm Light,
  Dark, Glacier, Granite, Alpine Mist, Blue Dusk, Midnight Ridge,
  Nightfall).
- Print and plain-text/Markdown download for individual notes.
- Downloadable, application-level **Library Backup**, with a validated
  preview-then-confirm full-library restore.
- An in-app **Help** system covering all major end-user and
  administrator-visible workflows.
- A proven standalone Docker Compose deployment path: a non-root
  runtime image, automatic startup migrations, and a linear Quick
  Deploy guide.

## Deployment model

- Docker Compose, three services: `web`, `scheduler`, and `postgres`.
- PostgreSQL 17 (`postgres:17-bookworm`, a floating tag within major
  17, receiving ordinary 17.x image/security updates on every
  `docker compose pull`).
- Database migrations are applied **automatically at startup** by both
  `web` and `scheduler`, coordinated by a PostgreSQL advisory lock --
  there is no separate manual migration step in the normal install or
  upgrade flow.
- Persistent PostgreSQL data lives in a host bind mount, not an
  ephemeral container filesystem.
- V1 container images are built and tested for **`linux/amd64`** only.
- Canonical public source: `https://github.com/Br0kenSilos/RidgeNote`.
  Canonical public image: `ghcr.io/br0kensilos/ridgenote`, current V1
  tag `v1.0.1` -- see "Release status" above.

See [`deploy/README.md`](../deploy/README.md) for the linear Quick
Deploy procedure and [`docs/RUNBOOK_DEPLOYMENT.md`](RUNBOOK_DEPLOYMENT.md)
for the full operator reference.

## Backup / recovery warning

**Infrastructure/database backup remains required.** RidgeNote's
in-app **Library Backup** feature is a per-user, library-scoped
export/import tool intended for user-level migration and self-service
recovery -- it is **not** a substitute for whole-instance disaster
recovery, and it is not an administrator backup mechanism. Operators
are responsible for their own PostgreSQL-level or host/VM-level backup
strategy, and should **back up before any upgrade or destructive
maintenance** (see
[`docs/RUNBOOK_BACKUP_RESTORE.md`](RUNBOOK_BACKUP_RESTORE.md)).

## Supported / tested environment

- **Host OS/Docker/Compose:** validated against Ubuntu 24.04 LTS with a
  current Docker Engine and Compose plugin; any reasonably current
  Docker installation is expected to work.
- **CPU architecture:** `linux/amd64` only. arm64/aarch64 (including
  Raspberry Pi or Apple Silicon hosts) is not currently built or
  validated -- do not assume compatibility.
- **Browsers:** tested on current Chromium-family browsers and Firefox,
  across desktop, tablet, and phone viewport widths. Safari has not
  been formally validated.
- **Responsive layouts:** exercised and accepted across wide, medium,
  and narrow widths through both dedicated per-feature browser
  acceptance during development and functional acceptance evidence at
  release-candidate time.
- **External/public exposure**: RidgeNote includes practical
  application-level protections (account controls, login lockouts,
  session handling, ownership isolation, CSRF protection, and related
  safeguards). A documented reverse-proxy/HTTPS deployment path covers
  the configuration needed for external exposure (see
  [`docs/RUNBOOK_DEPLOYMENT.md`](RUNBOOK_DEPLOYMENT.md)); broader
  Internet-facing hardening beyond a properly configured reverse
  proxy -- general rate limiting, perimeter/WAF controls, and similar --
  was not a primary V1 design goal and remains the operator's own
  responsibility.

## Upgrade expectations

- Ordinary RidgeNote upgrades are **image-based**: pull the new image
  tag and recreate the `web`/`scheduler` services.
- Startup migrations are automatic -- no separate migration command is
  part of the normal upgrade flow.
- **PostgreSQL major-version upgrades are a separate concern and are
  not supported in-place** by the V1 Compose deployment; a future major
  PostgreSQL version change requires its own, not-yet-documented
  procedure.
- **Back up before upgrading.** Automatic migrations only apply
  ordinary, backward-compatible schema changes; any future release
  requiring special upgrade handling will say so explicitly in that
  release's own notes.

## Known limitations / deferred items

- arm64/aarch64 images are not built or validated for V1.
- Safari is not formally validated.
- No PostgreSQL major-version upgrade procedure is provided yet.
- Broader public-exposure security hardening (beyond a properly
  configured reverse proxy) -- general rate limiting, perimeter/WAF
  controls, and similar -- is not yet provided and remains a documented
  gap for a future release.
- Grouped folder-and-notes restore, individual/manual permanent note
  delete, editable account identity fields (display name, username,
  email self-service), Tag merge/bulk recolor, and self-service
  password reset are not part of V1.
- Per-user session idle timeout preferences are deferred future work --
  `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS` (including its `0`/"never"
  setting) remains a single global server setting applying to all
  users; it is not implemented as a per-user preference in this
  version.

## Acceptance evidence

A fresh full acceptance pass against current `main` and a fresh
standalone deployment rehearsal on a dedicated rehearsal host were both
completed as part of V1 release readiness. Both concluded **PASS WITH
NON-BLOCKING OBSERVATIONS** -- no known unresolved V1 defect. The
acceptance pass's only observation was an environment-tooling
limitation (no live browser automation available in that pass's
execution environment, substituted with an extensive functional pass
plus confirmation that no frontend file had changed since the last
real browser-verified state); the rehearsal's only observations were
about its own delivery-mechanism substitutions, not about any
deployment defect. Full evidence:
[`docs/ACCEPTANCE_RESULTS_V1.md`](ACCEPTANCE_RESULTS_V1.md). No private
host or network detail is recorded in that document.

## License / notices

RidgeNote is licensed under the [Apache License 2.0](../LICENSE). See
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) for the
third-party dependency and license register, and
[`reports/`](../reports/) for dependency/SBOM reports.
