# Third-Party Notices and Dependency License Register

## Review Status

Pre-implementation technology, dependency, and licensing review completed on **2026-06-27**.

This document records approved direct dependencies, approved development-only dependencies, container-image choices, deferred technologies, and the policy for transitive dependencies.

Approval in this document means that the component is acceptable for RidgeNote's planned Apache-2.0 distribution based on the upstream information reviewed on the review date. It is not legal advice. Exact resolved versions, hashes, image digests, transitive dependencies, and generated license reports are committed and kept current through this project's lockfiles and generated dependency/license/SBOM reports -- see "Dependency and Report Status" below.

The review must be repeated:

- before public release
- whenever a major dependency materially changes
- whenever a new direct runtime dependency or bundled frontend asset is proposed
- whenever a license, NOTICE file, distribution model, or container base materially changes

## Version and Locking Policy

- Python direct dependencies use constrained input files and a fully resolved, hash-checked lock file generated with `pip-tools`.
- JavaScript direct dependencies use exact versions in `package.json` and a committed `package-lock.json`; builds use `npm ci`.
- Container images use explicit version-and-distribution tags and must be pinned to reviewed image digests when Compose and Dockerfiles are first created.
- Transitive dependencies are not individually listed as approved direct dependencies below. They must be captured by lockfiles and generated dependency/license reports.
- Security and maintenance updates are deliberate changes: update, review release notes and license changes, regenerate lockfiles/reports, test, and commit together.

## Approved Direct Production Dependencies and Images

| Component | Recommended version or policy | Official source | Maintenance status | License | Apache-2.0 compatibility | Attribution / NOTICE / source-offer / redistribution | Container or build implications | Security and maintenance notes | Review status |
|---|---|---|---|---|---|---|---|---|---|
| Python | `3.13.14`; patch updates within Python 3.13 after review | Python Software Foundation / `python.org` | Active security and bug-fix line | PSF License 2.0 | Compatible | Preserve upstream copyright/license material when redistributing Python; no NOTICE or source-offer requirement identified | Runtime base uses the official Python image | Track Python security releases; do not float to Python 3.14 without review | Approved |
| Official Python container image | `python:3.13.14-slim-bookworm`, pinned by digest | Docker Official Images / `docker-library/python` | Actively maintained official image | Python license plus Debian package licenses | Compatible, subject to included-component obligations | Preserve applicable licenses and notices from Python and included Debian packages; retain generated image/SBOM report | Final application image base; run RidgeNote as a non-root user | Rebuild regularly for OS security fixes; digest changes require review and test | Approved |
| Django | `~=5.2.0` LTS; exact current 5.2 patch locked at implementation | Django Software Foundation / `djangoproject.com` and PyPI `Django` | Supported LTS line | BSD-3-Clause | Compatible | Preserve copyright and license text in redistributed source/binary notices; no NOTICE or source-offer requirement | Installed in application image | Stay on 5.2 LTS security patches; Django 6.x is not an automatic upgrade | Approved |
| PostgreSQL server | Major 17; current minor; initial image `postgres:17.10-bookworm`, pinned by digest | PostgreSQL Global Development Group / Docker Official Image | PostgreSQL 17 supported through November 2029 | PostgreSQL License; image also contains Debian-licensed components | Compatible | Preserve PostgreSQL license/copyright and applicable Debian notices; no source-offer requirement identified for PostgreSQL itself | Dedicated Compose service `postgres`; database port remains unpublished | Apply current minor releases after backup and release-note review | Approved |
| Psycopg | `psycopg==3.3.4` using the pure-Python package with system `libpq`; do not use the bundled `binary` extra by default | Psycopg project / PyPI `psycopg` | Actively maintained | LGPL-3.0-only | Compatible for dynamic use alongside Apache-2.0 code | Keep Psycopg's LGPL license and copyright notice; do not remove users' ability to replace the library; document source availability. No RidgeNote source relicensing is required for unmodified dynamic use | Runtime image needs Debian `libpq5`; avoids bundling Psycopg's self-contained binary wheel | Re-review before switching to `psycopg[binary]` or `psycopg[c]` because bundled/native components change obligations and build surface | Approved with obligations |
| Gunicorn | `gunicorn==26.0.0` | Gunicorn project / PyPI `gunicorn` | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text in third-party notices; no NOTICE or source-offer requirement | Production WSGI server in `web`; scheduler runs a Django management command instead | Configure bounded workers/timeouts and graceful shutdown; never use Django's development server for production-style deployment | Approved |
| WhiteNoise | Major 6, exact current patch locked at implementation (`whitenoise>=6,<7`) | WhiteNoise project / PyPI `whitenoise` | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text; no NOTICE or source-offer requirement | Serves versioned static assets from the application for direct `IP:PORT` use; reverse proxies may still cache them | Use immutable hashed static files; do not use it for user uploads | Approved |
| Tiptap core | `@tiptap/core==3.27.1` | Tiptap / npm and `github.com/ueberdosis/tiptap` | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text; no NOTICE or source-offer requirement | Bundled into compiled frontend assets | Pin all Tiptap packages to one tested version; schema changes require document-compatibility review | Approved |
| Tiptap ProseMirror wrapper | `@tiptap/pm==3.27.1` | Tiptap / npm | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text | Direct dependency because Tiptap documents it as part of the installation set; bundled at build time | Keep aligned exactly with other Tiptap packages | Approved |
| Tiptap StarterKit | `@tiptap/starter-kit==3.27.1` | Tiptap / npm | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text | Bundles the core document, paragraph, text, heading, list, bold, italic, and undo/redo functionality | Disable unused StarterKit features in configuration rather than exposing unapproved editor features | Approved |
| Tiptap Underline extension | `@tiptap/extension-underline==3.27.1` | Tiptap / npm | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text | Bundled frontend asset | Keep aligned with Tiptap core version | Approved |
| Tiptap Link extension | `@tiptap/extension-link==3.27.1` | Tiptap / npm | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text | Bundled frontend asset | Validate protocols and use safe link attributes; do not permit arbitrary script URLs | Approved |
| ProseMirror packages | Resolved transitively through `@tiptap/pm==3.27.1`; exact versions captured in `package-lock.json` | ProseMirror project / npm and `github.com/ProseMirror` | Actively maintained | MIT | Compatible | Preserve MIT licenses for bundled ProseMirror modules | Bundled into frontend assets | Track through lockfile and generated license report; do not add individual ProseMirror packages directly unless Tiptap requires it | Approved transitive family |
| Vite | Major 7 stable; exact version locked in `package-lock.json` after install and before coding | Vite project / `vite.dev` and npm `vite` | Actively maintained | MIT | Compatible | Preserve copyright and MIT license text | Frontend build tool only; not present as a server process in production | Pin exact resolved version; review major upgrades and build-output changes | Approved |
| TypeScript | TypeScript 5.9 line; select the latest stable 5.9 patch available at implementation time (currently `typescript==5.9.3`) and pin it exactly in `package.json` and `package-lock.json` | Microsoft / npm `typescript` | Actively maintained | Apache-2.0 | Compatible | Preserve Apache-2.0 license and any applicable notices | Build and type-check tool only | Compile in strict mode; no TypeScript runtime service. TypeScript 6 is deferred until compatibility with Vite, ESLint, typescript-eslint, Tiptap, and RidgeNote's build configuration has matured and been verified | Approved |
| Node.js build image | Node.js 24 LTS, official `node:24-bookworm-slim`, pinned by digest | Node.js project / Docker Official Images | Active LTS | Node.js MIT license plus bundled third-party and Debian licenses | Compatible, subject to included-component obligations | Preserve Node.js and included third-party notices in build records; final RidgeNote runtime image should not contain Node.js | Multi-stage build only; compile assets, then copy outputs into Python image | Rebuild for security fixes; record Node/npm versions used for each lockfile | Approved build-only image |

## Approved Development-Only Dependencies

| Component | Recommended version or policy | Official source | Maintenance status | License | Apache-2.0 compatibility | Obligations | Purpose and notes | Review status |
|---|---|---|---|---|---|---|---|---|
| pip-tools | `pip-tools==7.5.3` | PyPI / `github.com/jazzband/pip-tools` | Actively maintained | BSD-3-Clause | Compatible | Preserve BSD license text | Produce deterministic, hash-checked Python lock files | Approved development-only |
| pytest | Major 9; exact version locked in development requirements | pytest project / PyPI `pytest` | Actively maintained | MIT | Compatible | Preserve MIT license text | Python test runner | Approved development-only |
| pytest-django | `pytest-django==4.12.0` | pytest-django project / PyPI | Actively maintained | BSD-3-Clause | Compatible | Preserve BSD license text | Django integration for pytest | Approved development-only |
| pytest-cov | `pytest-cov==7.1.0` | pytest-dev / PyPI | Actively maintained | MIT | Compatible | Preserve MIT license text | Coverage reporting; enforce meaningful thresholds only after the test suite exists | Approved development-only |
| Ruff | `ruff==0.15.20` | Astral / PyPI `ruff` | Actively maintained | MIT | Compatible | Preserve MIT license text | Python linting, import sorting, and formatting; replaces multiple tools | Approved development-only |
| ESLint | Major 9; exact version locked in `package-lock.json` | ESLint project / npm `eslint` | Actively maintained | MIT | Compatible | Preserve MIT license text | JavaScript/TypeScript linting; configure only rules the project understands | Approved development-only |
| typescript-eslint | Major 8 compatible with ESLint 9; exact version locked | typescript-eslint project / npm `typescript-eslint` | Actively maintained | MIT | Compatible | Preserve MIT license text | Type-aware TypeScript ESLint configuration | Approved development-only |
| Prettier | Major 3; exact version locked | Prettier project / npm `prettier` | Actively maintained | MIT | Compatible | Preserve MIT license text | Frontend formatting only; Ruff formats Python | Approved development-only |
| Vitest | `vitest==4.1.9` | Vitest project / npm `vitest` | Actively maintained | MIT | Compatible | Preserve MIT license text | Unit tests for editor and focused frontend modules | Approved development-only |

## CSS and Icons

### CSS

Use project-owned, ordinary CSS for Version One.

No CSS framework is approved or required. Bootstrap, Tailwind CSS, and component-framework packages were considered unnecessary for the initial scope. Plain CSS avoids a large styling dependency surface and keeps Django templates understandable.

### Icons

No icon library (npm package or CDN) is approved as a runtime dependency.

Most icons remain small, project-owned SVGs created specifically for RidgeNote (`core/templates/core/icons/`). Sixteen of those repository-owned partials copy unmodified path geometry from the [Lucide](https://lucide.dev) icon project, selected as RidgeNote's approved source icon library for cases where an official, recognizable icon communicates an action more clearly than a hand-drawn one:

| RidgeNote partial | Upstream Lucide icon | Used for | License |
|---|---|---|---|
| `core/templates/core/icons/note-new.svg` | `file-plus` | New note (tree/drawer creation toolbar) | ISC |
| `core/templates/core/icons/folder-new.svg` | `folder-plus` | New folder (tree/drawer creation toolbar) | ISC |
| `core/templates/core/icons/move.svg` | `folder-input` | Move note (tree/drawer creation toolbar) | ISC |
| `core/templates/core/icons/sun.svg` | `sun` | Theme toggle -- switch to light theme (global header) | ISC |
| `core/templates/core/icons/trash.svg` | `trash-2` | Direct Trash navigation (note-detail workspace header) | MIT (Feather) |
| `core/templates/core/icons/history.svg` | `history` | Recent notes toolbar trigger (note-detail workspace) | ISC |
| `core/templates/core/icons/pin.svg` | `pin` | Home row Pin/Unpin toggle, both states (Home note list) | ISC |
| `core/templates/core/icons/bold.svg` | `bold` | Note-detail formatting toolbar -- Bold | ISC |
| `core/templates/core/icons/italic.svg` | `italic` | Note-detail formatting toolbar -- Italic | MIT (Feather) |
| `core/templates/core/icons/underline.svg` | `underline` | Note-detail formatting toolbar -- Underline | ISC |
| `core/templates/core/icons/list.svg` | `list` | Note-detail formatting toolbar -- Bulleted list | ISC |
| `core/templates/core/icons/list-ordered.svg` | `list-ordered` | Note-detail formatting toolbar -- Numbered list | ISC |
| `core/templates/core/icons/link.svg` | `link` | Note-detail formatting toolbar -- Link | MIT (Feather) |
| `core/templates/core/icons/undo-2.svg` | `undo-2` | Note-detail formatting toolbar -- Undo | ISC |
| `core/templates/core/icons/redo-2.svg` | `redo-2` | Note-detail formatting toolbar -- Redo | ISC |
| `core/templates/core/icons/chevrons-right.svg` | `chevrons-right` | Collapsed rail's tree reveal/hide toggle (workspace shell) -- rotated 180deg by CSS once the tree is expanded | ISC |

Lucide's own upstream `LICENSE` file lists most icons under Lucide's ISC terms, but a smaller set (including `trash-2`, `italic`, and `link`) is listed there as derived from the [Feather](https://feathericons.com) project and carries Feather's original MIT terms instead -- confirmed directly against that upstream file each time an icon was added. `sun`, `history`, `pin`, `bold`, `underline`, `list`, `list-ordered`, `undo-2`, `redo-2`, and `chevrons-right` are not in that Feather-derived list and remain ISC.

`pin.svg` uses genuine upstream Lucide thumbtack geometry.

No Lucide npm package or CDN reference exists anywhere in the codebase; the exact upstream SVG markup was copied by hand into these repository-owned partials (only presentation attributes were adjusted -- `aria-hidden`, `focusable`, and dropping literal `width`/`height` already controlled by RidgeNote's shared `.icon-button` CSS -- the path data itself is unmodified). Full ISC and MIT license text and per-icon attribution are recorded in `core/templates/core/icons/LUCIDE_LICENSE.txt`, the narrowest location colocated with the icons themselves. Do not copy any further third-party SVG paths (Lucide or otherwise) without recording their source and license in that same file.

## Transitive Dependencies

Transitive Python and JavaScript packages are approved only as resolved dependencies of the direct packages above, not as independent architectural choices.

Required controls:

- commit the Python lock file with hashes
- commit `package-lock.json`
- use `pip install --require-hashes` for locked Python installs
- use `npm ci` for JavaScript installs
- generate and retain Python and npm dependency/license reports
- inspect all unknown, copyleft, dual-licensed, custom, or unlicensed entries before accepting the lockfile
- record container image digests and generate an image package/SBOM report
- repeat the review before public release

## Deferred or Rejected Technologies

| Technology | Status | Reason |
|---|---|---|
| Redis, Celery, RabbitMQ, and general-purpose queues | Rejected for Version One | The approved scheduler is a dedicated Compose service running a Django management command; a queue adds unjustified infrastructure |
| APScheduler and similar scheduler libraries | Rejected initially | A daily loop, overdue-run check, and PostgreSQL advisory lock can be implemented with Django and the Python standard library |
| Daphne, Uvicorn, ASGI WebSocket stacks | Rejected for Version One | RidgeNote requires WSGI HTTP behavior, not WebSockets or collaborative editing |
| Separate React/Vue/Svelte SPA | Rejected for Version One | Conflicts with the approved Django-rendered modular monolith |
| Tailwind CSS, Bootstrap, and component UI frameworks | Deferred/rejected initially | Plain CSS is sufficient and has a smaller build and license surface |
| Icon library | Deferred | Text controls and project-owned SVGs are sufficient until the real UI demonstrates a need |
| Playwright browser automation | Deferred | Useful later for critical browser workflows, but its browsers and container footprint are unnecessary for the first skeleton |
| DOMPurify or server-side HTML sanitizer | Deferred with a guardrail | RidgeNote stores schema-validated JSON and does not accept arbitrary HTML. Add and review a sanitizer before any arbitrary HTML import or untrusted HTML rendering path |
| `psycopg[binary]` | Rejected by default | Convenient, but bundles native libraries and expands binary redistribution and update considerations |
| Alpine runtime images | Rejected initially | Debian slim provides simpler compatibility with Python wheels and `libpq`; the size savings do not justify musl-specific friction |
| Django 6.x | Deferred | Django 5.2 LTS offers a longer, conservative support target for Version One |
| PostgreSQL 18 | Deferred | PostgreSQL 17 is already approved, supported through 2029, and avoids a needless major-version choice change before implementation |

## Public-Release Compliance Checklist

Before any public image or source release:

1. Regenerate Python and npm dependency/license reports from committed lockfiles.
2. Generate container-image SBOM/package reports for the final Python and PostgreSQL images.
3. Confirm all direct and transitive licenses remain compatible with distribution.
4. Copy required attribution and NOTICE text into this file or a referenced notice file.
5. Confirm any LGPL components remain replaceable and unmodified, or fulfill the applicable source obligations.
6. Confirm no package marked unreviewed, unknown, custom, or unlicensed is shipped.
7. Re-run security audits and address or document material findings.
8. Verify that source code, lockfiles, license texts, and modification notices required by upstream licenses are included.

## Dependency and Report Status

Resolved lockfiles, generated dependency/license/SBOM reports, and
pinned container image digests are the source of truth for exact
current versions, not this narrative document. See `reports/README.md`
for the exact commands used to generate each report.

Pinned image inputs:

- Node build image: `node:24-bookworm-slim@sha256:b31e7a42fdf8b8aa5f5ed477c72d694301273f1069c5a2f71d53c6482e99a2fc`
- Python runtime image: `python:3.13.14-slim-bookworm@sha256:fcbd8dfc2605ba7c2eca646846c5e892b2931e41f6227985154a596f26ab8ed7`
- PostgreSQL image: `postgres:17.10-bookworm@sha256:17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a`

Generated reports, all under `reports/`:

- `python-pip-list.json` (`pip list --format=json`) and
  `python-license-metadata.json` (Python `importlib.metadata`) --
  package list and partial license metadata, not a formal SBOM.
- `npm-dependency-tree.json` (`npm ls --all --json`) and
  `npm-license-metadata.json` (package-lock license fields) --
  dependency tree and partial license metadata, not a formal SBOM.
- `npm-sbom.cyclonedx.json` (`npm sbom --sbom-format cyclonedx --json`)
  -- npm-generated CycloneDX SBOM for npm dependencies only.
- `npm-audit.json` (`npm audit --json`) and `npm-audit-omit-dev.json`
  (`npm audit --omit=dev --json`) -- audit reports, not an SBOM.
- `container-packages-ridgenote.txt` and `container-packages-postgres.txt`
  (`dpkg-query -W` inside each pinned image) -- partial Debian package
  inventories, not a formal SBOM.

**Current audit status (as of 2026-08-28):** the full `npm audit`
(including development/build tooling) reports 4 high-severity findings,
all in transitive development/build-tooling dependencies pulled in by
the ESLint/Vite toolchain; none of them are installed in, or reachable
from, the built RidgeNote runtime image (see "Runtime Boundary" in the
project `README.md`). The production-dependency audit
(`npm audit --omit=dev`) reports 0 vulnerabilities. See
`reports/npm-audit.json`, `reports/npm-audit-omit-dev.json`, and
`reports/README.md` for full detail.

Node.js, Vite, and esbuild are build/development tooling only, used in
the Node build stage or validation containers, and are not included in
the final Python runtime image.

No permanent runtime or development dependency and no host-wide
scanner was added solely to generate these reports.
