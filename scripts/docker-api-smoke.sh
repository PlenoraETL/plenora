#!/usr/bin/env bash
# =============================================================================
# Plenora-ETL Docker API smoke runner (SKELETON)
# =============================================================================
#
# BE-CLEANROOM-072-W4-DOCKER-API-SMOKE-SKELETON.
#
# Targets an already running backend and performs only checks that do not need
# seeded credentials yet. This script is intentionally separate from
# scripts/docker-validate.sh: docker-validate boots infrastructure; this runner
# is the future home for user-facing API acceptance.
#
# Non-destructive contract:
#   - never starts/stops/removes Docker resources;
#   - never writes .env.docker;
#   - never deletes projects/files/tasks;
#   - never reports auth/project/upload/execute success until those flows are
#     implemented against a real seed helper and real API responses.
#
# Usage:
#   bash scripts/docker-api-smoke.sh
#   bash scripts/docker-api-smoke.sh --infrastructure-only
#   PLENORA_ACCEPTANCE_BASE_URL=http://127.0.0.1:5004 \
#       bash scripts/docker-api-smoke.sh --infrastructure-only
#
# Exit codes:
#   0 : implemented checks passed in --infrastructure-only mode.
#   2 : invocation error or failed implemented check.
#   3 : infrastructure checks passed, but full API smoke is intentionally
#       pending because auth seed/project/upload/execute are not implemented.
# =============================================================================

set -uo pipefail

INFRASTRUCTURE_ONLY=0
BASE_URL="${PLENORA_ACCEPTANCE_BASE_URL:-http://127.0.0.1:5004}"
BASE_URL="${BASE_URL%/}"

usage() {
    cat <<'USAGE'
Plenora-ETL Docker API smoke runner skeleton.

Usage:
  bash scripts/docker-api-smoke.sh
  bash scripts/docker-api-smoke.sh --infrastructure-only
  bash scripts/docker-api-smoke.sh --help

Environment:
  PLENORA_ACCEPTANCE_BASE_URL  Backend base URL. Defaults to
                              http://127.0.0.1:5004

Implemented today:
  GET /health
  GET /ready

Pending by design:
  auth seed/login, /auth/me, project create/list/delete, tiny CSV upload/list,
  project execute/poll/download, and scheduler smoke.

Use --infrastructure-only when you only want the implemented /health and
/ready checks to return success. Full mode exits 3 after those checks until a
real Docker-native auth seed helper and API flow are implemented.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --infrastructure-only)
            INFRASTRUCTURE_ONLY=1
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

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: 'curl' binary not found on PATH." >&2
    echo "       Install curl or run from an environment that provides it." >&2
    exit 2
fi

run_get_check() {
    local path="$1"
    local url="${BASE_URL}${path}"

    echo "GET ${url}"
    if ! curl -fsS "$url" >/dev/null; then
        echo "ERROR: ${path} check failed against ${BASE_URL}." >&2
        echo "       Start the Docker backend first, wait for readiness, then retry." >&2
        exit 2
    fi
}

echo "Docker API smoke target: ${BASE_URL}"
run_get_check "/health"
run_get_check "/ready"

if [ "$INFRASTRUCTURE_ONLY" -eq 1 ]; then
    echo "Infrastructure-only Docker API smoke checks passed."
    exit 0
fi

cat >&2 <<'PENDING'
TODO: Docker API smoke full flow is intentionally pending.

Implemented checks passed: /health and /ready.

Not implemented yet:
  - seed isolated admin/group inside the backend container;
  - POST /api/auth/login and GET /api/auth/me;
  - project create/list/delete;
  - tiny CSV upload/list/delete;
  - project execute/poll/download;
  - optional scheduler manual-run smoke.

This script does not fake acceptance success for those steps. Re-run with
--infrastructure-only to accept only the implemented infrastructure checks.
PENDING
exit 3
