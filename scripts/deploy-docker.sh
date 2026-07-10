#!/usr/bin/env bash
# Plenora-ETL - Docker deploy/update script for an already installed host.
#
# Scope:
#   - pulls the selected Git branch with --ff-only;
#   - rebuilds backend/frontend images;
#   - recreates backend, worker, beat, and frontend containers;
#   - keeps postgres/rabbitmq/data volumes intact;
#   - runs lightweight smoke checks.
#
# First install remains:
#   bash scripts/install-docker.sh
#
# Normal update after a push/pull-ready deploy:
#   bash scripts/deploy-docker.sh

set -uo pipefail

DRY_RUN=0
NO_PULL=0
NO_BUILD=0
SKIP_SMOKE=0
FRONTEND_VITE_API_URL="${FRONTEND_VITE_API_URL:-}"
PLENORA_WORKER_REPLICAS="${PLENORA_WORKER_REPLICAS:-}"
PLENORA_DOCKER_NETWORK="${PLENORA_DOCKER_NETWORK:-plenora}"
PLENORA_DOCKER_SUBNET="${PLENORA_DOCKER_SUBNET:-172.28.0.0/16}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BRANCH=""
DOCKER_CMD=()

export PLENORA_DOCKER_NETWORK
export PLENORA_DOCKER_SUBNET
export COMPOSE_FILE

usage() {
    cat <<'USAGE'
Plenora-ETL Docker deploy/update script.

Usage:
  bash scripts/deploy-docker.sh
  bash scripts/deploy-docker.sh --branch main
  bash scripts/deploy-docker.sh --no-pull
  bash scripts/deploy-docker.sh --dry-run
  bash scripts/deploy-docker.sh --worker-replicas 2

Options:
  --branch BRANCH
      Git branch to pull from origin. Default: current branch.

  --no-pull
      Do not run git fetch/pull. Rebuild/recreate from the current checkout.

  --no-build
      Do not rebuild images. Recreate containers with existing local images.

  --frontend-api-url URL
      Sets FRONTEND_VITE_API_URL for the frontend build.

  --worker-replicas N
      Number of worker containers to run. Default: current running
      worker replica count if detectable, otherwise 1.

  --skip-smoke
      Skip HTTP and worker smoke checks.

  --dry-run
      Print the actions that would run without executing them.

Environment overrides:
  PLENORA_WORKER_REPLICAS
  PLENORA_DOCKER_NETWORK
  PLENORA_DOCKER_SUBNET
  FRONTEND_VITE_API_URL
  COMPOSE_FILE
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
        --branch)
            [ $# -ge 2 ] || die "--branch requires a value."
            BRANCH="$2"
            shift 2
            ;;
        --no-pull)
            NO_PULL=1
            shift
            ;;
        --no-build)
            NO_BUILD=1
            shift
            ;;
        --frontend-api-url)
            [ $# -ge 2 ] || die "--frontend-api-url requires a value."
            FRONTEND_VITE_API_URL="$2"
            shift 2
            ;;
        --worker-replicas)
            [ $# -ge 2 ] || die "--worker-replicas requires a value."
            PLENORA_WORKER_REPLICAS="$2"
            shift 2
            ;;
        --skip-smoke)
            SKIP_SMOKE=1
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
    [ -f "docker-compose.yml" ] || die "docker-compose.yml not found. Run from the repo root."
    [ -f ".env.docker.example" ] || die ".env.docker.example not found. Run from the repo root."
    [ -f "backend/Dockerfile" ] || die "backend/Dockerfile not found. Run from the repo root."
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
    die "docker not found. Install Docker first or rerun scripts/install-docker.sh --install-deps on Debian/Ubuntu."
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

detect_worker_replicas() {
    if [ -n "$PLENORA_WORKER_REPLICAS" ]; then
        validate_positive_int "PLENORA_WORKER_REPLICAS" "$PLENORA_WORKER_REPLICAS"
        return
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        PLENORA_WORKER_REPLICAS=1
        return
    fi

    local count
    count="$("${DOCKER_CMD[@]}" compose ps -q worker 2>/dev/null | wc -l | tr -d ' ')"
    if [ -z "$count" ] || [ "$count" -le 0 ]; then
        count=1
    fi
    PLENORA_WORKER_REPLICAS="$count"
}

ensure_env_and_network() {
    log_step "Preflight"
    run_docker --version
    run_docker compose version

    if [ ! -f ".env.docker" ]; then
        die ".env.docker not found. Run first install with: bash scripts/install-docker.sh"
    fi

    log "+ ${DOCKER_CMD[*]} network inspect ${PLENORA_DOCKER_NETWORK}"
    if [ "$DRY_RUN" -eq 0 ] && ! "${DOCKER_CMD[@]}" network inspect "$PLENORA_DOCKER_NETWORK" >/dev/null 2>&1; then
        run_docker network create --driver bridge --subnet "$PLENORA_DOCKER_SUBNET" "$PLENORA_DOCKER_NETWORK"
    elif [ "$DRY_RUN" -eq 1 ]; then
        log "+ ${DOCKER_CMD[*]} network create --driver bridge --subnet ${PLENORA_DOCKER_SUBNET} ${PLENORA_DOCKER_NETWORK}  # if missing"
    fi
}

git_update() {
    if [ "$NO_PULL" -eq 1 ]; then
        log_step "Git pull skipped"
        return 0
    fi

    log_step "Git update"

    if [ -n "$(git status --porcelain)" ]; then
        die "working tree is dirty. Commit/stash local changes or rerun with --no-pull if you know what you are doing."
    fi

    if [ -z "$BRANCH" ]; then
        BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    fi
    [ -n "$BRANCH" ] && [ "$BRANCH" != "HEAD" ] || die "cannot determine current Git branch. Use --branch BRANCH."

    run git fetch origin "$BRANCH"

    local current_branch
    current_branch="$(git rev-parse --abbrev-ref HEAD)"
    if [ "$current_branch" != "$BRANCH" ]; then
        run git checkout "$BRANCH"
    fi

    run git pull --ff-only origin "$BRANCH"
}

compose_update() {
    log_step "Compose config"
    run_docker compose config --quiet

    if [ -n "$FRONTEND_VITE_API_URL" ]; then
        export FRONTEND_VITE_API_URL
        log "Using FRONTEND_VITE_API_URL=${FRONTEND_VITE_API_URL}"
    fi

    if [ "$NO_BUILD" -eq 0 ]; then
        log_step "Build images"
        run_docker compose build backend frontend
    else
        log_step "Build skipped"
    fi

    log_step "Restart stack"
    run_docker compose up -d postgres rabbitmq
    run_docker compose up -d --wait --force-recreate backend
    run_docker compose up -d --force-recreate --scale worker="$PLENORA_WORKER_REPLICAS" worker worker-control beat
    run_docker compose up -d --wait --force-recreate frontend
}

smoke_checks() {
    if [ "$SKIP_SMOKE" -eq 1 ]; then
        log_step "Smoke checks skipped"
        return 0
    fi

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
select_docker_command
detect_worker_replicas

log "=========================================================="
log " Deploy Plenora Docker"
log "   branch          : ${BRANCH:-current}"
log "   pull            : $([ "$NO_PULL" -eq 1 ] && printf 'no' || printf 'yes')"
log "   build           : $([ "$NO_BUILD" -eq 1 ] && printf 'no' || printf 'yes')"
log "   worker replicas : ${PLENORA_WORKER_REPLICAS}"
log "   compose file    : ${COMPOSE_FILE}"
log "   dry-run         : ${DRY_RUN}"
log "=========================================================="

ensure_env_and_network
git_update
compose_update
smoke_checks

if [ "$DRY_RUN" -eq 1 ]; then
    log_step "Dry run complete"
    log "No files were written and no containers were changed."
else
    log_step "Deploy complete"
    log "Stack updated. Use:"
    log "  ${DOCKER_CMD[*]} compose ps"
    log "  ${DOCKER_CMD[*]} compose logs --tail 200 backend worker worker-control beat frontend"
fi
