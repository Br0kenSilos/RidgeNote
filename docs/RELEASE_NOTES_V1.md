# RidgeNote V1 Release Notes

## Release status

This document describes the **V1 release candidate** -- the state of
`main` after all release-readiness work (independent review, repository
sanitation, release/operator documentation, a fresh full acceptance
pass, and a fresh standalone deployment rehearsal) is complete. The
final canonical public source and image coordinates have been chosen
(GitHub and GitHub Container Registry, see "Deployment model" below),
and private staging toward them is underway -- but **no public V1 tag,
public GitHub release, or public repository/package visibility exists
yet.** Public visibility is pending a separate owner presentation
review and explicit approval. This document will be finalized once
that happens; nothing here should be read as claiming the release has
already shipped.

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
- PostgreSQL 17, pinned by digest.
- Database migrations are applied **automatically at startup** by both
  `web` and `scheduler`, coordinated by a PostgreSQL advisory lock --
  there is no separate manual migration step in the normal install or
  upgrade flow.
- Persistent PostgreSQL data lives in a host bind mount, not an
  ephemeral container filesystem.
- V1 container images are built and tested for **`linux/amd64`** only.
- Canonical public source: `https://github.com/Br0kenSilos/RidgeNote`.
  Canonical public image: `ghcr.io/br0kensilos/ridgenote`, primary V1
  tag `v1.0.0`. Both are staged privately as of this document and are
  **not yet publicly visible** -- see "Release status" above.

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
- The final public registry/source coordinates are chosen (see
  "Deployment model" above) but not yet publicly visible -- private
  staging and an owner presentation review are still pending public
  release.

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
