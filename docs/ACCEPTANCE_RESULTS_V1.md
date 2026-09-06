# RidgeNote V1 Validation Results

This document records the final validation evidence for RidgeNote V1.
It is intentionally a concise, release-facing summary of what was
tested and what passed -- not a chronological engineering record of
how RidgeNote was built.

## Automated Validation

- **Backend test suite:** 1118 passed. A further 25 failures occur
  only when the suite is run as `root`; these are the *expected*
  result of intentional root-refusal tests in
  `core/tests/test_setup_deploy_host.py`, which deliberately verify
  that the deployment setup tooling refuses to run as `root`. The same
  test file passes **32/32** when run in its required non-root
  context. There is no unexplained backend test failure.
- **Frontend test suite (Vitest):** 1079/1079 passed.
- **TypeScript type-check:** clean, no errors.
- **Django system check:** 0 issues.
- **Ruff (Python lint):** clean, all checks passed.

## Functional Acceptance

An extensive functional pass exercised RidgeNote's real
views/forms/middleware/sessions/CSRF handling end to end, using
disposable data only. **51/51 checks passed.**

| Area | Result |
|---|---|
| Authentication and account administration | Pass |
| Notes | Pass |
| Folders | Pass |
| Tags | Pass |
| Full Search | Pass |
| Trash and recovery | Pass |
| Library Backup | Pass |
| Help | Pass |
| Multi-user isolation | Pass |
| Theme persistence | Pass |

## Browser and Responsive Validation

Chromium-family browsers and Firefox have been directly tested during
RidgeNote's development, across desktop, tablet, and phone viewport
widths, and against all built-in themes. Safari has not been formally
validated.

The final validation pass itself could not perform fresh live browser
rendering, because the available headless-browser runtime could not
launch in that execution environment (a missing system library it
could not be granted permission to install). No frontend
template/style/script file had changed since the layouts and themes
were last directly, visually verified in a real browser, so that prior
verified state remains current. This pass instead confirmed, without
live rendering: full functional coverage of the affected areas (see
above), and that no relevant frontend source had changed.

## Standalone Deployment Rehearsal

A separate, fresh standalone deployment rehearsal was completed on a
dedicated, disposable host, independent of the automated/functional
validation above.

- **Host:** Ubuntu 24.04 LTS, Docker Engine, Docker Compose.
- **Deployment path:** the published, deployment-style Docker Compose
  bundle (not a local development checkout).
- A fresh deployment reached a healthy three-service RidgeNote stack
  (application, scheduler, database).
- Database migrations applied automatically at startup.
- The health endpoint reported healthy.
- First-run setup and login succeeded.
- Folder, note, and tag creation succeeded.
- Full Search worked correctly.
- Preferences and theme switching worked correctly.
- Help worked correctly.
- Trash restore worked correctly.
- Library Backup worked correctly.
- Data persisted correctly across a container restart.
- A logical PostgreSQL backup (`pg_dump`) was created successfully
  while the stack was running.

**Result: PASS WITH NON-BLOCKING OBSERVATIONS.** The observations from
this rehearsal concerned substitutions in how the rehearsal itself was
delivered and operated, not any defect in RidgeNote's deployment
behavior.

## Known Validation Limitations

- No fresh live browser rendering was performed during the final
  automated/functional validation pass, for the execution-environment
  reason described above.
- Safari has not been formally validated.
- The V1 container image is built and tested for `linux/amd64` only.

## Overall Result

**PASS WITH NON-BLOCKING OBSERVATIONS.**

No known unresolved V1 correctness, privacy/ownership, security,
data-loss, or deployment defect was found during final release
validation. The non-blocking observations recorded above are
validation-environment and rehearsal-delivery limitations, not known
RidgeNote product defects.
