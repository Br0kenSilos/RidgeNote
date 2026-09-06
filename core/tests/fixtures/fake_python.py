#!/usr/bin/env python3
"""Fake `python` executable for isolated tests of docker-entrypoint.sh.

Only understands `manage.py startup_migrate` and logs every invocation's
argv, one line per call, to `FAKE_PYTHON_LOG` -- so tests can assert
exactly what docker-entrypoint.sh decided to run, without any real
Django settings, database, or management-command machinery.

- ``FAKE_PYTHON_STARTUP_MIGRATE_EXIT``: exit code `manage.py
  startup_migrate` returns (default 0).
- Any other argv (e.g. a real `gunicorn`/other command handed to `exec`
  would never reach this fake at all -- only `python ...` invocations
  do) is logged and exits 0, so the fake stays usable as a generic argv
  recorder for "final command" assertions in tests that invoke it
  directly as the container command.
"""

import os
import sys

args = sys.argv[1:]

log_path = os.environ.get("FAKE_PYTHON_LOG")
if log_path:
    with open(log_path, "a") as fh:
        fh.write(" ".join(args) + "\n")

if args[:2] == ["manage.py", "startup_migrate"]:
    sys.exit(int(os.environ.get("FAKE_PYTHON_STARTUP_MIGRATE_EXIT", "0")))

sys.exit(0)
