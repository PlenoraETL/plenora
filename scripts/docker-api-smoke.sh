#!/usr/bin/env bash
# =============================================================================
# Plenora-ETL Docker API smoke runner
# =============================================================================
#
# BE-CLEANROOM-072-W4-DOCKER-API-SMOKE-SKELETON.
#
# Targets an already running backend and performs bounded, non-destructive
# checks that do not need seeded credentials. This script is intentionally
# separate from scripts/docker-validate.sh: docker-validate boots
# infrastructure; this runner polls the user-facing API surface.
#
# Non-destructive contract:
#   - never starts/stops/removes Docker resources;
#   - never writes .env.docker;
#   - never deletes projects/files/tasks;
#   - never reports auth/project/upload/execute success until those flows are
#     implemented against a real seed helper and real API responses.
#
# Login/logout skip reason:
#   docker-compose.yml and .env.docker.example do not seed deterministic
#   user credentials. This smoke therefore does not attempt login/logout;
#   adding a user inside the runner would mutate application data and make
#   the check order-dependent.
#
# Usage:
#   bash scripts/docker-api-smoke.sh
#   bash scripts/docker-api-smoke.sh --infrastructure-only
#   PLENORA_ACCEPTANCE_BASE_URL=http://127.0.0.1:5004 \
#   PLENORA_ACCEPTANCE_TIMEOUT_SECONDS=120 \
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
TIMEOUT_SECONDS="${PLENORA_ACCEPTANCE_TIMEOUT_SECONDS:-90}"
INTERVAL_SECONDS="${PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS:-2}"
CURL_MAX_TIME_SECONDS="${PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS:-5}"

usage() {
    cat <<'USAGE'
Plenora-ETL Docker API smoke runner.

Usage:
  bash scripts/docker-api-smoke.sh
  bash scripts/docker-api-smoke.sh --infrastructure-only
  bash scripts/docker-api-smoke.sh --help

Environment:
  PLENORA_ACCEPTANCE_BASE_URL  Backend base URL. Defaults to
                              http://127.0.0.1:5004
  PLENORA_ACCEPTANCE_TIMEOUT_SECONDS
                              Per-endpoint poll timeout. Defaults to 90.
  PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS
                              Poll interval. Defaults to 2.
  PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS
                              Per-request curl timeout. Defaults to 5.

Implemented today:
  GET /health
  GET /ready

Pending by design:
  auth seed/login/logout, /auth/me, project create/list/delete, tiny CSV
  upload/list, project execute/poll/download, and scheduler smoke.

Login/logout skip reason:
  compose does not seed deterministic user credentials; creating one here
  would mutate application data and make this smoke order-dependent.

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

case "$TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "ERROR: PLENORA_ACCEPTANCE_TIMEOUT_SECONDS must be a positive integer." >&2
        exit 2
        ;;
esac
case "$INTERVAL_SECONDS" in
    ''|*[!0-9]*)
        echo "ERROR: PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS must be a positive integer." >&2
        exit 2
        ;;
esac
case "$CURL_MAX_TIME_SECONDS" in
    ''|*[!0-9]*)
        echo "ERROR: PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS must be a positive integer." >&2
        exit 2
        ;;
esac
if [ "$TIMEOUT_SECONDS" -le 0 ] || [ "$INTERVAL_SECONDS" -le 0 ] || [ "$CURL_MAX_TIME_SECONDS" -le 0 ]; then
    echo "ERROR: smoke timeouts and intervals must be greater than zero." >&2
    exit 2
fi

run_get_check() {
    local path="$1"
    local url="${BASE_URL}${path}"
    local deadline
    local body_file
    local http_status=""
    local curl_rc=0

    echo "Polling GET ${url} for up to ${TIMEOUT_SECONDS}s"
    deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
    body_file="$(mktemp)"

    while [ "$(date +%s)" -le "$deadline" ]; do
        http_status="$(curl -sS --max-time "$CURL_MAX_TIME_SECONDS" -o "$body_file" -w "%{http_code}" "$url" 2>/dev/null)"
        curl_rc=$?
        if [ $curl_rc -eq 0 ] && [ "$http_status" -ge 200 ] && [ "$http_status" -lt 300 ]; then
            echo "${path} -> HTTP ${http_status}"
            rm -f "$body_file"
            return 0
        fi
        sleep "$INTERVAL_SECONDS"
    done

    echo "ERROR: ${path} did not return 2xx from ${BASE_URL} within ${TIMEOUT_SECONDS}s." >&2
    echo "       Last HTTP status: ${http_status:-000}; curl exit: ${curl_rc}." >&2
    if [ -s "$body_file" ]; then
        echo "Last response body:" >&2
        cat "$body_file" >&2
        echo >&2
    fi
    rm -f "$body_file"
    echo "       Inspect backend logs and dependency health before retrying." >&2
    exit 2
}

document_login_logout_skip() {
    echo "Skipping login/logout smoke: compose does not seed deterministic user credentials."
    echo "Creating a user here would mutate application data, so CI only proves /health and /ready."
}

echo "Docker API smoke target: ${BASE_URL}"
run_get_check "/health"
run_get_check "/ready"
document_login_logout_skip

if [ "$INFRASTRUCTURE_ONLY" -eq 1 ]; then
    echo "Infrastructure-only Docker API smoke checks passed."
    exit 0
fi

cat >&2 <<'PENDING'
TODO: Docker API smoke full flow is intentionally pending.

Implemented checks passed: /health and /ready.

Not implemented yet:
  - seed isolated admin/group inside the backend container;
  - POST /api/auth/login, GET /api/auth/me, and POST /api/auth/logout;
  - project create/list/delete;
  - tiny CSV upload/list/delete;
  - project execute/poll/download;
  - optional scheduler manual-run smoke.

This script does not fake acceptance success for those steps. Re-run with
--infrastructure-only to accept only the implemented infrastructure checks.
PENDING
exit 3
