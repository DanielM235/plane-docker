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
  status    Show running container status.
  test      Run the integration test suite inside Docker.
  logs      Stream logs (optionally pass a service name as argument).
  help      Show this help message.

Typical first-time setup:
  ./setup.sh install --project-name plane-prod --domain plane.example.com --port 8091
  # review .env, configure nginx, then:
  ./setup.sh start
HELP
    ;;
  *)
    error "Unknown command: $COMMAND  (run './setup.sh help' for usage)"
    ;;
esac
