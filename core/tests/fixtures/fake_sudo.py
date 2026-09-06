#!/usr/bin/env python3
"""Fake `sudo` executable for isolated tests of setup-deploy-host.sh.

Simulates successful privilege escalation without performing any real
ownership/permission change or requiring real root access -- exactly
what an isolated, non-destructive test needs. Optionally logs its
invocation for assertions, via ``FAKE_SUDO_LOG``.
"""

import os
import sys

log_path = os.environ.get("FAKE_SUDO_LOG")
if log_path:
    with open(log_path, "a") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\n")

sys.exit(0)
