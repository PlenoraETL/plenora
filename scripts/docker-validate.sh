#!/usr/bin/env bash
# =============================================================================
# Plenora-ETL — Docker validation pack runner (FIRST SCAFFOLD)
# =============================================================================
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
# Status: FIRST SCAFFOLD, NOT YET VALIDATED. Every command in this
# script is a hypothesis until run on a real Linux host.
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
# =============================================================================

set -uo pipefail

DRY_RUN=0

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
preserves all named volumes (postgres-data, redis-data, data-dir).

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

# Pre-flight 4: .env.docker exists. The validation pack consumes
# the secrets file Step 2 of install-docker.md creates; this script
# NEVER produces or overwrites it (a regenerate path would silently
# rotate the encryption / JWT keys and break running deployments).
if [ "$DRY_RUN" -eq 0 ] && [ ! -f ".env.docker" ]; then
    echo "ERROR: .env.docker not found." >&2
    echo "       Run Step 2 of docs/ops/install-docker.md first:" >&2
    echo "         cp .env.docker.example .env.docker" >&2
    echo "         # then add PLENORA_ENCRYPTION_KEY and JWT_SECRET_KEY" >&2
    echo "         chmod 600 .env.docker" >&2
    exit 2
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
        echo "  - docker compose logs --tail 200 backend worker beat" >&2
        echo "See docs/ops/install-docker.md \"Validation pack\" for" >&2
        echo "the per-step Failure paste guidance." >&2
        exit $rc
    fi
}

run_step 1 "config (no apply)" -- \
    docker compose config

run_step 2 "build backend image" -- \
    docker compose build backend

run_step 3a "up postgres + redis" -- \
    docker compose up -d postgres redis

run_step 3b "up backend (gates on HEALTHCHECK)" -- \
    docker compose up -d --wait backend

run_step 4a "GET /health" -- \
    curl -fsS http://127.0.0.1:5004/health

# Inject a newline between the two curl outputs so the transcript
# is readable. The curl JSON body has no trailing newline.
[ "$DRY_RUN" -eq 0 ] && echo

run_step 4b "GET /ready" -- \
    curl -fsS http://127.0.0.1:5004/ready

[ "$DRY_RUN" -eq 0 ] && echo

run_step 5 "up worker + beat" -- \
    docker compose up -d worker beat

run_step 6 "cleanroom.ping round-trip" -- \
    docker compose exec -T worker python -c \
        "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"

run_step 7 "memory caps visibility" -- \
    docker compose exec -T backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"

run_step 8 "non-destructive stop (preserves volumes)" -- \
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
