#!/usr/bin/env bash
# Recreate the Plenora E2E fixture stack and attach the running Plenora app.
#
# This script is intentionally scoped to the test environment:
#   - it may remove E2E containers/volumes;
#   - it does not remove Plenora application volumes;
#   - it links backend/worker/beat to the E2E Docker network.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
E2E_COMPOSE="${SCRIPT_DIR}/docker-compose.yml"
E2E_ENV="${SCRIPT_DIR}/.env"
E2E_ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
TEST_ENV="${SCRIPT_DIR}/test/.env"
TEST_ENV_EXAMPLE="${SCRIPT_DIR}/test/.env.example"

DRY_RUN=0
RESET=1
BUILD=1
CROSS_DB=1
LINK_PLENORA=1
SEED_ADMIN=1
WAIT_SERVICES=1
PLENORA_WORKER_SERVICE="${PLENORA_WORKER_SERVICE:-worker}"
PLENORA_COMPOSE_PROJECT="${PLENORA_COMPOSE_PROJECT:-plenora}"
PLENORA_TEST_NETWORK="${PLENORA_TEST_NETWORK:-}"
PLENORA_E2E_USERNAME="${PLENORA_E2E_USERNAME:-}"
PLENORA_E2E_PASSWORD="${PLENORA_E2E_PASSWORD:-}"
PLENORA_E2E_GROUP_ID="${PLENORA_E2E_GROUP_ID:-root}"
DOCKER_CMD=()

usage() {
    cat <<'USAGE'
Recreate Plenora E2E fixtures and attach the running Plenora app.

Usage:
  bash backend/test-e2e-v2/recreate.sh
  bash backend/test-e2e-v2/recreate.sh --no-cross-db
  bash backend/test-e2e-v2/recreate.sh --no-reset
  bash backend/test-e2e-v2/recreate.sh --dry-run

Default behavior:
  - copies backend/test-e2e-v2/.env from .env.example if missing;
  - copies backend/test-e2e-v2/test/.env from .env.example if missing;
  - removes and recreates E2E fixture containers and volumes;
  - starts Postgres, fake REST, FTP, SFTP, MinIO;
  - starts cross-db fixtures too: MySQL, SQL Server, Oracle;
  - connects Plenora backend/worker/beat containers to the E2E network;
  - creates/updates a superadmin test user in Plenora.

Options:
  --no-cross-db
      Start only the light fixture set: Postgres, fake REST, FTP, SFTP, MinIO.

  --no-reset
      Do not run docker compose down -v before starting fixtures.

  --no-build
      Do not rebuild fixture images.

  --no-link
      Do not connect Plenora backend/worker/beat to the E2E network.

  --no-seed-admin
      Do not create/update the E2E superadmin user.

  --no-wait
      Do not wait for fixture healthchecks.

  --dry-run
      Print actions without executing them.

Environment:
  PLENORA_TEST_NETWORK
      Docker network for E2E fixtures. Default comes from .env or plenora-test.

  PLENORA_COMPOSE_PROJECT
      Compose project name for the running Plenora app. Default: plenora.

  PLENORA_E2E_USERNAME / PLENORA_E2E_PASSWORD
      Test superadmin credentials. If omitted, values are read from
      backend/test-e2e-v2/.env, then backend/test-e2e-v2/test/.env, then
      default to plenora_admin / plenora_admin_2026!.

  PLENORA_E2E_GROUP_ID
      Group id for the test superadmin. Default: root.
USAGE
}

log() {
    printf '%s\n' "$*"
}

log_step() {
    printf '\n==> %s\n' "$*"
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 2
}

run() {
    log "+ $*"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    "$@"
}

run_docker() {
    if [ "${#DOCKER_CMD[@]}" -eq 0 ]; then
        die "Docker command was not selected."
    fi
    log "+ ${DOCKER_CMD[*]} $*"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    "${DOCKER_CMD[@]}" "$@"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-cross-db)
            CROSS_DB=0
            shift
            ;;
        --no-reset)
            RESET=0
            shift
            ;;
        --no-build)
            BUILD=0
            shift
            ;;
        --no-link)
            LINK_PLENORA=0
            shift
            ;;
        --no-seed-admin)
            SEED_ADMIN=0
            shift
            ;;
        --no-wait)
            WAIT_SERVICES=0
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

assert_repo_root() {
    [ -f "${REPO_ROOT}/docker-compose.yml" ] || die "docker-compose.yml not found from ${REPO_ROOT}."
    [ -f "$E2E_COMPOSE" ] || die "E2E compose file not found: ${E2E_COMPOSE}."
}

select_docker_command() {
    if [ "$DRY_RUN" -eq 1 ]; then
        DOCKER_CMD=(docker)
        return
    fi
    command -v docker >/dev/null 2>&1 || die "docker not found."
    if docker version >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
        return
    fi
    if command -v sudo >/dev/null 2>&1; then
        DOCKER_CMD=(sudo docker)
        return
    fi
    die "docker exists but the daemon is not reachable."
}

env_value_from_file() {
    local file="$1"
    local key="$2"
    [ -f "$file" ] || return 0
    awk -F= -v key="$key" '
        /^[[:space:]]*#/ { next }
        {
            k = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
            if (k == key) {
                sub(/^[^=]*=/, "")
                gsub(/^[[:space:]]+|[[:space:]]+$/, "")
                print
                exit
            }
        }
    ' "$file"
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    local tmp

    log "+ set ${key} in ${file#${REPO_ROOT}/}"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi

    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { done = 0 }
        /^[[:space:]]*#/ { print; next }
        {
            line = $0
            split(line, parts, "=")
            k = parts[1]
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
            if (k == key) {
                print key "=" value
                done = 1
                next
            }
            print
        }
        END {
            if (done == 0) {
                print key "=" value
            }
        }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
}

ensure_env_files() {
    log_step "E2E env files"
    if [ ! -f "$E2E_ENV" ]; then
        run cp "$E2E_ENV_EXAMPLE" "$E2E_ENV"
    else
        log "${E2E_ENV#${REPO_ROOT}/} already exists."
    fi

    if [ ! -f "$TEST_ENV" ]; then
        run cp "$TEST_ENV_EXAMPLE" "$TEST_ENV"
    else
        log "${TEST_ENV#${REPO_ROOT}/} already exists."
    fi

    local network_from_env
    network_from_env="$(env_value_from_file "$E2E_ENV" PLENORA_TEST_NETWORK)"
    PLENORA_TEST_NETWORK="${PLENORA_TEST_NETWORK:-${network_from_env:-plenora-test}}"

    local username password
    username="$(env_value_from_file "$E2E_ENV" PLENORA_E2E_USERNAME)"
    [ -n "$username" ] || username="$(env_value_from_file "$TEST_ENV" PLENORA_E2E_USERNAME)"
    password="$(env_value_from_file "$E2E_ENV" PLENORA_E2E_PASSWORD)"
    [ -n "$password" ] || password="$(env_value_from_file "$TEST_ENV" PLENORA_E2E_PASSWORD)"

    PLENORA_E2E_USERNAME="${PLENORA_E2E_USERNAME:-${username:-plenora_admin}}"
    PLENORA_E2E_PASSWORD="${PLENORA_E2E_PASSWORD:-${password:-plenora_admin_2026!}}"

    set_env_value "$E2E_ENV" PLENORA_TEST_NETWORK "$PLENORA_TEST_NETWORK"
    set_env_value "$E2E_ENV" PLENORA_E2E_USERNAME "$PLENORA_E2E_USERNAME"
    set_env_value "$E2E_ENV" PLENORA_E2E_PASSWORD "$PLENORA_E2E_PASSWORD"
    set_env_value "$TEST_ENV" PLENORA_E2E_USERNAME "$PLENORA_E2E_USERNAME"
    set_env_value "$TEST_ENV" PLENORA_E2E_PASSWORD "$PLENORA_E2E_PASSWORD"

    log "E2E network: ${PLENORA_TEST_NETWORK}"
    log "E2E username: ${PLENORA_E2E_USERNAME}"
}

compose_e2e() {
    if [ "$CROSS_DB" -eq 1 ]; then
        run_docker compose --env-file "$E2E_ENV" -f "$E2E_COMPOSE" --profile cross-db "$@"
    else
        run_docker compose --env-file "$E2E_ENV" -f "$E2E_COMPOSE" "$@"
    fi
}

remove_stale_minio_container() {
    if [ "$RESET" -ne 1 ]; then
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ ${DOCKER_CMD[*]} rm -f plenora-e2e-minio  # if it is a stale standalone container"
        return 0
    fi
    if ! "${DOCKER_CMD[@]}" inspect plenora-e2e-minio >/dev/null 2>&1; then
        return 0
    fi
    local project
    project="$("${DOCKER_CMD[@]}" inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' plenora-e2e-minio 2>/dev/null || true)"
    if [ "$project" != "plenora-test" ]; then
        run_docker rm -f plenora-e2e-minio
    fi
}

start_fixtures() {
    log_step "Recreate E2E fixtures"
    run_docker compose --env-file "$E2E_ENV" -f "$E2E_COMPOSE" config --quiet
    if [ "$RESET" -eq 1 ]; then
        run_docker compose --env-file "$E2E_ENV" -f "$E2E_COMPOSE" --profile cross-db down -v --remove-orphans
        remove_stale_minio_container
    fi

    local up_args=(up -d)
    if [ "$BUILD" -eq 1 ]; then
        up_args+=(--build)
    fi
    compose_e2e "${up_args[@]}"
}

wait_for_container_health() {
    local name="$1"
    local timeout_seconds="$2"
    local started
    local status

    started="$(date +%s)"
    while true; do
        status="$("${DOCKER_CMD[@]}" inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true)"
        if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
            log "${name}: ${status}"
            return 0
        fi
        if [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
            die "${name} ended with status ${status}."
        fi
        if [ $(( "$(date +%s)" - started )) -ge "$timeout_seconds" ]; then
            die "timeout waiting for ${name}; last status=${status:-missing}."
        fi
        sleep 3
    done
}

wait_for_container_exit_success() {
    local name="$1"
    local timeout_seconds="$2"
    local started
    local status
    local exit_code

    started="$(date +%s)"
    while true; do
        status="$("${DOCKER_CMD[@]}" inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
        exit_code="$("${DOCKER_CMD[@]}" inspect -f '{{.State.ExitCode}}' "$name" 2>/dev/null || true)"
        if [ "$status" = "exited" ] && [ "$exit_code" = "0" ]; then
            log "${name}: exited 0"
            return 0
        fi
        if [ "$status" = "exited" ] && [ "$exit_code" != "0" ]; then
            die "${name} exited with code ${exit_code}."
        fi
        if [ $(( "$(date +%s)" - started )) -ge "$timeout_seconds" ]; then
            die "timeout waiting for ${name}; last status=${status:-missing} exit=${exit_code:-unknown}."
        fi
        sleep 2
    done
}

wait_for_fixtures() {
    if [ "$DRY_RUN" -eq 1 ]; then
        log_step "Wait E2E fixtures"
        log "+ wait for fixture healthchecks  # skipped in dry-run"
        return 0
    fi
    if [ "$WAIT_SERVICES" -ne 1 ]; then
        return 0
    fi

    log_step "Wait E2E fixtures"
    wait_for_container_health plenora-test-postgres 180
    wait_for_container_health plenora-test-fake-rest 120
    wait_for_container_health plenora-test-ftp 120
    wait_for_container_health plenora-test-sftp 120
    wait_for_container_health plenora-e2e-minio 120
    wait_for_container_exit_success plenora-e2e-minio-init 180
    if [ "$CROSS_DB" -eq 1 ]; then
        wait_for_container_health plenora-test-mysql 240
        wait_for_container_health plenora-test-sqlserver 300
        wait_for_container_health plenora-test-oracle 900
    fi
}

container_name() {
    "${DOCKER_CMD[@]}" inspect -f '{{.Name}}' "$1" 2>/dev/null | sed 's#^/##'
}

is_connected_to_network() {
    local container="$1"
    local network="$2"
    local name
    name="$(container_name "$container")"
    [ -n "$name" ] || return 1
    "${DOCKER_CMD[@]}" network inspect -f '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}' "$network" 2>/dev/null | grep -Fx "$name" >/dev/null 2>&1
}

connect_container_to_network() {
    local container="$1"
    local network="$2"
    [ -n "$container" ] || return 0
    if is_connected_to_network "$container" "$network"; then
        log "$(container_name "$container") already on ${network}."
        return 0
    fi
    run_docker network connect "$network" "$container"
}

compose_root_ps_quiet() {
    local service="$1"
    local ids
    ids="$(
        cd "$REPO_ROOT"
        "${DOCKER_CMD[@]}" compose ps -q "$service" 2>/dev/null || true
    )"
    if [ -n "$ids" ]; then
        printf '%s\n' "$ids"
        return 0
    fi
    "${DOCKER_CMD[@]}" ps \
        --filter "label=com.docker.compose.project=${PLENORA_COMPOSE_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}" \
        -q 2>/dev/null || true
}

link_plenora() {
    if [ "$LINK_PLENORA" -ne 1 ]; then
        return 0
    fi

    log_step "Link Plenora app to E2E network"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ connect backend/worker/beat/frontend containers to ${PLENORA_TEST_NETWORK}  # if running"
        return 0
    fi
    if [ "$DRY_RUN" -eq 0 ]; then
        "${DOCKER_CMD[@]}" network inspect "$PLENORA_TEST_NETWORK" >/dev/null 2>&1 || die "network ${PLENORA_TEST_NETWORK} not found."
    fi

    local backend_id beat_id frontend_id worker_ids
    backend_id="$(compose_root_ps_quiet backend)"
    beat_id="$(compose_root_ps_quiet beat)"
    frontend_id="$(compose_root_ps_quiet frontend)"
    worker_ids="$(compose_root_ps_quiet "$PLENORA_WORKER_SERVICE")"

    if [ -z "$backend_id" ]; then
        log "WARN: Plenora backend is not running. Start Plenora first, then rerun this script or run:"
        log "  docker compose up -d postgres redis backend worker beat frontend"
        return 0
    fi

    connect_container_to_network "$backend_id" "$PLENORA_TEST_NETWORK"
    connect_container_to_network "$beat_id" "$PLENORA_TEST_NETWORK"
    connect_container_to_network "$frontend_id" "$PLENORA_TEST_NETWORK"
    for worker_id in $worker_ids; do
        connect_container_to_network "$worker_id" "$PLENORA_TEST_NETWORK"
    done
}

seed_e2e_admin() {
    if [ "$SEED_ADMIN" -ne 1 ]; then
        return 0
    fi

    log_step "Seed E2E superadmin"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ seed or update ${PLENORA_E2E_USERNAME} in running Plenora backend  # skipped in dry-run"
        return 0
    fi
    local backend_id
    backend_id="$(compose_root_ps_quiet backend)"
    if [ -z "$backend_id" ]; then
        log "WARN: backend is not running; admin seed skipped."
        return 0
    fi
    if [ "${#PLENORA_E2E_PASSWORD}" -lt 12 ]; then
        die "PLENORA_E2E_PASSWORD must be at least 12 characters."
    fi

    local script
    script='import os, uuid
from app.core.auth import db
from app.shared.password_hashing import hash_password

username = os.environ["PLENORA_E2E_USERNAME"]
password = os.environ["PLENORA_E2E_PASSWORD"]
group_id = os.environ.get("PLENORA_E2E_GROUP_ID") or "root"

if not username.strip():
    raise SystemExit("PLENORA_E2E_USERNAME is empty")
if len(password) < 12:
    raise SystemExit("PLENORA_E2E_PASSWORD must be at least 12 characters")
if db.get_group(group_id) is None:
    db.create_group(group_id, "Root", "E2E root group")

existing = db.get_user_by_username(username)
if existing is None:
    db.create_user(
        user_id=f"e2e-{uuid.uuid4().hex[:12]}",
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role="superadmin",
        email="e2e@local.test",
    )
else:
    db.update_user(existing.id, is_active=True, role="superadmin", group_id=group_id, email="e2e@local.test")
    db.update_user_password(existing.id, hash_password(password))

print(f"E2E superadmin ready: username={username} role=superadmin group_id={group_id}")'

    log "+ ${DOCKER_CMD[*]} exec -e PLENORA_E2E_USERNAME -e PLENORA_E2E_PASSWORD -e PLENORA_E2E_GROUP_ID <backend> python -c <seed-script>"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    "${DOCKER_CMD[@]}" exec \
        -e PLENORA_E2E_USERNAME="$PLENORA_E2E_USERNAME" \
        -e PLENORA_E2E_PASSWORD="$PLENORA_E2E_PASSWORD" \
        -e PLENORA_E2E_GROUP_ID="$PLENORA_E2E_GROUP_ID" \
        "$backend_id" python -c "$script"
}

smoke_plenora_link() {
    if [ "$LINK_PLENORA" -ne 1 ] || [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi

    log_step "Smoke Plenora link"
    local backend_id
    backend_id="$(compose_root_ps_quiet backend)"
    if [ -z "$backend_id" ]; then
        log "WARN: backend is not running; smoke skipped."
        return 0
    fi
    run_docker exec "$backend_id" python -c "import socket; socket.getaddrinfo('plenora-test-postgres', 5432); socket.getaddrinfo('plenora-test-fake-rest', 8080); socket.getaddrinfo('plenora-e2e-minio', 9000); print('E2E DNS ok')"
    if command -v curl >/dev/null 2>&1; then
        run curl -fsS http://127.0.0.1:5004/health
        log ""
    fi
}

assert_repo_root
select_docker_command
ensure_env_files
start_fixtures
wait_for_fixtures
link_plenora
seed_e2e_admin
smoke_plenora_link

log_step "E2E environment ready"
log "Fixture compose:"
if [ "$CROSS_DB" -eq 1 ]; then
    log "  docker compose --env-file backend/test-e2e-v2/.env -f backend/test-e2e-v2/docker-compose.yml --profile cross-db ps"
else
    log "  docker compose --env-file backend/test-e2e-v2/.env -f backend/test-e2e-v2/docker-compose.yml ps"
fi
log "Run tests:"
log "  PLENORA_E2E_CROSS_DB=${CROSS_DB} python backend/test-e2e-v2/test/run_all.py"
