#!/usr/bin/env bash
# Prepare a clean standalone RidgeNote deployment directory: create and
# secure persistent directories, apply the approved V1 PostgreSQL data
# ownership (999:999 -- see deploy/README.md's "Create and prepare
# PostgreSQL's persistent storage" step), initialize .env (never
# overwriting an existing one), and validate the resulting Compose
# configuration.
#
# This script never starts RidgeNote, never runs migrations, and never
# creates an administrator -- those remain explicit, separate steps.
#
# Usage:
#   scripts/setup-deploy-host.sh \
#     --deploy-dir /opt/ridgenote \
#     --host 192.0.2.10 \
#     --external-url http://192.0.2.10:8000
#
#   scripts/setup-deploy-host.sh --deploy-dir DIR --host HOST \
#     --external-url URL --validate-only
#
# Requires the target directory to already contain exactly the three
# source-free deployment files (docker-compose.yml, env.example,
# README.md), placed there separately -- this script does not fetch them.

set -euo pipefail

PROGRAM_NAME="$(basename "${BASH_SOURCE[0]}")"

usage() {
  cat <<EOF
Usage: ${PROGRAM_NAME} --deploy-dir DIR --host HOST --external-url URL [--validate-only]
       ${PROGRAM_NAME} --help

Prepares a clean standalone RidgeNote deployment directory. Never starts
RidgeNote, never runs migrations, never creates an administrator.

Required:
  --deploy-dir DIR    Path to the directory already containing
                       docker-compose.yml, env.example, and README.md.
  --host HOST          LAN hostname or IP this instance is reached at.
  --external-url URL   Full URL this instance is reached at, e.g.
                       http://192.0.2.10:8000

Optional:
  --validate-only      Make no filesystem, ownership, permission, or
                        secret change. Verify an existing deployment
                        directory and .env, and report pass/fail.
  --help                Show this message and exit.
EOF
}

DEPLOY_DIR=""
HOST=""
EXTERNAL_URL=""
VALIDATE_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-dir)
      DEPLOY_DIR="${2:-}"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --external-url)
      EXTERNAL_URL="${2:-}"
      shift 2
      ;;
    --validate-only)
      VALIDATE_ONLY=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$DEPLOY_DIR" || -z "$HOST" || -z "$EXTERNAL_URL" ]]; then
  echo "ERROR: --deploy-dir, --host, and --external-url are all required." >&2
  usage >&2
  exit 2
fi

# ---------------------------------------------------------------------
# Root-execution guard
# ---------------------------------------------------------------------

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: do not run this script as root or via 'sudo $PROGRAM_NAME'." >&2
  echo "It escalates narrowly (sudo chown/chmod) only for the PostgreSQL" >&2
  echo "data directory, when required. Run it as your normal deploy user." >&2
  exit 1
fi

# ---------------------------------------------------------------------
# Required-command checks
# ---------------------------------------------------------------------

for cmd in docker openssl sed grep awk stat chmod mkdir; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not usable by the current user (is it installed" >&2
  echo "and is this user in the 'docker' group?)." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: 'docker compose' (the Compose V2 plugin) is not available." >&2
  echo "This script does not support the legacy standalone docker-compose." >&2
  exit 1
fi

# ---------------------------------------------------------------------
# Deploy-directory path safety
# ---------------------------------------------------------------------

case "$DEPLOY_DIR" in
  "" | / | /home | /var | /etc | /usr | /tmp)
    echo "ERROR: refusing to use an unsafe deployment directory: '$DEPLOY_DIR'" >&2
    exit 1
    ;;
esac

if [[ ! -d "$DEPLOY_DIR" ]]; then
  echo "ERROR: deployment directory does not exist: $DEPLOY_DIR" >&2
  echo "Create it and place docker-compose.yml, env.example, and README.md" >&2
  echo "in it first (see deploy/README.md)." >&2
  exit 1
fi

if [[ -L "$DEPLOY_DIR" ]]; then
  echo "ERROR: deployment directory must not be a symlink: $DEPLOY_DIR" >&2
  exit 1
fi

RESOLVED_DEPLOY_DIR="$(cd "$DEPLOY_DIR" && pwd -P)"
case "$RESOLVED_DEPLOY_DIR" in
  "" | / | /home | /var | /etc | /usr | /tmp)
    echo "ERROR: deployment directory resolves to an unsafe path: $RESOLVED_DEPLOY_DIR" >&2
    exit 1
    ;;
esac
DEPLOY_DIR="$RESOLVED_DEPLOY_DIR"

for required_file in docker-compose.yml env.example README.md; do
  path="$DEPLOY_DIR/$required_file"
  if [[ ! -f "$path" ]]; then
    echo "ERROR: required deployment file missing: $path" >&2
    exit 1
  fi
  if [[ -L "$path" ]]; then
    echo "ERROR: required deployment file must not be a symlink: $path" >&2
    exit 1
  fi
done

DATA_DIR="$DEPLOY_DIR/data"
PG_DATA_DIR="$DEPLOY_DIR/data/postgres"
BACKUPS_DIR="$DEPLOY_DIR/backups"
PG_BACKUPS_DIR="$DEPLOY_DIR/backups/postgres"

for maybe_symlink in "$DATA_DIR" "$PG_DATA_DIR" "$BACKUPS_DIR" "$PG_BACKUPS_DIR"; do
  if [[ -e "$maybe_symlink" && -L "$maybe_symlink" ]]; then
    echo "ERROR: refusing to proceed -- unsafe symlink present: $maybe_symlink" >&2
    exit 1
  fi
done

ENV_FILE="$DEPLOY_DIR/.env"
ENV_EXAMPLE="$DEPLOY_DIR/env.example"

# ---------------------------------------------------------------------
# PostgreSQL image sanity check (Compose-rendered, not brittle YAML regex)
# ---------------------------------------------------------------------
#
# Only confirms docker-compose.yml resolves to exactly one, genuinely
# `postgres:`-named image -- catching a corrupted/misedited Compose
# file -- not a specific tag or digest. The approved V1 image
# (`postgres:17-bookworm`, a floating tag within PostgreSQL major 17)
# is deliberately not digest-pinned, so no digest is required here.

discover_postgres_image() {
  local env_source="$1"
  local image
  image="$(docker compose --project-directory "$DEPLOY_DIR" --env-file "$env_source" config --images postgres 2>/dev/null || true)"
  if [[ -z "$image" ]]; then
    echo "ERROR: could not discover the PostgreSQL image from docker-compose.yml." >&2
    exit 1
  fi
  if [[ "$(printf '%s\n' "$image" | wc -l)" -ne 1 ]]; then
    echo "ERROR: PostgreSQL image discovery did not resolve to exactly one image: $image" >&2
    exit 1
  fi
  case "$image" in
    postgres:*) ;;
    *)
      echo "ERROR: discovered image is not a 'postgres:' image: $image" >&2
      exit 1
      ;;
  esac
  printf '%s' "$image"
}

# The pre-.env discovery step only needs a syntactically valid env file for
# Compose's own variable interpolation -- env.example already supplies
# non-secret placeholder values for every variable Compose reads, and the
# postgres image itself is a literal, unparameterized string in
# docker-compose.yml, so no real secret is required merely to discover it.
POSTGRES_IMAGE="$(discover_postgres_image "$ENV_EXAMPLE")"
echo "Discovered PostgreSQL image: $POSTGRES_IMAGE"

# ---------------------------------------------------------------------
# PostgreSQL data ownership -- fixed 999:999
# ---------------------------------------------------------------------
#
# The approved V1 deployment contract hardcodes the PostgreSQL data
# directory's ownership to 999:999, the standard numeric user/group the
# official `postgres:17-bookworm` image runs as -- see deploy/README.md's
# "Create and prepare PostgreSQL's persistent storage" step. This
# deliberately replaces an earlier dynamic `docker run --entrypoint id`
# discovery approach; that approach existed to avoid ever assuming a
# fixed number, but the real-world deployment rehearsal confirmed 999:999
# is the correct, unmissable value to instruct directly.
POSTGRES_UID=999
POSTGRES_GID=999
echo "PostgreSQL data ownership target: ${POSTGRES_UID}:${POSTGRES_GID}"

# =======================================================================
# --validate-only: read-only verification, no mutation
# =======================================================================

if [[ "$VALIDATE_ONLY" -eq 1 ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: --validate-only requires an existing .env: $ENV_FILE" >&2
    exit 1
  fi

  fail=0
  check() {
    local label="$1" ok="$2"
    if [[ "$ok" -eq 0 ]]; then
      echo "  OK   $label"
    else
      echo "  FAIL $label"
      fail=1
    fi
  }

  env_mode="$(stat -c '%a' "$ENV_FILE")"
  [[ "$env_mode" -le 600 ]]
  check ".env mode is 600 or narrower (found: $env_mode)" $?

  if grep -Eq 'replace-with-' "$ENV_FILE"; then
    check "no unresolved secret placeholder remains in .env" 1
  else
    check "no unresolved secret placeholder remains in .env" 0
  fi

  required_vars=(
    RIDGENOTE_SECRET_KEY RIDGENOTE_DEBUG
    RIDGENOTE_ALLOWED_HOSTS RIDGENOTE_CSRF_TRUSTED_ORIGINS
    RIDGENOTE_EXTERNAL_URL RIDGENOTE_DATABASE_NAME
    RIDGENOTE_DATABASE_USER RIDGENOTE_DATABASE_PASSWORD
  )
  all_present=0
  for var in "${required_vars[@]}"; do
    if ! grep -Eq "^${var}=.+" "$ENV_FILE"; then
      all_present=1
    fi
  done
  check "all required variables present and nonempty" $all_present

  # One user-facing database password (RIDGENOTE_DATABASE_PASSWORD);
  # docker-compose.yml wires it directly into PostgreSQL's own
  # POSTGRES_PASSWORD, so there is no separate value to compare in
  # .env -- confirm the *rendered Compose configuration* actually
  # receives it, without ever printing the value itself.
  ridgenote_db_pw="$(grep -E '^RIDGENOTE_DATABASE_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  rendered_pg_pw="$(docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" config 2>/dev/null \
    | awk '/^  postgres:/{f=1} f && /POSTGRES_PASSWORD:/{print $2; exit}')"
  [[ -n "$ridgenote_db_pw" && "$ridgenote_db_pw" == "$rendered_pg_pw" ]]
  check "PostgreSQL receives RIDGENOTE_DATABASE_PASSWORD as POSTGRES_PASSWORD" $?

  allowed_hosts="$(grep -E '^RIDGENOTE_ALLOWED_HOSTS=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  case ",$allowed_hosts," in
    *",127.0.0.1,"*) has_loopback=0 ;;
    *) has_loopback=1 ;;
  esac
  check "RIDGENOTE_ALLOWED_HOSTS includes 127.0.0.1" $has_loopback
  case ",$allowed_hosts," in
    *",$HOST,"*) has_host=0 ;;
    *) has_host=1 ;;
  esac
  check "RIDGENOTE_ALLOWED_HOSTS includes supplied --host ($HOST)" $has_host

  csrf_origin="$(grep -E '^RIDGENOTE_CSRF_TRUSTED_ORIGINS=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  case ",$csrf_origin," in
    *",$EXTERNAL_URL,"*) has_origin=0 ;;
    *) has_origin=1 ;;
  esac
  check "RIDGENOTE_CSRF_TRUSTED_ORIGINS includes supplied --external-url" $has_origin

  debug_value="$(grep -E '^RIDGENOTE_DEBUG=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  [[ "$debug_value" == "0" ]]
  check "RIDGENOTE_DEBUG is disabled (0)" $?

  purge_value="$(grep -E '^RIDGENOTE_PURGE_ENABLED=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  [[ "$purge_value" == "true" ]]
  check "RIDGENOTE_PURGE_ENABLED remains enabled by default" $?

  if docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" config --quiet 2>/dev/null; then
    check "docker compose config --quiet succeeds" 0
  else
    check "docker compose config --quiet succeeds" 1
  fi

  echo ""
  if [[ "$fail" -eq 0 ]]; then
    echo "Validation passed. No changes were made."
    exit 0
  else
    echo "Validation FAILED. No changes were made." >&2
    exit 1
  fi
fi

# =======================================================================
# Mutating path: directory creation, ownership, .env initialization
# =======================================================================

mkdir -p "$DATA_DIR"
chmod 755 "$DATA_DIR"

mkdir -p "$BACKUPS_DIR"
chmod 700 "$BACKUPS_DIR"

mkdir -p "$PG_BACKUPS_DIR"
chmod 700 "$PG_BACKUPS_DIR"

pg_data_preexisted=0
if [[ -d "$PG_DATA_DIR" ]] && [[ -n "$(ls -A "$PG_DATA_DIR" 2>/dev/null)" ]]; then
  pg_data_preexisted=1
  echo "NOTE: $PG_DATA_DIR already contains data -- its contents will not" \
       "be modified, only the directory's own ownership/mode if needed."
fi
mkdir -p "$PG_DATA_DIR"

current_owner="$(stat -c '%u:%g' "$PG_DATA_DIR")"
if [[ "$current_owner" != "${POSTGRES_UID}:${POSTGRES_GID}" ]]; then
  echo "Correcting ownership of $PG_DATA_DIR to ${POSTGRES_UID}:${POSTGRES_GID} (was $current_owner)..."
  if ! sudo chown "${POSTGRES_UID}:${POSTGRES_GID}" "$PG_DATA_DIR"; then
    echo "ERROR: could not chown $PG_DATA_DIR -- sudo privilege is required" >&2
    echo "for this one narrowly scoped operation and was not available." >&2
    exit 1
  fi
fi
current_mode="$(stat -c '%a' "$PG_DATA_DIR")"
if [[ "$current_mode" != "700" ]]; then
  if ! sudo chmod 700 "$PG_DATA_DIR"; then
    echo "ERROR: could not chmod $PG_DATA_DIR to 700." >&2
    exit 1
  fi
fi

if [[ "$pg_data_preexisted" -eq 1 ]]; then
  echo "(PostgreSQL data directory was nonempty; contents left untouched.)"
fi

if [[ -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE already exists -- refusing to overwrite it." >&2
  echo "Re-run with --validate-only to verify the existing .env instead." >&2
  exit 1
fi

echo "Generating .env..."
(
  umask 077
  django_secret="$(openssl rand -hex 48)"
  db_password="$(openssl rand -hex 32)"

  # One generated database password, written to the single user-facing
  # RIDGENOTE_DATABASE_PASSWORD variable only -- docker-compose.yml
  # wires it directly into PostgreSQL's own POSTGRES_PASSWORD, so no
  # separate POSTGRES_PASSWORD line is written to .env.
  sed \
    -e "s#^RIDGENOTE_SECRET_KEY=.*#RIDGENOTE_SECRET_KEY=${django_secret}#" \
    -e "s#^RIDGENOTE_ALLOWED_HOSTS=.*#RIDGENOTE_ALLOWED_HOSTS=${HOST},127.0.0.1#" \
    -e "s#^RIDGENOTE_CSRF_TRUSTED_ORIGINS=.*#RIDGENOTE_CSRF_TRUSTED_ORIGINS=${EXTERNAL_URL}#" \
    -e "s#^RIDGENOTE_EXTERNAL_URL=.*#RIDGENOTE_EXTERNAL_URL=${EXTERNAL_URL}#" \
    -e "s#^RIDGENOTE_DATABASE_PASSWORD=.*#RIDGENOTE_DATABASE_PASSWORD=${db_password}#" \
    "$ENV_EXAMPLE" > "$ENV_FILE"

  unset django_secret db_password
)
chmod 600 "$ENV_FILE"
echo ".env created (mode 600). Generated secret values are not printed."

# Only sanity-check the placeholders this script itself is responsible for
# resolving (the generated secrets).
if grep -Eq 'replace-with-' "$ENV_FILE"; then
  echo "ERROR: .env still contains an unresolved 'replace-with-' placeholder after generation." >&2
  echo "This indicates a template/script mismatch -- not proceeding." >&2
  exit 1
fi

echo "Validating Compose configuration..."
if ! docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" config --quiet; then
  echo "ERROR: docker compose config failed against the generated .env." >&2
  exit 1
fi

RENDERED="$(docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" config)"
echo "$RENDERED" | grep -q '^  web:'
echo "$RENDERED" | grep -q '^  scheduler:'
echo "$RENDERED" | grep -q '^  postgres:'
if echo "$RENDERED" | grep -A2 '^  web:' | grep -q 'build:'; then
  echo "ERROR: rendered configuration unexpectedly contains a 'build:' key for web." >&2
  exit 1
fi

echo ""
echo "=========================================================="
echo "Deployment directory prepared: $DEPLOY_DIR"
echo "  - $DATA_DIR (operator-owned, 755)"
echo "  - $PG_DATA_DIR (${POSTGRES_UID}:${POSTGRES_GID}, 700)"
echo "  - $BACKUPS_DIR (operator-owned, 700)"
echo "  - $PG_BACKUPS_DIR (operator-owned, 700)"
echo "  - .env created (600), Compose configuration validated"
echo ""
echo "RidgeNote was NOT started. Next steps (run manually):"
echo ""
echo "  cd $DEPLOY_DIR"
echo "  docker compose pull"
echo "  docker compose up -d"
echo "  docker compose logs -f"
echo ""
echo "Database migrations are applied automatically at startup -- there is"
echo "no separate manual migration step (see deploy/README.md, 'Automatic"
echo "startup migrations')."
echo ""
echo "Then visit ${EXTERNAL_URL}/setup/ to create the administrator account."
echo "=========================================================="
