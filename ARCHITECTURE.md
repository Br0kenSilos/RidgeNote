# RidgeNote Architecture

This document explains how RidgeNote is put together and why, for
anyone reading the source, considering a fork, or wondering why a
particular design choice was made. It covers durable structure, not
a feature list (see `README.md`) or operational procedure (see the
runbooks under `docs/`).

## Application Structure

RidgeNote is a **Django modular monolith**: server-rendered pages,
with focused TypeScript for interactive pieces (chiefly the rich-text
editor), all in one application rather than a separate
frontend/backend deployment. This fits an app that is primarily
authenticated data management with real recovery, retention,
administration, and concurrency rules — Django's mature session,
ORM, and migration tooling cover those concerns without extra moving
parts. A separate SPA/API split, or a full microservices split, was
deliberately avoided; both would add deployment and coordination
overhead this application doesn't need.

**PostgreSQL is the sole authoritative store.** Every user, note,
folder, tag, and audit event lives there. RidgeNote needs
transactional updates, ownership constraints, and safe multi-user
concurrency — a single relational database gives all of that without
introducing a second store to keep in sync. Full-text search also
runs directly in PostgreSQL rather than a separate search service.

**Background work runs as a separate `scheduler` process**, built
from the same application image as the web process, running a
management command instead of serving HTTP. It handles retention/
purge cleanup on a timer. This keeps RidgeNote portable across
ordinary Docker hosts without depending on host cron, systemd
timers, or a message-broker-backed job system — a plain, lockable,
idempotent cleanup loop is enough for this workload.

## Runtime and Deployment Model

The web process serves synchronous request/response traffic under
Gunicorn (WSGI), not ASGI. RidgeNote has no WebSocket endpoints and
no real-time collaborative-editing feature, so there is nothing an
async server would provide here.

Both the web and scheduler processes apply pending database
migrations automatically at startup, coordinated by a PostgreSQL
advisory lock so two containers starting together never race each
other. There is no separate manual migration step in normal
operation.

RidgeNote ships as **Docker Compose**, three services: `web`,
`scheduler`, `postgres`. These service names, and the internal
hostname each service is reachable at, are treated as a stability
contract — templates, scripts, and operator documentation all assume
them. There are two distinct Compose files for two distinct
audiences: the root `docker-compose.yml` builds the application from
source for local development; `deploy/docker-compose.yml` runs a
published image against persistent host storage for a normal
standalone install. They are not meant to be interchangeable.

## Data Ownership and Integrity

Every note, folder, and tag belongs to exactly one user. Ownership
isolation is enforced at the query/service layer — a request can
only see or act on data it owns (or, for a bounded set of
administrator-recovery operations, metadata about other users'
deleted content) — not merely hidden in the UI.

Note bodies are stored as **structured editor JSON** (Tiptap/
ProseMirror), not raw HTML or Markdown. This preserves rich-text
semantics reliably across editing, rendering, and export; Markdown
and plain-text output are derived, secondary representations, not
the source of truth.

Saves use **optimistic concurrency**: each note carries an integer
version, and a save from a stale version is rejected rather than
silently applied. This prevents one browser tab or device from
silently overwriting another's edits without requiring live
collaborative editing.

Deleting a folder is **one transactional operation** covering the
folder and the notes inside it together — restore returns them
together too. Deleting a user account is similarly a complete,
consistent restore: the account and its owned notes, folders, tags,
and trash state are recovered as one unit, not piecemeal, because
recovering only part of it would leave ownership and organization
inconsistent.

RidgeNote does **not** keep a persistent, server-stored revision
history for notes in V1. It is intentionally a lightweight
scratch-pad application, not a document-versioning system. In-session
Undo/Redo is handled by the editor itself (non-persistent, and reset
whenever a note view is freshly loaded, since RidgeNote is
server-rendered rather than a single-page application). Recovering
older content relies on Trash/restore, administrator recovery, or an
operator's own database backup — not a revision store.

## Database and Schema Evolution

Migrations are conservative. No migration has ever removed a
data-bearing field or model; the only constraint removals on record
have widened an allowed set of values (for example, adding a new
theme or tag color), never narrowed one. This additive discipline is
intentional: schema changes should extend what's possible, not
retroactively invalidate data that already exists.

Automatic startup migrations apply ordinary, backward-compatible
schema changes only. Once real user data exists, this project does
not promise every future change will be a silent, automatic upgrade
— a release that needs a breaking or operator-involved database
change is expected to say so explicitly in its own release notes,
rather than relying on this document as a blanket guarantee.

## Frontend Approach

RidgeNote is server-rendered: Django templates produce the page
shell and navigation, and TypeScript (built with Vite) progressively
enhances specific interactions on top of that — most importantly the
rich-text editor, which is inherently JavaScript-dependent and does
not have a no-JavaScript fallback. This is not a client-side SPA:
there is no client-side router, and each page load is a real server
request rendering real HTML, with JavaScript layered on top rather
than replacing that model.

## Security Boundaries

RidgeNote's application layer includes practical protections:
account controls, login lockout, server-side database-backed
sessions (so disabling a user or resetting a password can invalidate
their sessions centrally), ownership isolation, and CSRF protection.

What RidgeNote does **not** provide is network-perimeter security.
HTTPS termination, reverse-proxy configuration, host/OS hardening,
firewalling, and general Internet-facing controls like rate limiting
are the operator's responsibility, using their own infrastructure in
front of RidgeNote. RidgeNote is not, and does not claim to be, fully
Internet-hardened on its own.

## Backup and Recovery Boundaries

RidgeNote's built-in recovery layers operate at the application
level: user-initiated **Library Backup** (a per-user export/import
tool for portability and self-service recovery), **Trash and
restore**, and **administrator recovery** for content past the
normal owner-visible window. None of these is a substitute for a
full-instance backup — Library Backup in particular is scoped to one
user's own library, not the whole database.

Whole-instance disaster recovery is a PostgreSQL/infrastructure-level
concern, and it is the operator's responsibility, using whatever
host, VM, container, or database-level backup approach fits their
environment. See the deployment and backup/restore runbooks under
`docs/` for the operational detail this document deliberately does
not repeat.

## Contributor Design Principles

A few principles are worth preserving in any change to RidgeNote:

- **Ownership isolation is not negotiable.** Any new query or view
  touching notes, folders, or tags must be scoped to the requesting
  user (or go through the explicit, narrow administrator-recovery
  path), never rely on the UI alone to hide other users' data.
- **Prefer additive schema changes.** Widen before you narrow; treat
  a destructive migration against real user data as an exceptional,
  carefully considered event, not a routine one.
- **Keep the deployment portable.** Don't introduce a dependency on
  host cron, a specific orchestrator, or a message broker for what a
  simple, lockable, idempotent background job can already do.
- **Respect the service-topology contract.** The `web`/`scheduler`/
  `postgres` service names and the internal hostnames they're
  reachable at are assumed elsewhere (templates, scripts, operator
  docs) — changing them is a breaking change, not a refactor.
- **Keep the server-rendered core server-rendered.** Add
  TypeScript where an interaction genuinely needs client-side
  behavior; don't reach for a client-side framework or router to
  replace what Django already renders correctly.

## Deliberate Non-Goals

Some things are intentionally out of scope for RidgeNote's
architecture, not merely unimplemented yet:

- **Real-time collaborative editing.** Optimistic concurrency (reject
  a stale save, surface the conflict) was chosen specifically instead
  of live multi-editor synchronization.
- **An ASGI/WebSocket runtime.** RidgeNote is a conventional
  request/response application; there is no feature today that needs
  a persistent connection.
- **A general-purpose job queue or message broker.** The scheduler's
  simple timer-plus-advisory-lock model is deliberately lighter-weight
  infrastructure than Celery/Redis-style systems.
- **A separate public API or SPA frontend.** Nothing rules this out
  permanently, but it would be a deliberate future addition, not an
  incremental extension of the current server-rendered design.
