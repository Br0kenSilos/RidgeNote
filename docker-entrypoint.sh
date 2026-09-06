#!/usr/bin/env bash
# RidgeNote image entrypoint. Runs the automatic startup-migration check
# (core/management/commands/startup_migrate.py -- wait for PostgreSQL,
# acquire a PostgreSQL advisory lock, apply pending migrations, release
# the lock) before handing control to the container's real command via
# `exec "$@"`, so both `web` (Gunicorn) and `scheduler`
# (run_purge_scheduler) share this same safety check.
#
# All database/retry/locking logic lives in startup_migrate.py, not
# here -- this script only decides whether to invoke it.
#
# The automatic check is intentionally skipped in exactly two narrow,
# documented cases, so it is never confusingly run twice:
#   - the requested command is itself an explicit `manage.py migrate`
#     or `manage.py startup_migrate` invocation (e.g. via
#     `docker compose run --rm web python manage.py migrate --noinput`);
#   - RIDGENOTE_SKIP_STARTUP_MIGRATION=1 is set, for advanced
#     troubleshooting (e.g. running `dbshell` while diagnosing a broken
#     database) -- not intended for ordinary use.
# Every other command (gunicorn, run_purge_scheduler, shell,
# createsuperuser, ...) still gets the automatic check by default.
set -euo pipefail

skip_startup_migration=0

if [[ "${RIDGENOTE_SKIP_STARTUP_MIGRATION:-}" == "1" ]]; then
  skip_startup_migration=1
elif [[ "${1:-}" == "python" && "${2:-}" == "manage.py" \
      && ( "${3:-}" == "migrate" || "${3:-}" == "startup_migrate" ) ]]; then
  skip_startup_migration=1
fi

if [[ "$skip_startup_migration" == "0" ]]; then
  python manage.py startup_migrate
fi

exec "$@"
