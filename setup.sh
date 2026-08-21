#!/usr/bin/env bash
# setup.sh — Plane Community Edition bootstrap script
#
# Inspired by the official Plane setup script:
#   curl -fsSL https://github.com/makeplane/plane/releases/latest/download/setup.sh
#
# Two-phase workflow:
#   1. install  — first-time setup: prerequisites check, .env generation, and
#                 image pull ONLY.  No containers are ever started.
#                 After install the user validates .env, configures nginx,
#                 and makes any custom adjustments before calling start.
#   2. start    — runs database migrations then brings the full stack up.
#                 Assumes install has been completed and .env is validated.
#
# Usage:
#   ./setup.sh install [--project-name NAME] [--domain DOMAIN] [--port PORT]
#   ./setup.sh start
#   ./setup.sh stop | restart | pull | migrate | status | test | logs [svc]
#   ./setup.sh backup
#   ./setup.sh restore <backup-dir>
#   ./setup.sh upgrade [--to VERSION] [--skip-tests]
#
# Options accepted by 'install':
#   --project-name, -p NAME   Override COMPOSE_PROJECT_NAME in .env
#   --domain,       -d DOMAIN Override DOMAIN_NAME (also sets WEB_URL and
#                             CORS_ALLOWED_ORIGINS to http://DOMAIN)
#   --port,         -P PORT   Override LISTEN_HTTP_PORT in .env
#
# Requirements:
#   - Docker Engine  ≥ 24
#   - Docker Compose v2 (integrated — `docker compose`)

set -euo pipefail

# ----------------------------------------------------------------
# Constants
# ----------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
COMPOSE_TEST_FILE="${SCRIPT_DIR}/docker-compose.test.yml"
VERSION_FILE="${SCRIPT_DIR}/VERSION"
PROJECT_VERSION="$(cat "${VERSION_FILE}" 2>/dev/null | tr -d '[:space:]' || echo 'unknown')"
RELEASES_DIR="${SCRIPT_DIR}/releases"
RELEASE_ORDER_FILE="${RELEASES_DIR}/order.txt"

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
info()    { printf '\033[0;34m[INFO]\033[0m  %s\n' "$*"; }
success() { printf '\033[0;32m[OK]\033[0m    %s\n' "$*"; }
warn()    { printf '\033[0;33m[WARN]\033[0m  %s\n' "$*"; }
error()   { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

print_header() {
  cat <<EOF
--------------------------------------------
 ____  _
|  _ \| | __ _ _ __   ___
| |_) | |/ _\` | '_ \ / _ \
|  __/| | (_| | | | |  __/
|_|   |_|\__,_|_| |_|\___|

  Community Edition — Docker Compose Setup
  Config version : ${PROJECT_VERSION}
--------------------------------------------
EOF
}

check_prerequisites() {
  info "Checking prerequisites…"

  # Docker
  if ! command -v docker &>/dev/null; then
    error "Docker is not installed. Install it from https://docs.docker.com/engine/install/"
  fi

  DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
  DOCKER_MAJOR=$(echo "$DOCKER_VERSION" | cut -d. -f1)
  if [[ "$DOCKER_MAJOR" -lt 24 ]]; then
    warn "Docker $DOCKER_VERSION detected; version ≥ 24 is recommended."
  fi

  # Docker Compose v2 (integrated plugin — `docker compose`)
  if ! docker compose version &>/dev/null; then
    error "Docker Compose v2 plugin not found. Run: apt install docker-compose-plugin"
  fi

  success "All prerequisites satisfied."
}

# Generate a random hex secret of $1 bytes (produces 2*$1 hex chars)
generate_secret() {
  local length="${1:-32}"
  tr -dc 'a-f0-9' < /dev/urandom | head -c "$((length * 2))" 2>/dev/null || \
    python3 -c "import secrets; print(secrets.token_hex($length))"
}

init_env() {
  if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists — skipping generation."
    return
  fi

  info "Generating .env from .env.example…"
  cp "$ENV_EXAMPLE" "$ENV_FILE"

  # Auto-generate secrets
  local secret_key
  secret_key=$(generate_secret 50)
  local live_secret_key
  live_secret_key=$(generate_secret 32)
  local pg_pass
  pg_pass=$(generate_secret 16)
  local mq_pass
  mq_pass=$(generate_secret 16)
  local minio_user
  minio_user=$(generate_secret 10)
  local minio_pass
  minio_pass=$(generate_secret 20)

  sed -i \
    -e "s|CHANGE_ME_USE_A_LONG_RANDOM_STRING|${secret_key}|g" \
    -e "s|CHANGE_ME_LIVE_SERVER_SECRET_KEY|${live_secret_key}|g" \
    -e "s|CHANGE_ME_DB_PASSWORD|${pg_pass}|g" \
    -e "s|CHANGE_ME_MQ_PASSWORD|${mq_pass}|g" \
    -e "s|CHANGE_ME_MINIO_USER|${minio_user}|g" \
    -e "s|CHANGE_ME_MINIO_PASSWORD|${minio_pass}|g" \
    "$ENV_FILE"

  warn "Secrets generated and written to .env"
  warn "Review .env and set DOMAIN_NAME / WEB_URL before starting."
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# ----------------------------------------------------------------
# Backup / migration helpers
# ----------------------------------------------------------------

# Read a single KEY=VALUE from .env (first match).  Empty if absent.
read_env_value() {
  local key="$1"
  grep -m1 "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

# Apply a releases/<version>.env override file onto .env.
apply_release_file() {
  local file="$1"
  local line key value
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    [[ -z "$key" ]] && continue
    value="${line#*=}"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
      printf '%s\n' "$line" >> "$ENV_FILE"
    fi
  done < "$file"
}

# Create a full backup: PostgreSQL dump + MinIO file volume + .env copy.
# The backup is stored under BACKUP_DIR/<timestamp>/.
do_backup() {
  local ts dest backup_root project pg_user pg_db vol retention
  # Nanosecond granularity so rapid successive backups (e.g. during
  # `upgrade`) never collide into the same directory.
  ts="$(date +%Y%m%d-%H%M%S-%N)"
  backup_root="$(read_env_value BACKUP_DIR)"
  backup_root="${backup_root:-backups}"
  dest="${SCRIPT_DIR}/${backup_root}/${ts}"
  mkdir -p "${dest}/db" "${dest}/files"

  project="$(read_env_value COMPOSE_PROJECT_NAME)"
  project="${project:-plane-prod}"
  pg_user="$(read_env_value POSTGRES_USER)"; pg_user="${pg_user:-plane}"
  pg_db="$(read_env_value POSTGRES_DB)";     pg_db="${pg_db:-plane}"
  vol="${project}_miniodata"

  info "Backing up PostgreSQL database (${pg_db})…"
  compose exec -T plane-db pg_dump \
    -U "$pg_user" -d "$pg_db" \
    --format=custom --no-owner --no-privileges \
    > "${dest}/db/plane.dump"

  info "Backing up uploaded files (MinIO volume: ${vol})…"
  docker run --rm -v "${vol}:/source:ro" alpine:3.20 \
    tar czf - -C /source . \
    > "${dest}/files/minio-data.tgz"

  # Keep a copy of .env for reference and restore (it contains secrets).
  if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "${dest}/.env"
  fi

  {
    echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "project=${project}"
    echo "app_release=$(read_env_value APP_RELEASE)"
    echo "postgres_user=${pg_user}"
    echo "postgres_db=${pg_db}"
    echo "minio_volume=${vol}"
  } > "${dest}/manifest.txt"

  success "Backup created: ${dest}"

  # Prune backups older than the retention window.
  retention="$(read_env_value BACKUP_RETENTION_DAYS)"
  if [[ "$retention" =~ ^[0-9]+$ && "$retention" -gt 0 ]]; then
    find "${SCRIPT_DIR}/${backup_root}" -mindepth 1 -maxdepth 1 -type d \
      -mtime "+${retention}" -exec rm -rf {} + 2>/dev/null || true
  fi
}

# ----------------------------------------------------------------
# Commands
# ----------------------------------------------------------------
cmd_pull() {
  info "Pulling latest images…"
  compose pull
  success "Images up to date."
}

cmd_migrate() {
  info "Running database migrations…"
  compose run --rm migrator
  success "Migrations complete."
}

# backup — dump PostgreSQL and archive the MinIO file volume.
cmd_backup() {
  if [[ ! -f "$ENV_FILE" ]]; then
    error ".env not found.  Run './setup.sh install' first."
  fi
  if ! do_backup; then
    error "Backup failed."
  fi
}

# restore — restore a backup created by 'backup'.
cmd_restore() {
  local src="${1:-}"
  if [[ -z "$src" ]]; then
    error "Usage: ./setup.sh restore <backup-dir>"
  fi
  if [[ ! -f "$ENV_FILE" ]]; then
    error ".env not found.  Run './setup.sh install' first."
  fi
  if [[ ! -d "$src" ]]; then
    error "Backup directory not found: $src"
  fi

  local project pg_user pg_db vol
  project="$(read_env_value COMPOSE_PROJECT_NAME)"
  project="${project:-plane-prod}"
  pg_user="$(read_env_value POSTGRES_USER)"; pg_user="${pg_user:-plane}"
  pg_db="$(read_env_value POSTGRES_DB)";     pg_db="${pg_db:-plane}"
  vol="${project}_miniodata"

  info "Ensuring database service is running…"
  compose up -d plane-db

  if [[ -f "${src}/db/plane.dump" ]]; then
    info "Restoring PostgreSQL database…"
    compose exec -T plane-db pg_restore \
      -U "$pg_user" -d "$pg_db" \
      --clean --if-exists --no-owner --no-privileges \
      < "${src}/db/plane.dump"
    success "Database restored."
  else
    warn "No database dump at ${src}/db/plane.dump — skipping DB restore."
  fi

  if [[ -f "${src}/files/minio-data.tgz" ]]; then
    info "Restoring uploaded files (MinIO volume: ${vol})…"
    docker run --rm -i -v "${vol}:/target" alpine:3.20 \
      tar xzf - -C /target \
      < "${src}/files/minio-data.tgz"
    success "Files restored."
  else
    warn "No file backup at ${src}/files/minio-data.tgz — skipping file restore."
  fi
}

# upgrade — stepwise migration through releases/order.txt.
# Backs up before each step, applies the per-release override, pulls images,
# runs migrations, restarts the stack, and (by default) runs the test suite.
cmd_upgrade() {
  local target="" run_tests=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --to)
        [[ -z "${2:-}" ]] && error "--to requires a version"
        target="$2"; shift 2 ;;
      --skip-tests)
        run_tests=0; shift ;;
      *) error "Unknown option for upgrade: $1  (run './setup.sh help')" ;;
    esac
  done

  if [[ ! -f "$ENV_FILE" ]]; then
    error ".env not found.  Run './setup.sh install' first."
  fi
  if [[ ! -f "$RELEASE_ORDER_FILE" ]]; then
    error "Release order file not found: $RELEASE_ORDER_FILE"
  fi

  # Build the ordered release list (skip blanks and comments).
  local releases=()
  local line
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    releases+=("$line")
  done < "$RELEASE_ORDER_FILE"
  if (( ${#releases[@]} == 0 )); then
    error "Release order file is empty: $RELEASE_ORDER_FILE"
  fi

  local current
  current="$(read_env_value APP_RELEASE)"
  [[ -z "$current" ]] && error "APP_RELEASE is not set in .env"

  local cur_idx=-1 tgt_idx=-1 i
  for i in "${!releases[@]}"; do
    [[ "${releases[$i]}" == "$current" ]] && cur_idx="$i"
    [[ -n "$target" && "${releases[$i]}" == "$target" ]] && tgt_idx="$i"
  done
  [[ -z "$target" ]] && tgt_idx=$(( ${#releases[@]} - 1 ))
  [[ "$cur_idx" -lt 0 ]] && error "Current APP_RELEASE '${current}' is not listed in ${RELEASE_ORDER_FILE}."
  [[ "$tgt_idx" -lt 0 ]] && error "Target version '${target}' is not listed in ${RELEASE_ORDER_FILE}."

  if [[ "$tgt_idx" -le "$cur_idx" ]]; then
    info "Already at (or beyond) '${releases[$tgt_idx]}' — nothing to migrate."
    return 0
  fi

  info "Migration path: ${current} → ${releases[$tgt_idx]}"
  info "Number of steps: $(( tgt_idx - cur_idx ))"
  if [[ "$run_tests" -eq 1 ]]; then
    info "The integration test suite will run after every step."
  else
    warn "Tests disabled (--skip-tests)."
  fi

  local step rel_file
  for (( i = cur_idx + 1; i <= tgt_idx; i++ )); do
    step="${releases[$i]}"
    echo
    info "=============================================================="
    info "Step $(( i - cur_idx ))/$(( tgt_idx - cur_idx )): upgrade to ${step}"
    info "=============================================================="

    info "Creating pre-migration backup…"
    if ! do_backup; then
      error "Backup failed before upgrading to ${step} — aborting (no change applied)."
    fi

    rel_file="${RELEASES_DIR}/${step}.env"
    if [[ -f "$rel_file" ]]; then
      info "Applying configuration override: releases/${step}.env"
      apply_release_file "$rel_file"
    else
      warn "Missing releases/${step}.env — falling back to APP_RELEASE=${step}."
      if grep -q '^APP_RELEASE=' "$ENV_FILE"; then
        sed -i "s|^APP_RELEASE=.*|APP_RELEASE=${step}|" "$ENV_FILE"
      else
        echo "APP_RELEASE=${step}" >> "$ENV_FILE"
      fi
    fi

    info "Pulling images for ${step}…"
    compose pull

    info "Running database migrations…"
    compose run --rm migrator

    info "Starting / updating the stack…"
    compose up -d --remove-orphans

    if [[ "$run_tests" -eq 1 ]]; then
      info "Running integration tests…"
      if ! cmd_test; then
        error "Integration tests failed after upgrading to ${step} — aborting migration."
      fi
    fi

    success "Step to ${step} complete."
  done

  echo
  success "Migration finished.  Plane is now on APP_RELEASE=${releases[$tgt_idx]}."
  info "Verify with: ./setup.sh status"
}

# install — first-time setup: prerequisites check, .env creation, image pull.
# No containers are started.  The user must review .env and configure nginx
# before calling 'start'.
# Accepts optional flags: --project-name / -p, --domain / -d, --port / -P
cmd_install() {
  local project_name="" domain="" port=""

  # Parse optional arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project-name|-p)
        [[ -z "${2:-}" ]] && error "--project-name requires a value"
        project_name="$2"; shift 2 ;;
      --domain|-d)
        [[ -z "${2:-}" ]] && error "--domain requires a value"
        domain="$2"; shift 2 ;;
      --port|-P)
        [[ -z "${2:-}" ]] && error "--port requires a value"
        port="$2"; shift 2 ;;
      *) error "Unknown option for install: $1  (run './setup.sh help')" ;;
    esac
  done

  check_prerequisites
  init_env

  # Apply CLI overrides to .env ----------------------------------------
  if [[ -n "$project_name" ]]; then
    sed -i "s|^COMPOSE_PROJECT_NAME=.*|COMPOSE_PROJECT_NAME=${project_name}|" "$ENV_FILE"
    info "COMPOSE_PROJECT_NAME → ${project_name}"
  fi

  if [[ -n "$domain" ]]; then
    sed -i \
      -e "s|^DOMAIN_NAME=.*|DOMAIN_NAME=${domain}|" \
      -e "s|^WEB_URL=.*|WEB_URL=https://${domain}|" \
      -e "s|^CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=https://${domain}|" \
      "$ENV_FILE"
    info "DOMAIN_NAME        → ${domain}"
    info "WEB_URL            → https://${domain}"
    info "CORS_ALLOWED_ORIGINS → https://${domain}"
  fi

  if [[ -n "$port" ]]; then
    sed -i "s|^LISTEN_HTTP_PORT=.*|LISTEN_HTTP_PORT=${port}|" "$ENV_FILE"
    info "LISTEN_HTTP_PORT   → ${port}"
  fi
  # --------------------------------------------------------------------

  cmd_pull

  success "Installation complete.  No containers have been started."
  info "Next steps:"
  info "  1. Review and adjust .env (especially WEB_URL, DOMAIN_NAME, LISTEN_HTTP_PORT)."
  info "  2. Configure nginx to proxy 127.0.0.1:\$(grep -m1 LISTEN_HTTP_PORT ${ENV_FILE} | cut -d= -f2-)."
  info "  3. Run: ./setup.sh start"
}

# start — run migrations then bring the full stack up.
# install must have been completed and .env validated before calling this.
cmd_start() {
  if [[ ! -f "$ENV_FILE" ]]; then
    error ".env not found.  Run './setup.sh install' first."
  fi
  info "Running database migrations…"
  compose run --rm migrator
  info "Starting Plane stack…"
  compose up -d --remove-orphans
  local port
  port=$(grep -m1 '^LISTEN_HTTP_PORT=' "$ENV_FILE" | cut -d= -f2-)
  success "Plane is running."
  info "Internal proxy port: ${port}  (nginx → 127.0.0.1:${port})"
}

cmd_stop() {
  info "Stopping Plane stack…"
  compose down
  success "Stack stopped."
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  compose ps
}

cmd_test() {
  info "Running integration tests inside Docker…"
  docker compose --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" -f "$COMPOSE_TEST_FILE" \
    run --rm test-runner
}

cmd_logs() {
  local service="${1:-}"
  if [[ -n "$service" ]]; then
    compose logs --follow "$service"
  else
    compose logs --follow
  fi
}

# ----------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------
print_header

COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
  install) cmd_install "$@" ;;
  start)   cmd_start   ;;
  stop)    cmd_stop    ;;
  restart) cmd_restart ;;
  pull)    cmd_pull    ;;
  migrate) cmd_migrate ;;
  backup)  cmd_backup  ;;
  restore) cmd_restore "$@" ;;
  upgrade) cmd_upgrade "$@" ;;
  status)  cmd_status  ;;
  test)    cmd_test    ;;
  logs)    cmd_logs "$@" ;;
  help|--help|-h)
    cat <<'HELP'
Usage: ./setup.sh <command> [options]

Commands:
  install   First-time setup: check prerequisites, create .env, pull images.
            Does NOT start any container — validate .env and nginx first.
            Options:
              --project-name, -p NAME   Set COMPOSE_PROJECT_NAME
              --domain, -d DOMAIN       Set DOMAIN_NAME (+ WEB_URL / CORS)
              --port, -P PORT           Set LISTEN_HTTP_PORT

  start     Run database migrations then start the stack.
            Assumes 'install' has been completed and .env is validated.
  stop      Stop and remove containers (data volumes are preserved).
  restart   Equivalent to stop + start.
  pull      Pull latest Plane images.
  migrate   Run database migrations only.
  backup    Back up the PostgreSQL database and uploaded files (MinIO).
  restore   Restore a backup created by 'backup' (usage: restore <backup-dir>).
  upgrade   Stepwise migration to a newer Plane release.
            Options:
              --to VERSION    Target release (default: latest in releases/order.txt)
              --skip-tests    Do not run the test suite after each step
            See docs/MIGRATION.md for the full procedure.
  status    Show running container status.
  test      Run the integration test suite inside Docker.
  logs      Stream logs (optionally pass a service name as argument).
  help      Show this help message.

Typical first-time setup:
  ./setup.sh install --project-name plane-prod --domain plane.example.com --port 8091
  # review .env, configure nginx, then:
  ./setup.sh start

Stepwise migration (e.g. v1.2.3 → v1.4.1):
  ./setup.sh backup            # safety net before starting
  ./setup.sh upgrade           # walks releases/order.txt, backs up before each step
HELP
    ;;
  *)
    error "Unknown command: $COMMAND  (run './setup.sh help' for usage)"
    ;;
esac
