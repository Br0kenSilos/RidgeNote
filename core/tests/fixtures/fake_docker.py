#!/usr/bin/env python3
"""Fake `docker` executable for isolated tests of setup-deploy-host.sh.

Controlled entirely through environment variables so no real Docker,
sudo, or network access is required:

- ``FAKE_DOCKER_LOG``: if set, every invocation's argv is appended here
  (one line per call), so tests can assert which subcommands actually ran.
- ``FAKE_DOCKER_NO_COMPOSE``: if set, ``docker compose version`` fails,
  simulating an environment without the Compose V2 plugin.
- ``FAKE_DOCKER_BAD_IMAGE``: if set, the discovered PostgreSQL image is
  not digest-pinned, to exercise the rejection path.
- ``FAKE_DOCKER_BAD_UID``: if set, ``id -u postgres``/``id -g postgres``
  return non-numeric output, to exercise the UID/GID rejection path.
"""

import os
import sys

args = sys.argv[1:]

log_path = os.environ.get("FAKE_DOCKER_LOG")
if log_path:
    with open(log_path, "a") as fh:
        fh.write(" ".join(args) + "\n")

PG_IMAGE = (
    "postgres:not-pinned"
    if os.environ.get("FAKE_DOCKER_BAD_IMAGE")
    else "postgres:17.10-bookworm@sha256:"
    "17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a"
)

if args[:1] == ["info"]:
    sys.exit(0)

if args[:1] == ["compose"]:
    if "version" in args:
        if os.environ.get("FAKE_DOCKER_NO_COMPOSE"):
            sys.exit(1)
        print("Docker Compose version v5.3.0")
        sys.exit(0)
    if "--images" in args:
        idx = args.index("--images")
        service = (
            args[idx + 1]
            if idx + 1 < len(args) and not args[idx + 1].startswith("-")
            else None
        )
        if service == "postgres":
            print(PG_IMAGE)
        else:
            print("test/ridgenote:sha-test")
            print("test/ridgenote:sha-test")
            print(PG_IMAGE)
        sys.exit(0)
    if "--quiet" in args:
        sys.exit(0)
    # Full config render. Reads RIDGENOTE_DATABASE_PASSWORD out of the
    # real --env-file argument so tests can verify it is actually
    # threaded through to PostgreSQL's own POSTGRES_PASSWORD, the same
    # way real Compose variable interpolation would.
    postgres_password = ""
    if "--env-file" in args:
        env_file_path = args[args.index("--env-file") + 1]
        try:
            with open(env_file_path) as fh:
                for line in fh:
                    if line.startswith("RIDGENOTE_DATABASE_PASSWORD="):
                        postgres_password = line.strip().split("=", 1)[1]
                        break
        except OSError:
            pass
    print("services:")
    print("  web:")
    print("    image: test/ridgenote:sha-test")
    print("  scheduler:")
    print("    image: test/ridgenote:sha-test")
    print("  postgres:")
    print(f"    image: {PG_IMAGE}")
    print("    environment:")
    print(f"      POSTGRES_PASSWORD: {postgres_password}")
    sys.exit(0)

if args[:1] == ["run"]:
    bad_uid = bool(os.environ.get("FAKE_DOCKER_BAD_UID"))
    if "-u" in args:
        print("not-a-number" if bad_uid else "999")
        sys.exit(0)
    if "-g" in args:
        print("not-a-number" if bad_uid else "999")
        sys.exit(0)
    sys.exit(0)

sys.exit(1)
