#!/usr/bin/env bash
# =============================================================================
# Plenora-ETL Docker API smoke runner
# =============================================================================
#
# Targets an already running backend and performs bounded checks against the
# real API surface. The full flow requires deterministic credentials seeded
# inside the backend container with:
#
#   PLENORA_SMOKE_PASSWORD=... python -m app.core.auth.smoke_seed
#
# Usage:
#   bash scripts/docker-api-smoke.sh --infrastructure-only
#   PLENORA_SMOKE_USERNAME=smoke-user \
#   PLENORA_SMOKE_PASSWORD=smoke-password-change-me \
#       bash scripts/docker-api-smoke.sh
#
# Exit codes:
#   0 : implemented checks passed.
#   2 : invocation error or failed implemented check.
# =============================================================================

set -uo pipefail

INFRASTRUCTURE_ONLY=0
BASE_URL="${PLENORA_ACCEPTANCE_BASE_URL:-http://127.0.0.1:5004}"
BASE_URL="${BASE_URL%/}"
TIMEOUT_SECONDS="${PLENORA_ACCEPTANCE_TIMEOUT_SECONDS:-90}"
INTERVAL_SECONDS="${PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS:-2}"
CURL_MAX_TIME_SECONDS="${PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS:-5}"
SMOKE_USERNAME="${PLENORA_SMOKE_USERNAME:-}"
SMOKE_PASSWORD="${PLENORA_SMOKE_PASSWORD:-}"
PROJECT_ID=""
TOKEN=""
TMP_FILES=""

cleanup_smoke_project_on_exit() {
    if [ -n "$PROJECT_ID" ] && [ -n "$TOKEN" ]; then
        curl -sS --max-time "$CURL_MAX_TIME_SECONDS" \
            -X DELETE "${BASE_URL}/api/projects/${PROJECT_ID}" \
            -H "Authorization: Bearer ${TOKEN}" \
            >/dev/null 2>&1 || true
        PROJECT_ID=""
    fi
}

cleanup_tmp_files() {
    for file in $TMP_FILES; do
        rm -f "$file"
    done
}

cleanup_on_exit() {
    cleanup_smoke_project_on_exit
    cleanup_tmp_files
}
trap cleanup_on_exit EXIT

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
  PLENORA_SMOKE_USERNAME      Seeded smoke username. Required for full flow.
  PLENORA_SMOKE_PASSWORD      Seeded smoke password. Required for full flow.

Implemented full flow:
  GET /health
  GET /ready
  POST /api/auth/login
  GET /api/auth/me
  POST /api/projects
  GET /api/projects
  DELETE /api/projects/{id}
  POST /api/auth/logout

Use --infrastructure-only when you only want /health and /ready.
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
    exit 2
fi

if ! command -v python >/dev/null 2>&1; then
    echo "ERROR: 'python' binary not found on PATH." >&2
    exit 2
fi

case "$TIMEOUT_SECONDS" in ''|*[!0-9]*) echo "ERROR: PLENORA_ACCEPTANCE_TIMEOUT_SECONDS must be a positive integer." >&2; exit 2 ;; esac
case "$INTERVAL_SECONDS" in ''|*[!0-9]*) echo "ERROR: PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS must be a positive integer." >&2; exit 2 ;; esac
case "$CURL_MAX_TIME_SECONDS" in ''|*[!0-9]*) echo "ERROR: PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS must be a positive integer." >&2; exit 2 ;; esac
if [ "$TIMEOUT_SECONDS" -le 0 ] || [ "$INTERVAL_SECONDS" -le 0 ] || [ "$CURL_MAX_TIME_SECONDS" -le 0 ]; then
    echo "ERROR: smoke timeouts and intervals must be greater than zero." >&2
    exit 2
fi

new_tmp_file() {
    local file
    file="$(mktemp)"
    TMP_FILES="${TMP_FILES} ${file}"
    echo "$file"
}

run_get_check() {
    local path="$1"
    local url="${BASE_URL}${path}"
    local deadline
    local body_file
    local http_status=""
    local curl_rc=0

    echo "Polling GET ${url} for up to ${TIMEOUT_SECONDS}s"
    deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
    body_file="$(new_tmp_file)"

    while [ "$(date +%s)" -le "$deadline" ]; do
        http_status="$(curl -sS --max-time "$CURL_MAX_TIME_SECONDS" -o "$body_file" -w "%{http_code}" "$url" 2>/dev/null)"
        curl_rc=$?
        if [ $curl_rc -eq 0 ] && [ "$http_status" -ge 200 ] && [ "$http_status" -lt 300 ]; then
            echo "${path} -> HTTP ${http_status}"
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
    exit 2
}

json_get() {
    local file="$1"
    local path="$2"
    python - "$file" "$path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
for part in sys.argv[2].split("."):
    data = data[int(part)] if isinstance(data, list) else data[part]
print(data)
PY
}

login_payload_json() {
    python - "$SMOKE_USERNAME" "$SMOKE_PASSWORD" <<'PY'
import json
import sys

print(json.dumps(
    {"username": sys.argv[1], "password": sys.argv[2]},
    separators=(",", ":"),
))
PY
}

project_payload_json() {
    local project_name="$1"
    python - "$project_name" <<'PY'
import json
import sys

print(json.dumps(
    {
        "name": sys.argv[1],
        "description": "Docker API smoke",
        "project_type": "estrai",
    },
    separators=(",", ":"),
))
PY
}

curl_json() {
    local method="$1"
    local path="$2"
    local body="$3"
    local token="${4:-}"
    local expected="$5"
    local body_file
    local status
    local auth_args=()
    local data_args=()

    body_file="$(new_tmp_file)"
    if [ -n "$token" ]; then
        auth_args=(-H "Authorization: Bearer ${token}")
    fi
    if [ -n "$body" ]; then
        data_args=(--data "$body")
    fi

    status="$(
        curl -sS --max-time "$CURL_MAX_TIME_SECONDS" \
            -o "$body_file" -w "%{http_code}" \
            -X "$method" "${BASE_URL}${path}" \
            -H "Content-Type: application/json" \
            "${auth_args[@]}" \
            "${data_args[@]}" \
            2>/dev/null
    )"
    if [ "$status" != "$expected" ]; then
        echo "ERROR: ${method} ${path} returned HTTP ${status}, expected ${expected}." >&2
        if [ -s "$body_file" ]; then
            echo "Response body:" >&2
            cat "$body_file" >&2
            echo >&2
        fi
        exit 2
    fi
    echo "$body_file"
}

require_full_flow_credentials() {
    if [ -z "$SMOKE_USERNAME" ] || [ -z "$SMOKE_PASSWORD" ]; then
        echo "ERROR: PLENORA_SMOKE_USERNAME and PLENORA_SMOKE_PASSWORD are required for full smoke." >&2
        echo "       Seed them first with: python -m app.core.auth.smoke_seed" >&2
        exit 2
    fi
}

login_smoke_user() {
    local body_file
    local payload
    payload="$(login_payload_json)"
    body_file="$(
        curl_json "POST" "/api/auth/login" \
            "$payload" \
            "" "200"
    )"
    TOKEN="$(json_get "$body_file" "access_token")"
    if [ -z "$TOKEN" ]; then
        echo "ERROR: login response did not include access_token." >&2
        exit 2
    fi
    echo "Authenticated smoke user ${SMOKE_USERNAME}"
}

check_auth_me() {
    curl_json "GET" "/api/auth/me" "" "$TOKEN" "200" >/dev/null
    echo "Authenticated /api/auth/me passed"
}

create_smoke_project() {
    local body_file
    local payload
    local project_name
    project_name="docker-smoke-$(date +%s)-$$"
    payload="$(project_payload_json "$project_name")"
    body_file="$(
        curl_json "POST" "/api/projects" \
            "$payload" \
            "$TOKEN" "201"
    )"
    PROJECT_ID="$(json_get "$body_file" "metadata.id")"
    if [ -z "$PROJECT_ID" ]; then
        echo "ERROR: project create response did not include metadata.id." >&2
        exit 2
    fi
    echo "Created smoke project ${PROJECT_ID}"
}

list_smoke_projects() {
    local body_file
    body_file="$(curl_json "GET" "/api/projects" "" "$TOKEN" "200")"
    python - "$body_file" "$PROJECT_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)
project_id = sys.argv[2]
projects = payload.get("projects", [])
if not any(project.get("id") == project_id for project in projects):
    raise SystemExit(f"project {project_id} not found in /api/projects response")
PY
    echo "Listed smoke project ${PROJECT_ID}"
}

delete_smoke_project() {
    if [ -z "$PROJECT_ID" ]; then
        return
    fi
    curl_json "DELETE" "/api/projects/${PROJECT_ID}" "" "$TOKEN" "204" >/dev/null
    echo "Deleted smoke project ${PROJECT_ID}"
    PROJECT_ID=""
}

logout_smoke_user() {
    curl_json "POST" "/api/auth/logout" "{}" "$TOKEN" "200" >/dev/null
    TOKEN=""
    echo "Logged out smoke user"
}

echo "Docker API smoke target: ${BASE_URL}"
run_get_check "/health"
run_get_check "/ready"

if [ "$INFRASTRUCTURE_ONLY" -eq 1 ]; then
    echo "Infrastructure-only Docker API smoke checks passed."
    exit 0
fi

require_full_flow_credentials
login_smoke_user
check_auth_me
create_smoke_project
list_smoke_projects
delete_smoke_project
logout_smoke_user

echo "Full Docker API smoke passed."
