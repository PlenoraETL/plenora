#!/usr/bin/env bash
# Plenora-ETL - single-server Docker installer for Linux.
#
# Scope:
#   - validates host prerequisites;
#   - optionally installs Docker Engine + Compose plugin on Debian/Ubuntu;
#   - creates .env.docker from .env.docker.example without overwriting secrets;
#   - builds and starts the single-server compose stack;
#   - verifies Python DB providers, health, readiness, frontend, and worker ping.
#
# It intentionally does not run destructive cleanup commands. The normal stop
# path remains `docker compose stop`; volume removal is a manual operator action.

set -uo pipefail

DRY_RUN=0
CHECK_ONLY=0
INSTALL_DEPS=0
SKIP_SMOKE=0
SKIP_ADMIN_BOOTSTRAP=0
REQUIRE_SQLSERVER_ODBC=0
FRONTEND_VITE_API_URL="${FRONTEND_VITE_API_URL:-}"
BOOTSTRAP_ADMIN_USERNAME="${PLENORA_BOOTSTRAP_ADMIN_USERNAME:-${PLENORA_ADMIN_USERNAME:-}}"
BOOTSTRAP_ADMIN_PASSWORD="${PLENORA_BOOTSTRAP_ADMIN_PASSWORD:-${PLENORA_ADMIN_PASSWORD:-}}"
BOOTSTRAP_ADMIN_GROUP_ID="${PLENORA_BOOTSTRAP_ADMIN_GROUP_ID:-root}"
PLENORA_WORKER_REPLICAS="${PLENORA_WORKER_REPLICAS:-}"
PLENORA_DOCKER_NETWORK="${PLENORA_DOCKER_NETWORK:-plenora}"
PLENORA_DOCKER_SUBNET="${PLENORA_DOCKER_SUBNET:-172.28.0.0/16}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
DOCKER_CMD=()

export PLENORA_DOCKER_NETWORK
export PLENORA_DOCKER_SUBNET
export COMPOSE_FILE

usage() {
    cat <<'USAGE'
Plenora-ETL single-server Docker installer for Linux.

Usage:
  bash scripts/install-docker.sh
  bash scripts/install-docker.sh --check
  bash scripts/install-docker.sh --dry-run
  bash scripts/install-docker.sh --install-deps
  bash scripts/install-docker.sh --frontend-api-url http://<host>:5004
  bash scripts/install-docker.sh --require-sqlserver-odbc
  bash scripts/install-docker.sh --skip-smoke
  bash scripts/install-docker.sh --admin-username admin
  bash scripts/install-docker.sh --worker-replicas 2

Options:
  --check
      Preflight only. Does not write .env.docker and does not start containers.

  --dry-run
      Print the actions that would run without executing them.

  --install-deps
      On Debian/Ubuntu, install Git, curl, Python 3, Docker Engine, and the
      Docker Compose v2 plugin through apt. Requires sudo/root.

  --frontend-api-url URL
      Sets FRONTEND_VITE_API_URL for the frontend build.

  --require-sqlserver-odbc
      Fail the provider check unless the backend image exposes Microsoft ODBC
      Driver 18 or 17 for SQL Server through pyodbc.drivers().

  --skip-smoke
      Build and start the stack, but skip HTTP and worker smoke checks.

  --admin-username USERNAME
      Superadmin username to create or update. If omitted in an interactive
      terminal, the installer prompts and defaults to "admin".

  --admin-password PASSWORD
      Superadmin password to create or update. Prefer the environment variable
      PLENORA_BOOTSTRAP_ADMIN_PASSWORD to avoid shell history exposure.

  --admin-group-id GROUP_ID
      Group id for the bootstrap superadmin. Default: root.

  --skip-admin-bootstrap
      Do not prompt for or create a superadmin user.

  --worker-replicas N
      Number of worker containers to run. Default: 1.

Environment overrides:
  PLENORA_BOOTSTRAP_ADMIN_USERNAME / PLENORA_BOOTSTRAP_ADMIN_PASSWORD
      Non-interactive superadmin bootstrap credentials.

  PLENORA_BOOTSTRAP_ADMIN_GROUP_ID
      Group id used by the bootstrap superadmin. Default: root.

  PLENORA_WORKER_REPLICAS
      Number of worker containers to run. Default: 1.

  PLENORA_DOCKER_NETWORK
      External Docker bridge network used by the compose stack.
      Default: plenora

  PLENORA_DOCKER_SUBNET
      Subnet used when the installer creates PLENORA_DOCKER_NETWORK.
      Default: 172.28.0.0/16

  COMPOSE_FILE
      Compose file used by the installer. Default: docker-compose.yml.
      The default intentionally ignores local docker-compose.override.yml files.

Source of truth:
  docs/ops/install-docker-single-server.md
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
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'ERROR: command failed with exit code %s: %s\n' "$rc" "$*" >&2
        exit "$rc"
    fi
}

run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        run "$@"
    else
        command -v sudo >/dev/null 2>&1 || die "sudo not found; install sudo or run as root."
        run sudo "$@"
    fi
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
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'ERROR: docker command failed with exit code %s: %s %s\n' "$rc" "${DOCKER_CMD[*]}" "$*" >&2
        exit "$rc"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --install-deps)
            INSTALL_DEPS=1
            shift
            ;;
        --frontend-api-url)
            [ $# -ge 2 ] || die "--frontend-api-url requires a value."
            FRONTEND_VITE_API_URL="$2"
            shift 2
            ;;
        --require-sqlserver-odbc)
            REQUIRE_SQLSERVER_ODBC=1
            shift
            ;;
        --skip-smoke)
            SKIP_SMOKE=1
            shift
            ;;
        --admin-username)
            [ $# -ge 2 ] || die "--admin-username requires a value."
            BOOTSTRAP_ADMIN_USERNAME="$2"
            shift 2
            ;;
        --admin-password)
            [ $# -ge 2 ] || die "--admin-password requires a value."
            BOOTSTRAP_ADMIN_PASSWORD="$2"
            shift 2
            ;;
        --admin-group-id)
            [ $# -ge 2 ] || die "--admin-group-id requires a value."
            BOOTSTRAP_ADMIN_GROUP_ID="$2"
            shift 2
            ;;
        --skip-admin-bootstrap)
            SKIP_ADMIN_BOOTSTRAP=1
            shift
            ;;
        --worker-replicas)
            [ $# -ge 2 ] || die "--worker-replicas requires a value."
            PLENORA_WORKER_REPLICAS="$2"
            shift 2
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
    [ -f "docker-compose.yml" ] || die "docker-compose.yml not found. Run from the repo root."
    [ -f ".env.docker.example" ] || die ".env.docker.example not found. Run from the repo root."
    [ -f "backend/Dockerfile" ] || die "backend/Dockerfile not found. Run from the repo root."
}

install_linux_deps() {
    log_step "Install Linux host dependencies"

    [ -f /etc/os-release ] || die "/etc/os-release not found; automatic dependency install supports Debian/Ubuntu only."
    # shellcheck disable=SC1091
    . /etc/os-release

    local distro_id="${ID:-}"
    local id_like="${ID_LIKE:-}"
    local docker_repo_id=""
    if [ "$distro_id" = "ubuntu" ] || printf '%s' "$id_like" | grep -qw ubuntu; then
        docker_repo_id="ubuntu"
    elif [ "$distro_id" = "debian" ] || printf '%s' "$id_like" | grep -qw debian; then
        docker_repo_id="debian"
    else
        die "automatic Docker apt setup supports Debian/Ubuntu only. Install Docker manually, then rerun without --install-deps."
    fi

    local codename="${VERSION_CODENAME:-}"
    [ -n "$codename" ] || die "VERSION_CODENAME not found in /etc/os-release."

    run_sudo apt-get update
    run_sudo apt-get install -y ca-certificates curl gnupg git python3
    run_sudo install -m 0755 -d /etc/apt/keyrings
    run_sudo curl -fsSL "https://download.docker.com/linux/${docker_repo_id}/gpg" -o /etc/apt/keyrings/docker.asc
    run_sudo chmod a+r /etc/apt/keyrings/docker.asc

    local arch
    arch="$(dpkg --print-architecture)"
    local source_line="deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${docker_repo_id} ${codename} stable"
    log "+ write /etc/apt/sources.list.d/docker.list"
    if [ "$DRY_RUN" -eq 0 ]; then
        if [ "$(id -u)" -eq 0 ]; then
            printf '%s\n' "$source_line" > /etc/apt/sources.list.d/docker.list
        else
            printf '%s\n' "$source_line" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
        fi
    fi

    run_sudo apt-get update
    run_sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    if [ "$(id -u)" -ne 0 ]; then
        run_sudo usermod -aG docker "$USER"
        log "Docker group updated for ${USER}. The current run can still use sudo docker; open a new shell later for passwordless docker."
    fi
}

select_docker_command() {
    if [ "$DRY_RUN" -eq 1 ]; then
        DOCKER_CMD=(docker)
        return
    fi
    if command -v docker >/dev/null 2>&1; then
        if docker version >/dev/null 2>&1; then
            DOCKER_CMD=(docker)
            return
        fi
        if command -v sudo >/dev/null 2>&1; then
            DOCKER_CMD=(sudo docker)
            return
        fi
        die "docker exists but the daemon is not reachable. Start Docker or add your user to the docker group."
    fi
    die "docker not found. Install Docker first or rerun with --install-deps on Debian/Ubuntu."
}

preflight() {
    log_step "Preflight"
    run_docker --version
    run_docker compose version
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    command -v python3 >/dev/null 2>&1 || die "python3 not found; needed to generate .env.docker secrets."
    command -v curl >/dev/null 2>&1 || die "curl not found; needed for health smoke checks."
}

ensure_docker_network() {
    log_step "Docker network"

    log "+ ${DOCKER_CMD[*]} network inspect ${PLENORA_DOCKER_NETWORK}"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ ${DOCKER_CMD[*]} network create --driver bridge --subnet ${PLENORA_DOCKER_SUBNET} ${PLENORA_DOCKER_NETWORK}  # if missing"
        return 0
    fi

    if "${DOCKER_CMD[@]}" network inspect "$PLENORA_DOCKER_NETWORK" >/dev/null 2>&1; then
        log "Docker network ${PLENORA_DOCKER_NETWORK} already exists."
        return 0
    fi

    run_docker network create --driver bridge --subnet "$PLENORA_DOCKER_SUBNET" "$PLENORA_DOCKER_NETWORK"
}

env_value() {
    local key="$1"
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
    ' ".env.docker" 2>/dev/null
}

set_env_value() {
    local key="$1"
    local value="$2"
    local tmp

    log "+ set ${key} in .env.docker"
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
    ' ".env.docker" > "$tmp"
    mv "$tmp" ".env.docker"
}

new_fernet_key() {
    python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
}

new_jwt_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe(48))"
}

ensure_env_docker() {
    log_step "Create or refresh .env.docker"

    if [ ! -f ".env.docker" ]; then
        log "+ cp .env.docker.example .env.docker"
        if [ "$DRY_RUN" -eq 0 ]; then
            cp ".env.docker.example" ".env.docker"
        fi
    else
        log ".env.docker already exists; preserving existing non-empty values."
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ generate PLENORA_ENCRYPTION_KEY if empty"
        log "+ generate JWT_SECRET_KEY if empty"
        return 0
    fi

    if [ -z "$(env_value PLENORA_ENCRYPTION_KEY)" ]; then
        set_env_value PLENORA_ENCRYPTION_KEY "$(new_fernet_key)"
    fi
    if [ -z "$(env_value JWT_SECRET_KEY)" ]; then
        set_env_value JWT_SECRET_KEY "$(new_jwt_secret)"
    fi
    if [ "$(env_value STORAGE_POLICY)" = "db" ] && [ -z "$(env_value OBJECT_STORAGE_ENABLED)" ]; then
        set_env_value OBJECT_STORAGE_ENABLED "false"
    fi
    chmod 600 ".env.docker" || true

    [ -n "$(env_value PLENORA_ENCRYPTION_KEY)" ] || die "PLENORA_ENCRYPTION_KEY is still empty in .env.docker."
    [ -n "$(env_value JWT_SECRET_KEY)" ] || die "JWT_SECRET_KEY is still empty in .env.docker."
}

validate_positive_int() {
    local name="$1"
    local value="$2"
    case "$value" in
        ''|*[!0-9]*)
            die "${name} must be a positive integer."
            ;;
    esac
    if [ "$value" -le 0 ]; then
        die "${name} must be greater than zero."
    fi
}

collect_runtime_choices() {
    log_step "Runtime choices"

    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ choose worker replicas (default: 1)"
        [ -n "$PLENORA_WORKER_REPLICAS" ] || PLENORA_WORKER_REPLICAS=1
        validate_positive_int "PLENORA_WORKER_REPLICAS" "$PLENORA_WORKER_REPLICAS"
        log "worker replicas: ${PLENORA_WORKER_REPLICAS}"
        return 0
    fi

    if [ -z "$PLENORA_WORKER_REPLICAS" ] && [ -t 0 ]; then
        printf "worker replicas [1]: "
        read -r PLENORA_WORKER_REPLICAS
    fi
    PLENORA_WORKER_REPLICAS="${PLENORA_WORKER_REPLICAS:-1}"
    validate_positive_int "PLENORA_WORKER_REPLICAS" "$PLENORA_WORKER_REPLICAS"
    log "worker replicas: ${PLENORA_WORKER_REPLICAS}"
}

collect_admin_bootstrap_credentials() {
    log_step "Bootstrap superadmin"

    if [ "$SKIP_ADMIN_BOOTSTRAP" -eq 1 ]; then
        log "Superadmin bootstrap skipped."
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log "+ prompt for superadmin username/password if not provided"
        log "+ seed or update superadmin after backend is healthy"
        return 0
    fi

    if [ -z "$BOOTSTRAP_ADMIN_USERNAME" ] && [ -t 0 ]; then
        printf "Superadmin username [admin]: "
        read -r BOOTSTRAP_ADMIN_USERNAME
        BOOTSTRAP_ADMIN_USERNAME="${BOOTSTRAP_ADMIN_USERNAME:-admin}"
    fi

    if [ -z "$BOOTSTRAP_ADMIN_PASSWORD" ] && [ -t 0 ]; then
        local password_confirm=""
        while true; do
            printf "Superadmin password (min 12 chars): "
            read -r -s BOOTSTRAP_ADMIN_PASSWORD
            printf '\n'
            printf "Confirm superadmin password: "
            read -r -s password_confirm
            printf '\n'
            if [ "$BOOTSTRAP_ADMIN_PASSWORD" != "$password_confirm" ]; then
                log "Passwords do not match; try again."
                BOOTSTRAP_ADMIN_PASSWORD=""
                continue
            fi
            if [ "${#BOOTSTRAP_ADMIN_PASSWORD}" -lt 12 ]; then
                log "Password must be at least 12 characters; try again."
                BOOTSTRAP_ADMIN_PASSWORD=""
                continue
            fi
            break
        done
    fi

    if [ -z "$BOOTSTRAP_ADMIN_USERNAME" ] || [ -z "$BOOTSTRAP_ADMIN_PASSWORD" ]; then
        SKIP_ADMIN_BOOTSTRAP=1
        log "Superadmin bootstrap skipped: credentials were not provided and stdin is not interactive."
        log "Set PLENORA_BOOTSTRAP_ADMIN_USERNAME and PLENORA_BOOTSTRAP_ADMIN_PASSWORD for non-interactive installs."
        return 0
    fi

    if [ "${#BOOTSTRAP_ADMIN_PASSWORD}" -lt 12 ]; then
        die "superadmin password must be at least 12 characters."
    fi
}

seed_bootstrap_superadmin() {
    if [ "$SKIP_ADMIN_BOOTSTRAP" -eq 1 ]; then
        return 0
    fi

    log_step "Seed superadmin"
    log "+ ${DOCKER_CMD[*]} compose exec -T -e PLENORA_BOOTSTRAP_ADMIN_USERNAME -e PLENORA_BOOTSTRAP_ADMIN_PASSWORD -e PLENORA_BOOTSTRAP_ADMIN_GROUP_ID backend python -c <bootstrap-script>"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi

    export PLENORA_BOOTSTRAP_ADMIN_USERNAME="$BOOTSTRAP_ADMIN_USERNAME"
    export PLENORA_BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD"
    export PLENORA_BOOTSTRAP_ADMIN_GROUP_ID="$BOOTSTRAP_ADMIN_GROUP_ID"

    local script
    script='import os, uuid
from app.core.auth import db
from app.core.security.password_hashing import hash_password

username = os.environ["PLENORA_BOOTSTRAP_ADMIN_USERNAME"]
password = os.environ["PLENORA_BOOTSTRAP_ADMIN_PASSWORD"]
group_id = os.environ.get("PLENORA_BOOTSTRAP_ADMIN_GROUP_ID") or "root"

if not username.strip():
    raise SystemExit("bootstrap superadmin username is empty")
if len(password) < 12:
    raise SystemExit("bootstrap superadmin password must be at least 12 characters")
if not group_id.strip():
    raise SystemExit("bootstrap superadmin group id is empty")

if db.get_group(group_id) is None:
    db.create_group(group_id, "Root", "Bootstrap superadmin group")

existing = db.get_user_by_username(username)
if existing is None:
    db.create_user(
        user_id=f"bootstrap-{uuid.uuid4()}",
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role="superadmin",
        email="admin@example.local",
    )
else:
    db.update_user(existing.id, is_active=True, role="superadmin", group_id=group_id)
    db.update_user_password(existing.id, hash_password(password))

print(f"Superadmin ready: username={username} role=superadmin group_id={group_id}")'

    "${DOCKER_CMD[@]}" compose exec -T \
        -e PLENORA_BOOTSTRAP_ADMIN_USERNAME \
        -e PLENORA_BOOTSTRAP_ADMIN_PASSWORD \
        -e PLENORA_BOOTSTRAP_ADMIN_GROUP_ID \
        backend python -c "$script"
    local rc=$?

    unset PLENORA_BOOTSTRAP_ADMIN_PASSWORD
    BOOTSTRAP_ADMIN_PASSWORD=""

    if [ "$rc" -ne 0 ]; then
        printf 'ERROR: docker command failed with exit code %s: bootstrap superadmin\n' "$rc" >&2
        exit "$rc"
    fi
}

provider_check() {
    log_step "Database provider check"

    local required="False"
    if [ "$REQUIRE_SQLSERVER_ODBC" -eq 1 ]; then
        required="True"
    fi

    local script
    script="import psycopg, pymysql, oracledb, pyodbc; drivers=pyodbc.drivers(); print('python db providers ok'); print('odbc_drivers=', drivers); required=${required}; ok=any(d in drivers for d in ('ODBC Driver 18 for SQL Server','ODBC Driver 17 for SQL Server')); raise SystemExit(3 if required and not ok else 0)"
    run_docker compose exec -T backend python -c "$script"
}

smoke_checks() {
    log_step "Smoke checks"
    run curl -fsS http://127.0.0.1:5004/health
    log ""
    run curl -fsS http://127.0.0.1:5004/ready
    log ""
    run curl -fsS http://127.0.0.1:3000/
    log ""
    run_docker compose exec -T worker python -c "
import time, sys
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store
handle = dispatch_task_by_name('cleanroom.ping')
store = default_task_result_store()
deadline = time.monotonic() + 30
state = 'PENDING'
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ('SUCCESS', 'FAILURE'):
        state = record.state
        break
    time.sleep(0.5)
print(state)
sys.exit(0 if state == 'SUCCESS' else 1)
"
}

assert_repo_root

if [ "$INSTALL_DEPS" -eq 1 ]; then
    install_linux_deps
fi

select_docker_command
preflight

if [ "$CHECK_ONLY" -eq 1 ]; then
    log_step "Check complete"
    log "No files were written and no containers were started."
    exit 0
fi

ensure_env_docker
ensure_docker_network
collect_runtime_choices
collect_admin_bootstrap_credentials

if [ -n "$FRONTEND_VITE_API_URL" ]; then
    export FRONTEND_VITE_API_URL
    log "Using FRONTEND_VITE_API_URL=${FRONTEND_VITE_API_URL}"
fi

log_step "Build and start single-server stack"
run_docker compose config --quiet
run_docker compose build backend frontend
run_docker compose up -d postgres rabbitmq
run_docker compose up -d --wait backend
seed_bootstrap_superadmin
run_docker compose up -d --scale worker="$PLENORA_WORKER_REPLICAS" worker worker-control beat
run_docker compose up -d --wait frontend

provider_check

if [ "$SKIP_SMOKE" -eq 0 ]; then
    smoke_checks
else
    log_step "Smoke checks skipped"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    log_step "Dry run complete"
    log "No files were written and no containers were started."
else
    log_step "Install complete"
    log "Stack is running. Use:"
    log "  ${DOCKER_CMD[*]} compose ps"
    log "  ${DOCKER_CMD[*]} compose logs --tail 200 backend worker worker-control beat frontend"
    log "  ${DOCKER_CMD[*]} compose stop"
fi
