# Release Reports

This directory contains generated dependency, license, SBOM, and container-package reports documenting the third-party components in RidgeNote's V1 release. They give downstream users and auditors visibility into what's included and under what license, without requiring anyone to regenerate the data themselves. All files here are generated output; none is edited by hand.

No permanent dependency or host-wide scanner was added solely to produce these reports. Node.js, Vite, and esbuild are build/development tooling only -- used in the frontend build stage and in the throwaway containers below, never included in the final Python runtime image.

## Included Reports

### Dependency and License Reports

- `python-pip-list.json`
  - Tool: `pip list --format=json`
  - Command: `docker run --rm -v /path/to/ridgenote:/app -w /app python:3.13.14-slim-bookworm python -m pip list --format=json`
  - Scope: Python runtime and development dependencies installed from `requirements/base.txt` and `requirements/dev.txt`.
  - Status: Package list, not a formal SBOM.
- `python-license-metadata.json`
  - Tool: Python `importlib.metadata`
  - Command: same Python 3.13 container, reading installed distribution metadata.
  - Scope: Best-effort license metadata from installed Python packages.
  - Status: Partial license metadata report, not a formal SBOM.
- `npm-dependency-tree.json`
  - Tool: `npm ls --all --json`
  - Command: `docker run --rm -v /path/to/ridgenote:/app -v /app/node_modules -w /app node:24-bookworm-slim npm ls --all --json`
  - Scope: npm dependency tree from `package-lock.json` after `npm ci`.
  - Status: Dependency tree, not a formal SBOM.
- `npm-license-metadata.json`
  - Tool: Node.js JSON parsing of `package-lock.json`
  - Command: `docker run --rm -v /path/to/ridgenote:/app -w /app node:24-bookworm-slim node -e ...`
  - Scope: Best-effort npm license fields recorded in `package-lock.json`.
  - Status: Partial license metadata report, not a formal SBOM.
- `npm-sbom.cyclonedx.json`
  - Tool: `npm sbom --sbom-format cyclonedx --json`
  - Command: `docker run --rm -v /path/to/ridgenote:/app -v /app/node_modules -w /app node:24-bookworm-slim npm sbom --sbom-format cyclonedx --json`
  - Scope: npm dependency SBOM.
  - Status: Formal npm-generated CycloneDX SBOM for npm dependencies only.
- `npm-audit.json`
  - Tool: `npm audit --json`
  - Command: same Node 24 container after `npm ci`.
  - Scope: npm audit including development-only dependencies.
  - Status: Security audit report, not an SBOM.
- `npm-audit-omit-dev.json`
  - Tool: `npm audit --omit=dev --json`
  - Command: same Node 24 container after `npm ci`.
  - Scope: npm production-dependency audit only -- the dependency set that actually reaches the shipped application.
  - Status: Security audit report, not an SBOM.

### Container Package Reports

- `container-packages-ridgenote.txt`
  - Tool: `dpkg-query -W`
  - Command: build the local development image (`docker compose build`, which tags it `ridgenote:dev` per `docker-compose.yml`), then `docker run --rm -e RIDGENOTE_DEBUG=true -e RIDGENOTE_SKIP_STARTUP_MIGRATION=1 ridgenote:dev dpkg-query -W`. The environment variables bypass the entrypoint's normal startup validation/migration handling, which this one-off inventory command has no need for.
  - Scope: Debian packages visible in the built RidgeNote runtime image.
  - Status: Partial package inventory, not a formal SBOM.
- `container-packages-postgres.txt`
  - Tool: `dpkg-query -W`
  - Command: `docker run --rm postgres:17.10-bookworm@sha256:17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a sh -c '... dpkg-query -W'`
  - Scope: Debian packages visible in the pinned PostgreSQL image used by `docker-compose.yml`/`deploy/docker-compose.yml`.
  - Status: Partial package inventory, not a formal SBOM.

## Regenerating Reports

Regenerate all reports before a release, or whenever `package-lock.json` or `requirements/*.txt` changes. Each command above is self-contained and can be run independently; none of them modifies the lockfiles, `requirements/*.txt`, application source, or build configuration -- they only read installed package/distribution metadata and write the corresponding file in this directory.

## Interpretation Notes

- **SBOM scope**: `npm-sbom.cyclonedx.json` is a formal CycloneDX SBOM for npm dependencies only. There is no equivalent formal SBOM tool in the Python ecosystem used here; `python-pip-list.json` and `python-license-metadata.json` are best-effort substitutes, not a formal SBOM.
- **License-metadata limitations**: `npm-license-metadata.json` and `python-license-metadata.json` are derived from installed package metadata on a best-effort basis. Some npm entries legitimately have no top-level `license` field recorded in `package-lock.json` -- this is a known limitation of the extraction method, not a regeneration defect.
- **Production vs. development audit scope**: `npm audit --json` (`npm-audit.json`) covers every npm dependency, including development-only build tooling (ESLint, Vite, and their transitives) that is never installed in, or reachable from, the built RidgeNote runtime image (Node.js and the frontend build tools run only in the build stage; see the Dockerfile). `npm audit --omit=dev` (`npm-audit-omit-dev.json`) covers only the dependency set that actually reaches the shipped application, and is the audit that matters for the deployed runtime's exposure. As of the current report set, `npm-audit.json` shows findings confined to development/build-tooling dependencies (the ESLint/Vite toolchain and its transitives), none reachable from the production runtime image; `npm-audit-omit-dev.json` shows zero vulnerabilities. Always check the current files directly rather than relying on this note, since dependency versions change between regenerations.
- **Container package reports** are partial inventories (`dpkg-query -W` output), not formal SBOMs -- they capture Debian package names/versions visible inside the built image at the time of regeneration, for auditing OS-level package drift between releases.
