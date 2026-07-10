#!/usr/bin/env bash
# Plenora-ETL — Docker validation pack runner
#
# BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK.
# BE-GO-READY-INSTALL-013-DOCKER-VALIDATE-LINUX-SAFETY (preflight +
# LF lock hardening).
#
# Runs the eight executable validation commands documented in
# docs/ops/install-docker.md "Validation pack for the Linux PC test"
# in order, capturing each step's stdout/stderr inline so Marco can
# paste the full transcript back.
#
# LF line endings (BE-GO-READY-INSTALL-013):
#   This file MUST be checked out with LF line endings on every
#   platform. A CRLF copy fails on Linux with
#     `bash: bad interpreter: /usr/bin/env bash^M`
#   The repo's `.gitattributes` pins `scripts/*.sh text eol=lf` and
#   the source-bound test in
#   `backend/tests2/shared/test_docker_validate_script_guard.py`
#   asserts the on-disk file has zero `\r\n` sequences.
#
# Non-destructive contract (enforced by source-bound test
# tests2/shared/test_docker_validate_script_guard.py):
#   - never runs `docker compose down` (removes containers);
#   - never runs `docker compose down -v` (DESTRUCTIVE: removes volumes);
#   - never deletes / overwrites .env.docker;
#   - never runs `docker volume rm` against plenora-* volumes;
#   - never runs `docker system prune` variants.
#
# Preflights (run BEFORE any docker invocation; exit 2 with a
# diagnostic pointer if any precondition fails):
#   - repo root (looks for `docker-compose.yml`);
#   - docker binary on PATH;
#   - docker compose plugin available (`docker compose version`);
#   - `.env.docker` populated (Step 2 of install-docker.md).
#
# Usage:
#   bash scripts/docker-validate.sh                # run the pack
#   bash scripts/docker-validate.sh --dry-run      # print commands only
#   bash scripts/docker-validate.sh --help
#
# Exit codes:
#   0  : all eight steps reached completion (NOT a validation pass —
#        Marco must read the per-step output to confirm shapes).
#   2  : invocation error (bad flag, missing docker binary / compose
#        plugin / .env.docker, not in repo root).
#   non-zero (other): the failing `docker compose` / `curl` / `python`
#        sub-command's exit code, surfaced as-is.

set -uo pipefail

DRY_RUN=0
PLENORA_DOCKER_NETWORK="${PLENORA_DOCKER_NETWORK:-plenora}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
export PLENORA_DOCKER_NETWORK
export COMPOSE_FILE

usage() {
    cat <<'USAGE'
Plenora-ETL Docker validation pack runner.

Usage:
  bash scripts/docker-validate.sh                # run the pack
  bash scripts/docker-validate.sh --dry-run      # print commands only
  bash scripts/docker-validate.sh --help

Status: FIRST SCAFFOLD, NOT YET VALIDATED. See
docs/ops/install-docker.md "Validation pack for the Linux PC test"
for the expected output shapes.

Non-destructive: this script never removes containers, volumes,
or .env.docker. The teardown step is `docker compose stop`, which
preserves all named volumes (postgres-data, rabbitmq-data, data-dir).

Pre-flight: run from the repo root with .env.docker already
populated (see Step 2 of install-docker.md). The script exits 2
if those preconditions are not met.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Pre-flight 1: repo root. Every subsequent docker invocation
# resolves docker-compose.yml relative to PWD, so a wrong cwd would
# either fail late with a confusing diagnostic or (worse) operate
# on an unrelated compose project on the same host.
if [ ! -f "docker-compose.yml" ]; then
    echo "ERROR: docker-compose.yml not found in current directory." >&2
    echo "       Run from the repo root:" >&2
    echo "         cd /path/to/plenora && bash scripts/docker-validate.sh" >&2
    exit 2
fi

# Pre-flight 2: docker binary on PATH. `command -v` is the POSIX
# canonical "is this on PATH" test; it is faster and saner than
# parsing `which` output. Skipped in dry-run because dry-run only
# prints the canonical commands without executing anything.
if [ "$DRY_RUN" -eq 0 ] && ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: 'docker' binary not found on PATH." >&2
    echo "       Install Docker Engine first:" >&2
    echo "         https://docs.docker.com/engine/install/" >&2
    echo "       After install, verify:" >&2
    echo "         docker --version" >&2
    exit 2
fi

# Pre-flight 3: docker compose plugin (the `docker compose ...`
# subcommand, not the legacy `docker-compose` standalone). Without
# the plugin every step beyond #1 would fail; we surface the gap
# upfront with the canonical install pointer. Skipped in dry-run.
if [ "$DRY_RUN" -eq 0 ] && ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: 'docker compose' plugin not available." >&2
    echo "       The validation pack uses the v2 'docker compose'" >&2
    echo "       subcommand (NOT the legacy 'docker-compose' tool)." >&2
    echo "       Install the compose plugin:" >&2
    echo "         https://docs.docker.com/compose/install/linux/" >&2
    echo "       After install, verify:" >&2
    echo "         docker compose version" >&2
    exit 2
fi

# Pre-flight 4: .env.docker exists and has required secrets. The
# validation pack consumes the secrets file Step 2 of install-docker.md
# creates; this script NEVER produces or overwrites it (a regenerate
# path would silently rotate the encryption / JWT keys and break
# running deployments).
if [ "$DRY_RUN" -eq 0 ] && [ ! -f ".env.docker" ]; then
    echo "ERROR: .env.docker not found." >&2
    echo "       Run Step 2 of docs/ops/install-docker.md first:" >&2
    echo "         cp .env.docker.example .env.docker" >&2
    echo "         # then add PLENORA_ENCRYPTION_KEY and JWT_SECRET_KEY" >&2
    echo "         chmod 600 .env.docker" >&2
    exit 2
fi

# Pre-flight 5: the compose stack uses one explicit external bridge
# network so every service joins the same predictable Docker network.
# The installer creates it; this validation script only verifies it
# exists because the validation contract is non-destructive.
if [ "$DRY_RUN" -eq 0 ] && ! docker network inspect "$PLENORA_DOCKER_NETWORK" >/dev/null 2>&1; then
    echo "ERROR: Docker network '$PLENORA_DOCKER_NETWORK' not found." >&2
    echo "       Create it via the installer:" >&2
    echo "         bash scripts/install-docker.sh --skip-smoke" >&2
    echo "       Or manually:" >&2
    echo "         docker network create --driver bridge --subnet 172.28.0.0/16 $PLENORA_DOCKER_NETWORK" >&2
    exit 2
fi

env_docker_value() {
    local key="$1"
    awk -F= -v key="$key" '
        /^[[:space:]]*#/ { next }
        {
            k = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
            if (k == key) {
                sub(/^[^=]*=/, "")
                gsub(/^[[:space:]]+|[[:space:]]+$/, "")
                quote = sprintf("%c", 39)
                if ((substr($0, 1, 1) == "\"" && substr($0, length($0), 1) == "\"") ||
                    (substr($0, 1, 1) == quote && substr($0, length($0), 1) == quote)) {
                    $0 = substr($0, 2, length($0) - 2)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "")
                }
                print
                exit
            }
        }
    ' ".env.docker"
}

if [ "$DRY_RUN" -eq 0 ]; then
    missing_env_keys=""
    for required_key in PLENORA_ENCRYPTION_KEY JWT_SECRET_KEY; do
        if [ -z "$(env_docker_value "$required_key")" ]; then
            missing_env_keys="${missing_env_keys} ${required_key}"
        fi
    done
    if [ -n "$missing_env_keys" ]; then
        echo "ERROR: .env.docker is missing required non-empty secret key(s):${missing_env_keys}" >&2
        echo "       Fill them using docs/ops/install-docker.md Step 2, then rerun:" >&2
        echo "         bash scripts/docker-validate.sh" >&2
        echo "       Secret values were not printed." >&2
        exit 2
    fi
fi

run_step() {
    # Args: <step-number> <description> -- <command...>
    local n="$1"
    shift
    local desc="$1"
    shift
    if [ "$1" != "--" ]; then
        echo "INTERNAL ERROR: run_step expects '--' separator" >&2
        exit 2
    fi
    shift

    echo
    echo "=============================================================="
    echo "STEP $n: $desc"
    echo "    \$ $*"
    echo "=============================================================="

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "(dry-run: not executing)"
        return 0
    fi

    "$@"
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo
        echo "STEP $n FAILED with exit code $rc." >&2
        echo "Paste this transcript back along with:" >&2
        echo "  - docker compose ps" >&2
        echo "  - docker compose logs --tail 200 backend worker worker-control beat" >&2
        echo "See docs/ops/install-docker.md \"Validation pack\" for" >&2
        echo "the per-step Failure paste guidance." >&2
        exit $rc
    fi
}

run_ready_step() {
    local n="4b"
    local desc="GET /ready"
    local url="http://127.0.0.1:5004/ready"
    local body_file
    local rc
    local http_status

    echo
    echo "=============================================================="
    echo "STEP $n: $desc"
    echo "    \$ curl -fsS $url"
    echo "=============================================================="

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "(dry-run: not executing)"
        return 0
    fi

    body_file="$(mktemp)"
    http_status="$(curl -sS -o "$body_file" -w "%{http_code}" "$url")"
    rc=$?

    if [ $rc -eq 0 ] && [ "$http_status" -ge 200 ] && [ "$http_status" -lt 300 ]; then
        cat "$body_file"
        rm -f "$body_file"
        return 0
    fi

    echo
    echo "STEP $n FAILED: /ready returned HTTP ${http_status:-000} (curl exit $rc)." >&2
    if [ -s "$body_file" ]; then
        echo "Response body from /ready:" >&2
        cat "$body_file" >&2
        echo >&2
    else
        echo "No /ready response body was received." >&2
    fi
    rm -f "$body_file"
    echo "Readiness is the dependency gate. Inspect the failed check details above, then paste:" >&2
    echo "  - docker compose ps" >&2
    echo "  - docker compose logs --tail 200 backend" >&2
    echo "  - docker compose logs --tail 200 postgres rabbitmq" >&2
    echo "  - docker compose exec -T backend python -c \"from app.observability.health import gather_readiness; print(gather_readiness().body)\"" >&2
    echo "Common blockers: Postgres/RabbitMQ connectivity, pending or failed migrations, object storage policy/config, bootstrap not complete." >&2
    exit 1
}

run_schema_postcondition() {
    local n="4c"
    local desc="Postgres schema + migrations postcondition"
    local schema_sql="SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'plenora');"
    local table_sql="SELECT to_regclass('plenora.schema_migrations') IS NOT NULL;"
    local rows_sql="SELECT count(*) FROM plenora.schema_migrations;"
    local value
    local rc

    echo
    echo "=============================================================="
    echo "STEP $n: $desc"
    echo "    \$ docker compose exec -T postgres psql -U plenora -d plenora -tAc \"$schema_sql\""
    echo "    \$ docker compose exec -T postgres psql -U plenora -d plenora -tAc \"$table_sql\""
    echo "    \$ docker compose exec -T postgres psql -U plenora -d plenora -tAc \"$rows_sql\""
    echo "=============================================================="

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "(dry-run: not executing)"
        return 0
    fi

    value="$(docker compose exec -T postgres psql -U plenora -d plenora -tAc "$schema_sql" | tr -d '[:space:]')"
    rc=$?
    if [ $rc -ne 0 ] || [ "$value" != "t" ]; then
        echo
        echo "STEP $n FAILED: schema 'plenora' was not found after /ready." >&2
        echo "Inspect backend migration logs:" >&2
        echo "  docker compose logs --tail 200 backend" >&2
        exit 1
    fi

    value="$(docker compose exec -T postgres psql -U plenora -d plenora -tAc "$table_sql" | tr -d '[:space:]')"
    rc=$?
    if [ $rc -ne 0 ] || [ "$value" != "t" ]; then
        echo
        echo "STEP $n FAILED: table 'plenora.schema_migrations' was not found after /ready." >&2
        echo "Inspect backend migration logs:" >&2
        echo "  docker compose logs --tail 200 backend" >&2
        exit 1
    fi

    value="$(docker compose exec -T postgres psql -U plenora -d plenora -tAc "$rows_sql" | tr -d '[:space:]')"
    rc=$?
    case "$value" in
        ''|*[!0-9]*)
            value=0
            ;;
    esac
    if [ $rc -ne 0 ] || [ "$value" -le 0 ]; then
        echo
        echo "STEP $n FAILED: plenora.schema_migrations has no applied rows after /ready." >&2
        echo "Inspect backend migration logs:" >&2
        echo "  docker compose logs --tail 200 backend" >&2
        exit 1
    fi

    echo "schema=plenora schema_migrations_rows=$value"
}

# Docker path status report (BE-GO-READY-INSTALL-019-AUTOMATE-INSTALL-TRUTH-CHECKS)
# Checks the Docker artifacts that must exist before the Docker path can
# be called validated. Non-blocking: emits WARN lines, does not exit.
# Run before any docker step so gaps are visible upfront.
# Source of truth: tests2/shared/test_install_docker_fe_gap_guard.py.
report_docker_path_status() {
    echo ""
    echo "=============================================================="
    echo "DOCKER PATH STATUS (FIRST SCAFFOLD, NOT YET VALIDATED)"
    echo "=============================================================="
    local gaps=0

    if ls frontend/Dockerfile* >/dev/null 2>&1; then
        echo "  [OK]   frontend/Dockerfile* found."
    else
        echo "  [WARN] frontend/Dockerfile not found -- FE image NOT BUILT."
        gaps=$(( gaps + 1 ))
    fi

    local fe_service=0
    for needle in "  frontend:" "  web:" "  nginx:" "  caddy:" "  spa:"; do
        if grep -qF "$needle" docker-compose.yml 2>/dev/null; then
            fe_service=1
            break
        fi
    done
    if [ "$fe_service" -eq 1 ]; then
        echo "  [OK]   frontend service in docker-compose.yml."
    else
        echo "  [WARN] No frontend service in docker-compose.yml -- full-stack Docker NOT AVAILABLE."
        gaps=$(( gaps + 1 ))
    fi

    local ci_wired=0
    if [ -d ".github/workflows" ]; then
        if grep -rE "^[[:space:]]*-[[:space:]]*run:.*(docker[[:space:]]+compose|docker-compose)\\b" .github/workflows/ >/dev/null 2>&1; then
            ci_wired=1
        fi
    fi
    if [ "$ci_wired" -eq 1 ]; then
        echo "  [OK]   Docker compose CI lane found."
    else
        echo "  [WARN] No docker compose CI lane in .github/workflows/ -- Docker CI NOT WIRED."
        gaps=$(( gaps + 1 ))
    fi

    echo ""
    if [ "$gaps" -gt 0 ]; then
        echo "  $gaps Docker artifact gap(s) open. Do not claim validated Docker yet."
        echo "  See docs/ops/install-docker.md 'What ships today vs what does NOT'."
    else
        echo "  Frontend artifacts present; CI lane and real-host validation still gate the claim."
    fi
    echo "=============================================================="
    echo ""
}

report_docker_path_status

run_step 1 "config validation (no apply, no env dump)" -- \
    docker compose config --quiet

run_step 2 "build backend + frontend images" -- \
    docker compose build backend frontend

run_step 3a "up postgres + rabbitmq" -- \
    docker compose up -d postgres rabbitmq

run_step 3b "up backend (gates on HEALTHCHECK)" -- \
    docker compose up -d --wait backend

run_step 4a "GET /health" -- \
    curl -fsS http://127.0.0.1:5004/health

# Inject a newline between the two curl outputs so the transcript
# is readable. The curl JSON body has no trailing newline.
[ "$DRY_RUN" -eq 0 ] && echo

# Source-bound guard marker: run_step 4b "GET /ready".
# Implemented through run_ready_step so readiness failures can print
# the response body and dependency-focused paste guidance.
run_ready_step

[ "$DRY_RUN" -eq 0 ] && echo

run_schema_postcondition

run_step 5 "up worker + beat" -- \
    docker compose up -d worker worker-control beat

run_step 6a "up frontend (static nginx)" -- \
    docker compose up -d --wait frontend

run_step 6b "GET frontend SPA shell" -- \
    curl -fsS http://127.0.0.1:3000/

run_step 7 "cleanroom.ping round-trip" -- \
    docker compose exec -T worker python -c "
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

run_step 8 "memory caps visibility" -- \
    docker compose exec -T backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"

run_step 9 "non-destructive stop (preserves volumes)" -- \
    docker compose stop

echo
echo "=============================================================="
echo "Validation pack run reached completion."
echo
echo "NOT a validation pass — Marco must verify each step's output"
echo "against the expected shapes in docs/ops/install-docker.md"
echo "\"Validation pack for the Linux PC test\" before claiming"
echo "the Docker scaffold validated on Linux."
echo
echo "Reporting artifacts:"
echo "  - this transcript;"
echo "  - host metadata: uname -a, docker --version,"
echo "    docker compose version, cat /etc/os-release | grep PRETTY_NAME;"
echo "  - any failure-step output listed in install-docker.md."
echo "=============================================================="
