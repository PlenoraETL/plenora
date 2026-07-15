#!/usr/bin/env bash
# Plenora-ETL - Guided installer prototype (non-Docker, single-machine)
#
# BE-GO-READY-INSTALLATION-009-INSTALL-SCRIPT-PROTOTYPE.
#
# This is not a full production installer.
# It is safe, explicit, and idempotent. It does NOT install OS packages
# unless `--install-deps` is passed. It does NOT touch existing
# `backend/.env` secrets unless `--overwrite-secrets` is passed.
#
# Source of truth for the procedure: docs/ops/install-non-docker.md
# (sections 1-9). When this script and that doc disagree, the doc wins.
# This script implements only the steps listed in `STEPS=` below.
#
# Usage:
#   scripts/install.sh                # interactive prompts
#   scripts/install.sh --check        # preflight only: tools + native libs + Python version. No prompts, no writes, no service probes.
#   scripts/install.sh --smoke        # post-install smoke: memory caps visibility + app import + optional health/ready curl + optional cleanroom.ping. Non-destructive, no prompts, no writes.
#   scripts/install.sh --dry-run      # print what would happen, no writes
#   scripts/install.sh --non-interactive  # all answers from env vars
#   scripts/install.sh --install-deps     # apt install pkg list (Ubuntu)
#   scripts/install.sh --overwrite-secrets  # rewrite even if already set
#   scripts/install.sh --help
#
# Smoke env vars (read-only consumers, only honoured with --smoke):
#   PLENORA_SMOKE_BACKEND_URL  e.g. http://127.0.0.1:5004
#                              when set: curl /health and /ready
#   PLENORA_SMOKE_RUN_PING     1 to dispatch `cleanroom.ping` via Dramatiq
#                              (requires reachable RabbitMQ broker, a running
#                              worker, and venv)
#
# Env vars (consumed when present, prompted otherwise):
#   PLENORA_INSTALL_DATABASE_URL    Postgres DSN
#   PLENORA_INSTALL_DB_SCHEMA       default: plenora
#   PLENORA_INSTALL_BROKER_URL      RabbitMQ broker URL (DRAMATIQ_BROKER_URL)
#   PLENORA_INSTALL_BACKUP_BUCKET   optional backup bucket
#   PLENORA_INSTALL_OBJECT_STORAGE_BUCKET  runtime payload bucket
#   PLENORA_INSTALL_S3_ENDPOINT_URL S3-compatible endpoint
#   PLENORA_INSTALL_S3_ACCESS_KEY / PLENORA_INSTALL_S3_SECRET_KEY
#   PLENORA_INSTALL_CLOUD_PROVIDER  secrets provider (default: none)
#   PLENORA_INSTALL_AWS_REGION      e.g. eu-south-1 (CLOUD_PROVIDER=aws)
#   PLENORA_INSTALL_ENVIRONMENT     production | staging | development
#   PLENORA_INSTALL_BACKEND_PORT    default: 5004
#
# Out of scope (still manual after this script):
#   - apt repo for postgresql-client-17 (PGDG)
#   - systemd unit install (`sudo cp` of plenora-*.service)
#   - frontend `npm ci && npm run build` + reverse proxy config
#   - AWS SSM Parameter Store provisioning
#   - bucket creation, IAM, KMS
#
# This script EXITS NON-ZERO if a precondition is missing. Re-run it
# after fixing.

set -Eeuo pipefail

# Globals

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
ENV_FILE="${BACKEND_DIR}/.env"
ENV_EXAMPLE="${BACKEND_DIR}/.env.example"

DRY_RUN=0
NON_INTERACTIVE=0
INSTALL_DEPS=0
OVERWRITE_SECRETS=0
CHECK_ONLY=0
SMOKE_ONLY=0

# ANSI colors (disabled when stdout is not a tty)
if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;34m'
    C_BOLD=$'\033[1m'
    C_RESET=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi

log_info()  { printf "%b%s%b\n" "${C_BLUE}"  "[INFO]  $*" "${C_RESET}"; }
log_ok()    { printf "%b%s%b\n" "${C_GREEN}" "[OK]    $*" "${C_RESET}"; }
log_warn()  { printf "%b%s%b\n" "${C_YELLOW}" "[WARN]  $*" "${C_RESET}"; }
log_err()   { printf "%b%s%b\n" "${C_RED}"   "[ERROR] $*" "${C_RESET}" >&2; }
log_step()  { printf "\n%b== %s ==%b\n" "${C_BOLD}" "$*" "${C_RESET}"; }

die() { log_err "$*"; exit 1; }

# Argv

usage() {
    sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)            DRY_RUN=1 ;;
        --non-interactive)    NON_INTERACTIVE=1 ;;
        --install-deps)       INSTALL_DEPS=1 ;;
        --overwrite-secrets)  OVERWRITE_SECRETS=1 ;;
        --check)              CHECK_ONLY=1 ;;
        --smoke)              SMOKE_ONLY=1 ;;
        --help|-h)            usage ;;
        *)                    die "Unknown argument: $1 (use --help)" ;;
    esac
    shift
done

if (( DRY_RUN )); then
    log_info "DRY RUN: no files will be written, no commands beyond probes."
fi

# Prompts

prompt() {
    # prompt VAR_NAME "Question text" "default value" [secret]
    # If $VAR_NAME is already set non-empty in env, keep it.
    # In --non-interactive, fall back to default; die if empty + no default.
    local var="$1" question="$2" default="${3:-}" secret="${4:-0}"
    local current="${!var:-}"
    if [[ -n "$current" ]]; then
        log_info "Using ${var} from environment."
        return 0
    fi
    if (( NON_INTERACTIVE )); then
        if [[ -z "$default" ]]; then
            die "Non-interactive run: ${var} required (no default)."
        fi
        printf -v "$var" '%s' "$default"
        log_info "Non-interactive: ${var}=${default}"
        return 0
    fi
    local prompt_str="${question}"
    [[ -n "$default" ]] && prompt_str="${prompt_str} [${default}]"
    prompt_str="${prompt_str}: "
    local answer
    if (( secret )); then
        # -s suppresses echo; -p prompts. Newline after.
        read -rsp "$prompt_str" answer
        printf "\n"
    else
        read -rp "$prompt_str" answer
    fi
    if [[ -z "$answer" ]]; then
        answer="$default"
    fi
    if [[ -z "$answer" ]]; then
        die "${var} is required."
    fi
    printf -v "$var" '%s' "$answer"
}

confirm() {
    # confirm "Question?" - returns 0 yes / 1 no.
    local q="$1"
    if (( NON_INTERACTIVE )); then
        return 1  # default: no
    fi
    local yn
    read -rp "${q} [y/N] " yn
    [[ "$yn" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# Step 1 - Prereq probes

require_cmd() {
    local cmd="$1" hint="${2:-}"
    if ! command -v "$cmd" &>/dev/null; then
        log_err "Missing command: ${cmd}${hint:+ - ${hint}}"
        return 1
    fi
    log_ok "Found: $(command -v "$cmd")"
}

# Canonical list of host commands the installer requires. Single source
# of truth; any source-bound test should derive expectations from this
# array (see backend/tests2/shared/test_install_script_packaging_guard.py).
INSTALLER_REQUIRED_COMMANDS=(
    python3.12
    node
    npm
    psql
    curl
    git
)

# Required Python major.minor. Pinned to match all CI lanes
# (.github/workflows/{ci,ci-cleanroom,ci-functional}.yml).
INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR="3.12"

# Native shared libraries the runtime imports through pip wheels. We do
# NOT require building from source: psycopg2-binary bundles libpq, and
# pyarrow ships manylinux wheels. The remaining hard dep is GDAL,
# loaded at runtime by pyogrio / geopandas for GeoPackage / shapefile /
# GDB drivers. We probe via Python's ctypes loader so the answer
# matches what the runtime actually sees.
INSTALLER_REQUIRED_SHARED_LIBS=(
    "gdal:libgdal* (apt: libgdal-dev or libgdal*; required by pyogrio/geopandas at runtime)"
)

step_validate_prereqs() {
    log_step "Step 1 - Validate host prerequisites"

    local missing=0

    # Python 3.12 specifically (per docs/ops/install-non-docker.md section1).
    if command -v python3.12 &>/dev/null; then
        local pyver
        pyver="$(python3.12 --version 2>&1)"
        log_ok "python3.12: ${pyver}"
        # Hard-pin major.minor. python3.12 binary should always report
        # 3.12.x but we assert it explicitly: a compromised PATH or a
        # symlink could redirect to a different interpreter.
        if [[ "$pyver" != *"Python ${INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR}."* ]]; then
            log_err "python3.12 reports an unexpected version: ${pyver} (expected Python ${INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR}.x)."
            missing=1
        fi
    else
        log_err "python3.12 not found - install via apt or pyenv (see docs/ops/install-non-docker.md section1)."
        missing=1
    fi

    require_cmd node "node LTS - see docs/ops/install-non-docker.md section1" || missing=1
    require_cmd npm "shipped with node" || missing=1
    require_cmd psql "postgresql-client-17 (apt PGDG repo on Ubuntu 24.04)" || missing=1
    # Il broker (RabbitMQ) non richiede un client host: la reachability si
    # prova via TCP con python3.12 in step_probe_services (no redis-cli).
    require_cmd curl "apt install curl" || missing=1
    require_cmd git "apt install git" || missing=1

    if (( missing )); then
        if (( INSTALL_DEPS )); then
            log_warn "--install-deps was passed but auto-install is INTENTIONALLY not implemented in the prototype."
            log_warn "Run the apt install command from docs/ops/install-non-docker.md section1 manually, then re-run this script."
        fi
        if (( CHECK_ONLY )); then
            return 1
        fi
        die "Missing host prerequisites; see logs above."
    fi
}

# Step 1b - Cleanroom packaging guard (BE-GO-READY-INSTALLATION-013)
# Verifies what the running OS actually exposes to the Python runtime
# beyond the bare `command -v` checks: native shared libs, pg_dump
# version match against the target Postgres server, and a smoke
# smoke import on the venv if one already exists. None of these probes
# install anything; they only report what is or is not present.
step_validate_packaging() {
    log_step "Step 1b - Validate native packaging (cleanroom guard)"

    local missing=0

    # Shared libraries via Python's ctypes loader.
    # ctypes.util.find_library returns "" when the library is not on
    # the dynamic linker path. Empty here means the runtime CANNOT
    # load the lib even if `apt list --installed` shows the package -
    # this is the signal that matches what the app sees at boot.
    if command -v python3.12 &>/dev/null; then
        local entry libname libhint found
        for entry in "${INSTALLER_REQUIRED_SHARED_LIBS[@]}"; do
            libname="${entry%%:*}"
            libhint="${entry#*:}"
            found="$(
                python3.12 - <<PYEOF 2>/dev/null
import ctypes.util
print(ctypes.util.find_library("${libname}") or "")
PYEOF
            )"
            if [[ -n "$found" ]]; then
                log_ok "shared lib '${libname}' resolves to: ${found}"
            else
                log_err "shared lib '${libname}' NOT found by ctypes loader. ${libhint}"
                missing=1
            fi
        done
    else
        log_warn "python3.12 missing - shared library probes skipped (see Step 1)."
        missing=1
    fi

    # pg_dump major version match.
    # docs/ops/runtime-prereqs.md "Binari di sistema (host)" pins this
    # contract: pg_dump major must match the target Postgres server's
    # major. Mismatched dumps fail with cryptic errors. We probe only
    # the local pg_dump version here; the server-side major is
    # operator territory and gets checked downstream by the backup
    # unit. A WARN (not ERR) keeps the gate non-blocking when ops
    # have intentionally selected a different toolchain.
    if command -v pg_dump &>/dev/null; then
        local pgdump_ver
        pgdump_ver="$(pg_dump --version 2>/dev/null | head -n 1 || echo unknown)"
        log_ok "pg_dump: ${pgdump_ver}"
    else
        log_warn "pg_dump not found - backup units will fail at runtime (apt: postgresql-client-NN matched to server)."
    fi

    # Optional venv smoke import.
    # If the operator has already created backend/venv (e.g. running
    # --check after step 6 of the runbook), do a strict importability
    # check on the runtime deps that pull in native code. This catches
    # corrupted wheels, ABI mismatches, and venv-vs-host Python
    # mismatches that only surface at app boot. Skipped silently when
    # the venv is absent.
    local venv_python="${BACKEND_DIR}/venv/bin/python"
    if [[ -x "$venv_python" ]]; then
        log_info "Smoke-importing runtime deps via ${venv_python}..."
        if "$venv_python" - <<'PYEOF' 2>&1
import importlib
import sys
modules = ("psycopg2", "dramatiq", "fastapi", "uvicorn",
           "pyarrow", "pandas", "pyogrio", "geopandas", "shapely")
failed = []
for m in modules:
    try:
        importlib.import_module(m)
    except Exception as exc:
        failed.append((m, type(exc).__name__, str(exc)))
if failed:
    for m, etype, emsg in failed:
        print(f"FAIL {m}: {etype}: {emsg}")
    sys.exit(1)
print("OK: all runtime deps import")
PYEOF
        then
            log_ok "venv smoke import OK"
        else
            log_err "venv smoke import FAILED - see lines above. Likely cause: corrupted wheel, ABI mismatch, or venv built against a different Python."
            missing=1
        fi
    else
        log_info "backend/venv not found - skipping smoke import (created at install runbook step 6)."
    fi

    if (( missing )); then
        if (( CHECK_ONLY )); then
            return 1
        fi
        die "Cleanroom packaging guard failed; see logs above."
    fi
}

# Step S - Post-install smoke (--smoke)
# Non-destructive, prompt-free, read-only verification of a freshly
# installed clean machine. Implements rows 7 and 8 of the canonical
# clean-install smoke harness documented in
# docs/ops/install-marco-dry-run.md section 5, and adds two optional
# probes (health/ready curl, cleanroom.ping) for the operator who
# already started uvicorn + worker.
#
# Contracts:
#   - never writes a file, never restarts a service, never installs
#     a package;
#   - exits 0 only when every attempted check passed (caps + import +
#     any optional probe the operator opted into);
#   - exits 1 on the first hard fail;
#   - skipped checks (no venv, no backend URL, no broker) emit WARN
#     but do not fail the harness.

# Memory caps locked by the harness guard test
# `tests2/shared/test_install_smoke_harness_doc_guard.py::test_harness_5_2_lists_canonical_memory_caps`.
# Single source of truth on the script side; doc cross-checks via the
# guard test - if a cap is added to the runtime, both lists update.
INSTALLER_SMOKE_MEMORY_CAPS=(
    LOAD_FILE_MAX_BYTES
    WORKER_RSS_MAX_BYTES
    RESOURCE_MAX_BYTES
    ARTIFACT_PREVIEW_MAX_BYTES
    CROSS_JOIN_MAX_OUTPUT_ROWS
    MULTI_JOIN_MAX_OUTPUT_ROWS
)

step_smoke_caps_visibility() {
    log_step "Smoke 1 - Memory caps visibility"
    local venv_python="${BACKEND_DIR}/venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        log_warn "backend/venv not found - skipping caps visibility (run install runbook step 6 first)."
        return 2
    fi
    local script="" cap
    script+="from app.config import settings"$'\n'
    for cap in "${INSTALLER_SMOKE_MEMORY_CAPS[@]}"; do
        script+="print(f\"${cap}={settings.get('${cap}')}\")"$'\n'
    done
    if (cd "$BACKEND_DIR" && "$venv_python" -c "$script"); then
        log_ok "Memory caps visible (settings.get fallthrough OK)."
        return 0
    fi
    log_err "Failed to read memory caps via settings.get - venv or backend/.env may be broken."
    return 1
}

step_smoke_app_import() {
    log_step "Smoke 2 - Backend import sanity"
    local venv_python="${BACKEND_DIR}/venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        log_warn "backend/venv not found - skipping import sanity."
        return 2
    fi
    # Importing app.config is sufficient to exercise the Settings
    # singleton, .env load, and ADR-0021 boundary without booting
    # FastAPI or touching the DB.
    if (cd "$BACKEND_DIR" && "$venv_python" -c "import app.config; import workers.dramatiq_app"); then
        log_ok "app.config and workers.dramatiq_app import cleanly."
        return 0
    fi
    log_err "Backend import failed - check venv installation and backend/.env."
    return 1
}

step_smoke_health_ready() {
    log_step "Smoke 3 - /health and /ready"
    local url="${PLENORA_SMOKE_BACKEND_URL:-}"
    if [[ -z "$url" ]]; then
        log_info "PLENORA_SMOKE_BACKEND_URL not set - skipping health/ready probes."
        return 2
    fi
    if ! command -v curl &>/dev/null; then
        log_warn "curl not found - skipping health/ready probes."
        return 2
    fi
    local rc=0
    if curl -fsS --max-time 5 "${url%/}/health" >/dev/null; then
        log_ok "GET ${url%/}/health returned 200."
    else
        log_err "GET ${url%/}/health failed - is uvicorn running on ${url}?"
        rc=1
    fi
    if curl -fsS --max-time 10 "${url%/}/ready" >/dev/null; then
        log_ok "GET ${url%/}/ready returned 200 (all readiness checks green)."
    else
        log_err "GET ${url%/}/ready failed - inspect 'journalctl -u plenora-backend -n 100' for the failed gate."
        rc=1
    fi
    return $rc
}

step_smoke_cleanroom_ping() {
    log_step "Smoke 4 - Dramatiq cleanroom.ping round-trip"
    if [[ "${PLENORA_SMOKE_RUN_PING:-0}" != "1" ]]; then
        log_info "PLENORA_SMOKE_RUN_PING not set to 1 - skipping cleanroom.ping (requires foreground worker)."
        return 2
    fi
    local venv_python="${BACKEND_DIR}/venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        log_warn "backend/venv not found - skipping cleanroom.ping."
        return 2
    fi
    # Plenora 2.0: dispatch via Dramatiq su RabbitMQ; l'esito lo scrive il
    # worker su PostgreSQL (task_result_store). Niente AsyncResult/Redis: si
    # polla lo stato con una deadline LIMITATA (time.monotonic()+30), mai un
    # loop illimitato.
    local out
    if ! out="$(cd "$BACKEND_DIR" && "$venv_python" - <<'PYEOF' 2>&1
import time
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store

handle = dispatch_task_by_name("cleanroom.ping")
store = default_task_result_store()
deadline = time.monotonic() + 30
state = "PENDING"
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ("SUCCESS", "FAILURE"):
        state = record.state
        break
    time.sleep(0.5)
print(state)
PYEOF
    )"; then
        log_err "cleanroom.ping round-trip failed: ${out}"
        log_info "Hint: pika.exceptions.AMQPConnectionError -> broker URL wrong or RabbitMQ down."
        log_info "Hint: state stuck PENDING -> worker not consuming tasks (check the foreground worker terminal)."
        return 1
    fi
    if [[ "${out: -7}" == "SUCCESS" ]]; then
        log_ok "cleanroom.ping reached SUCCESS on PostgreSQL (dispatch + broker + result-store OK)."
        return 0
    fi
    log_err "cleanroom.ping did not reach SUCCESS within the deadline: ${out}"
    return 1
}

step_smoke_summary() {
    local caps_rc="$1" import_rc="$2" hr_rc="$3" ping_rc="$4"
    log_step "Smoke summary"
    local hard_fail=0
    # rc==0 OK, rc==1 hard fail, rc==2 skipped (warn-only)
    [[ $caps_rc -eq 1 ]] && hard_fail=1
    [[ $import_rc -eq 1 ]] && hard_fail=1
    [[ $hr_rc -eq 1 ]] && hard_fail=1
    [[ $ping_rc -eq 1 ]] && hard_fail=1
    if (( hard_fail )); then
        log_err "--smoke: FAIL - one or more harness checks did not pass."
        log_info "Re-read docs/ops/install-marco-dry-run.md section 5 and the [ERROR] lines above."
        exit 1
    fi
    log_ok "--smoke: PASS - every attempted check is green."
    log_info "Skipped checks (rc=2) are non-fatal but reduce coverage."
    log_info "To raise coverage: export PLENORA_SMOKE_BACKEND_URL and PLENORA_SMOKE_RUN_PING=1 then re-run."
    exit 0
}

step_check_only_summary() {
    # Called only when --check is passed. The two validators above
    # have already returned non-zero to signal failure. We rebuild a
    # 1-line summary and exit with the right code so CI / wrappers
    # can branch on it.
    local prereq_rc="$1" packaging_rc="$2"
    if (( prereq_rc == 0 && packaging_rc == 0 )); then
        log_ok "--check: PASS - all install prerequisites and packaging guards green."
        exit 0
    fi
    log_err "--check: FAIL - host is not ready. See [ERROR] lines above."
    log_info "Required commands: ${INSTALLER_REQUIRED_COMMANDS[*]}"
    log_info "Required Python: ${INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR}.x"
    log_info "Required shared libs: ${INSTALLER_REQUIRED_SHARED_LIBS[*]}"
    exit 1
}

# Step 1c - Env file + venv status (--check informational, BE-GO-READY-INSTALL-020)
# Reports whether backend/.env and backend/venv already exist.
# Called only in --check mode; never reads secret values, never writes.
# Does not affect the PASS/FAIL verdict from step_check_only_summary.
step_check_env_status() {
    log_step "Check 1c: env file + venv status"
    if [[ -f "${ENV_FILE}" ]]; then
        log_ok "backend/.env: EXISTS (run --smoke to verify values)."
    else
        log_warn "backend/.env: NOT FOUND (created by the full install run)."
    fi
    if [[ -x "${BACKEND_DIR}/venv/bin/python" ]]; then
        local py
        py="$("${BACKEND_DIR}/venv/bin/python" --version 2>&1)"
        log_ok "backend/venv: EXISTS (${py})."
    else
        log_info "backend/venv: NOT FOUND (created at install runbook step 6)."
    fi
}

# Step 2 - Gather configuration

step_gather_config() {
    log_step "Step 2 - Gather configuration"

    # Map env-prefixed inputs onto local working vars without clobbering
    # already-set inputs.
    DATABASE_URL="${PLENORA_INSTALL_DATABASE_URL:-}"
    DB_SCHEMA="${PLENORA_INSTALL_DB_SCHEMA:-}"
    BROKER_URL="${PLENORA_INSTALL_BROKER_URL:-}"
    BACKUP_BUCKET="${PLENORA_INSTALL_BACKUP_BUCKET:-}"
    OBJECT_STORAGE_BUCKET="${PLENORA_INSTALL_OBJECT_STORAGE_BUCKET:-}"
    OBJECT_STORAGE_BACKEND="${PLENORA_INSTALL_OBJECT_STORAGE_BACKEND:-}"
    S3_ENDPOINT_URL="${PLENORA_INSTALL_S3_ENDPOINT_URL:-}"
    S3_ACCESS_KEY="${PLENORA_INSTALL_S3_ACCESS_KEY:-}"
    S3_SECRET_KEY="${PLENORA_INSTALL_S3_SECRET_KEY:-}"
    CLOUD_PROVIDER="${PLENORA_INSTALL_CLOUD_PROVIDER:-}"
    AWS_REGION="${PLENORA_INSTALL_AWS_REGION:-}"
    ENVIRONMENT="${PLENORA_INSTALL_ENVIRONMENT:-}"
    BACKEND_PORT="${PLENORA_INSTALL_BACKEND_PORT:-}"

    prompt ENVIRONMENT \
        "Environment (production | staging | development)" \
        "production"

    prompt DATABASE_URL \
        "DATABASE_URL (postgresql://user:pass@host:5432/dbname)" \
        ""

    prompt DB_SCHEMA \
        "DB_SCHEMA" \
        "plenora"

    prompt BROKER_URL \
        "RabbitMQ broker URL (DRAMATIQ_BROKER_URL)" \
        "amqp://guest:guest@localhost:5672"

    prompt BACKUP_BUCKET \
        "BACKUP_BUCKET (separato dal runtime)" \
        "plenora-backups"

    prompt OBJECT_STORAGE_BUCKET "OBJECT_STORAGE_BUCKET" "plenora-runtime"
    [[ "$OBJECT_STORAGE_BUCKET" != "$BACKUP_BUCKET" ]] || \
        die "OBJECT_STORAGE_BUCKET must be DISTINCT from BACKUP_BUCKET."
    OBJECT_STORAGE_BACKEND="s3_compatible"
    prompt S3_ENDPOINT_URL "S3 endpoint URL" "http://127.0.0.1:8333"
    prompt S3_ACCESS_KEY "S3 access key" "plenora-storage"
    if [[ -z "$S3_SECRET_KEY" ]]; then
        S3_SECRET_KEY="$(read_env_var S3_SECRET_KEY)"
    fi
    if [[ -z "$S3_SECRET_KEY" ]]; then
        S3_SECRET_KEY="$(generate_secret)"
        log_info "Generated a new S3_SECRET_KEY."
    fi
    prompt CLOUD_PROVIDER "Secrets provider (aws | none)" "none"
    if [[ "$CLOUD_PROVIDER" == "aws" ]]; then
        prompt AWS_REGION "AWS_REGION" "eu-south-1"
    fi

    prompt BACKEND_PORT \
        "Backend port" \
        "5004"

    log_ok "Configuration gathered."
}

# Step 3 - Connectivity probes (psql + RabbitMQ broker TCP)

step_probe_services() {
    log_step "Step 3 - Probe DB and RabbitMQ broker connectivity"

    log_info "psql SELECT 1 against ${DATABASE_URL%%@*}@..."
    if (( DRY_RUN )); then
        log_info "[dry-run] skipping psql probe"
    else
        if psql "$DATABASE_URL" -tAc "SELECT 1" >/dev/null 2>&1; then
            log_ok "Postgres reachable, SELECT 1 returned 1."
        else
            die "psql SELECT 1 failed against the configured DATABASE_URL. Check the DSN, the role privileges, and pg_hba.conf."
        fi
    fi

    log_info "AMQP TCP probe against ${BROKER_URL}"
    if (( DRY_RUN )); then
        log_info "[dry-run] skipping broker probe"
    else
        # Il broker e' RabbitMQ (Dramatiq): niente redis-cli, niente client
        # host dedicato. Un connect TCP bounded all'host:porta del broker URL
        # via python3.12 (gia' richiesto) e' sufficiente a fail-fast.
        if python3.12 - "$BROKER_URL" <<'PYEOF'
import socket
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
host = parsed.hostname or "localhost"
port = parsed.port or 5672
try:
    with socket.create_connection((host, port), timeout=5):
        sys.exit(0)
except OSError:
    sys.exit(1)
PYEOF
        then
            log_ok "RabbitMQ broker reachable (TCP) at ${BROKER_URL}."
        else
            die "Broker TCP probe failed against ${BROKER_URL}. Check the URL and that RabbitMQ is running."
        fi
    fi
}

# Step 4 - Generate / merge backend/.env safely (no secret overwrite)

# Single source of truth for which keys this installer is authoritative
# managed by the installer. On every run the merger will set/refresh these to the values the
# script computes from prompts/env. ALL other keys (operator overrides,
# unknown keys, comments, blank lines) are preserved verbatim - see
# BE-GO-READY-INSTALLATION-012-IDEMPOTENT-ENV-MERGE.
#
# Adding a new key to this list means: (a) the installer will start
# writing it on every run, (b) any pre-existing operator value for it
# WILL BE REPLACED on rerun (with the secret-rotation guard the only
# exception, see resolve_secret).
INSTALLER_MANAGED_KEYS=(
    ENVIRONMENT
    DATABASE_URL
    DB_SCHEMA
    DRAMATIQ_BROKER_URL
    PLENORA_ENCRYPTION_KEY
    JWT_SECRET_KEY
    BACKUP_BUCKET
    OBJECT_STORAGE_ENABLED
    OBJECT_STORAGE_BACKEND
    OBJECT_STORAGE_BUCKET
    PLENORA_ARTIFACT_BUCKET
    S3_ENDPOINT_URL
    S3_ACCESS_KEY
    S3_SECRET_KEY
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    S3_BUCKET
    CLOUD_PROVIDER
    AWS_REGION
    PLENORA_BACKEND_PORT
)

is_managed_key() {
    local needle="$1" key
    for key in "${INSTALLER_MANAGED_KEYS[@]}"; do
        [[ "$key" == "$needle" ]] && return 0
    done
    return 1
}

# Reads existing key from .env if file exists; prints empty otherwise.
read_env_var() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || { printf ''; return; }
    # Match `KEY=...` not commented; first occurrence; tolerate quotes.
    awk -F= -v key="$key" '
        /^[[:space:]]*#/ { next }
        $1 == key {
            sub(/^[^=]*=/, "")
            sub(/^["'"'"']/, "")
            sub(/["'"'"']$/, "")
            print
            exit
        }' "$ENV_FILE"
}

# Generate a URL-safe random token via Python (always available since
# we required python3.12 in step 1). 48 bytes approx 64 chars.
generate_secret() {
    python3.12 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

# Resolve a secret: keep existing if non-empty unless --overwrite-secrets.
#
# IMPORTANT: this function is called inside $(...) substitution so its
# stdout becomes the secret value. Diagnostic log lines MUST go to
# stderr (>&2) - otherwise they would be appended to the secret on
# every merge rerun and silently corrupt the encrypted-key contract
# (BE-GO-READY-INSTALLATION-012).
resolve_secret() {
    local key="$1" generator_cmd="$2"
    local existing
    existing="$(read_env_var "$key")"
    if [[ -n "$existing" && $OVERWRITE_SECRETS -eq 0 ]]; then
        log_info "${key} already present in ${ENV_FILE} - preserved (use --overwrite-secrets to rotate)." >&2
        printf '%s' "$existing"
        return 0
    fi
    if [[ -n "$existing" && $OVERWRITE_SECRETS -eq 1 ]]; then
        log_warn "${key} EXISTS but --overwrite-secrets requested. Rotating will INVALIDATE encrypted records / sessions tied to the old value." >&2
        if ! confirm "Confirm overwrite of ${key}?"; then
            log_info "${key} preserved." >&2
            printf '%s' "$existing"
            return 0
        fi
    fi
    log_info "Generating new ${key}..." >&2
    eval "$generator_cmd"
}

# Build the associative array of CURRENT computed values for managed
# keys. Conditional keys (storage-policy-dependent, cloud-provider-
# dependent) are left UNSET in the array so the merger does not write
# nor overwrite them when not applicable. The merger uses
# `${MANAGED_VALUES[$k]:-}` and only emits when non-empty.
build_managed_values() {
    declare -gA MANAGED_VALUES=()
    MANAGED_VALUES[ENVIRONMENT]="$ENVIRONMENT"
    MANAGED_VALUES[DATABASE_URL]="$DATABASE_URL"
    MANAGED_VALUES[DB_SCHEMA]="$DB_SCHEMA"
    MANAGED_VALUES[DRAMATIQ_BROKER_URL]="$BROKER_URL"
    MANAGED_VALUES[PLENORA_ENCRYPTION_KEY]="$PLENORA_ENCRYPTION_KEY"
    MANAGED_VALUES[JWT_SECRET_KEY]="$JWT_SECRET_KEY"
    MANAGED_VALUES[BACKUP_BUCKET]="$BACKUP_BUCKET"
    MANAGED_VALUES[PLENORA_BACKEND_PORT]="$BACKEND_PORT"
    MANAGED_VALUES[OBJECT_STORAGE_ENABLED]="true"
    MANAGED_VALUES[OBJECT_STORAGE_BACKEND]="$OBJECT_STORAGE_BACKEND"
    MANAGED_VALUES[OBJECT_STORAGE_BUCKET]="$OBJECT_STORAGE_BUCKET"
    MANAGED_VALUES[PLENORA_ARTIFACT_BUCKET]="$OBJECT_STORAGE_BUCKET"
    MANAGED_VALUES[S3_ENDPOINT_URL]="$S3_ENDPOINT_URL"
    MANAGED_VALUES[S3_ACCESS_KEY]="$S3_ACCESS_KEY"
    MANAGED_VALUES[S3_SECRET_KEY]="$S3_SECRET_KEY"
    # SeaweedFS usa i nomi AWS standard; restano alias dello stesso secret.
    MANAGED_VALUES[AWS_ACCESS_KEY_ID]="$S3_ACCESS_KEY"
    MANAGED_VALUES[AWS_SECRET_ACCESS_KEY]="$S3_SECRET_KEY"
    MANAGED_VALUES[S3_BUCKET]="$OBJECT_STORAGE_BUCKET"
    MANAGED_VALUES[CLOUD_PROVIDER]="$CLOUD_PROVIDER"
    if [[ "$CLOUD_PROVIDER" == "aws" ]]; then
        MANAGED_VALUES[AWS_REGION]="$AWS_REGION"
    fi
}

# Greenfield: write a fresh .env from the installer template. Layout
# uses the same keys the merger will recognize on subsequent runs, so
# the second run is a true no-op when nothing changed.
write_env_template() {
    local out="$1"
    cat > "$out" <<EOF
# Plenora-ETL - generated by scripts/install.sh on $(date -u +'%Y-%m-%dT%H:%M:%SZ')
# Source: docs/ops/install-non-docker.md section6 (decision matrix).
# Lines below in the form KEY=VALUE that match an installer-managed
# key (see scripts/install.sh INSTALLER_MANAGED_KEYS) are refreshed on
# every rerun. ALL other keys, comments, and blank lines are preserved
# verbatim - safe to add LOG_RETENTION_DAYS, CORS_ORIGINS, DB_POOL_MAX,
# etc. by hand and they survive future installer runs.

ENVIRONMENT=${ENVIRONMENT}

# Database
DATABASE_URL=${DATABASE_URL}
DB_SCHEMA=${DB_SCHEMA}

# Task broker (RabbitMQ / Dramatiq)
DRAMATIQ_BROKER_URL=${BROKER_URL}

# Secrets (do NOT rotate post-encryption - see docs/ops/install-non-docker.md section6)
PLENORA_ENCRYPTION_KEY=${PLENORA_ENCRYPTION_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}

# Object storage + backup
BACKUP_BUCKET=${BACKUP_BUCKET}
EOF
    cat >> "$out" <<EOF
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=${OBJECT_STORAGE_BACKEND}
OBJECT_STORAGE_BUCKET=${OBJECT_STORAGE_BUCKET}
PLENORA_ARTIFACT_BUCKET=${OBJECT_STORAGE_BUCKET}
S3_ENDPOINT_URL=${S3_ENDPOINT_URL}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
AWS_ACCESS_KEY_ID=${S3_ACCESS_KEY}
AWS_SECRET_ACCESS_KEY=${S3_SECRET_KEY}
S3_BUCKET=${OBJECT_STORAGE_BUCKET}
CLOUD_PROVIDER=${CLOUD_PROVIDER}
EOF
    if [[ "$CLOUD_PROVIDER" == "aws" ]]; then
        cat >> "$out" <<EOF
AWS_REGION=${AWS_REGION}
EOF
    fi
    cat >> "$out" <<EOF

# Backend port (frontend reverse proxy points here)
PLENORA_BACKEND_PORT=${BACKEND_PORT}
EOF
}

# Merge: read $ENV_FILE line-by-line into $out. For each line:
#   - blank or comment              -> preserved verbatim;
#   - KEY=... where KEY is managed  -> KEY refreshed with current value;
#   - KEY=... where KEY is unknown  -> preserved verbatim (operator override);
#   - anything else (malformed)     -> preserved verbatim.
# After processing the file, any installer-managed key that did NOT
# already exist gets appended with a clear "Added by installer ..."
# header so the operator sees what's new.
#
# Counters (only_changed/preserved/appended) are emitted as log lines
# at the end so the operator gets a 1-line summary of the diff.
merge_env_into() {
    local out="$1"
    local -A seen=()
    local changed=0 preserved=0 appended=0
    local line key new_value

    : > "$out"

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Blank or comment - preserve verbatim.
        if [[ -z "${line//[[:space:]]/}" ]] || [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]]; then
            printf '%s\n' "$line" >> "$out"
            continue
        fi
        # Try to parse KEY=VALUE (KEY = leading non-`=` token).
        if [[ "$line" == *=* ]]; then
            key="${line%%=*}"
            # Strip surrounding whitespace from key only.
            key="${key#"${key%%[![:space:]]*}"}"
            key="${key%"${key##*[![:space:]]}"}"
            # Reject empty or non-identifier keys to avoid eating lines
            # like `=foo` or `KEY with spaces=`.
            if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && is_managed_key "$key"; then
                new_value="${MANAGED_VALUES[$key]:-}"
                if [[ -n "$new_value" ]]; then
                    printf '%s=%s\n' "$key" "$new_value" >> "$out"
                    seen["$key"]=1
                    changed=$(( changed + 1 ))
                    continue
                fi
                # Managed key but no current value (e.g. AWS_REGION when
                # legacy unmanaged settings). Preserve verbatim - the operator
                # may want to keep it for a later switch back.
            fi
        fi
        # Unknown / malformed / managed-but-no-value -> preserve verbatim.
        printf '%s\n' "$line" >> "$out"
        preserved=$(( preserved + 1 ))
    done < "$ENV_FILE"

    # Append any managed keys that were not present in the file.
    local k missing=()
    for k in "${INSTALLER_MANAGED_KEYS[@]}"; do
        if [[ -n "${MANAGED_VALUES[$k]:-}" && -z "${seen[$k]:-}" ]]; then
            missing+=("$k")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        {
            printf '\n'
            printf '# --- Added by scripts/install.sh on %s ---\n' \
                "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
            for k in "${missing[@]}"; do
                printf '%s=%s\n' "$k" "${MANAGED_VALUES[$k]}"
                appended=$(( appended + 1 ))
            done
        } >> "$out"
    fi

    log_info "Merge summary: ${changed} managed key(s) refreshed, ${preserved} non-managed line(s) preserved, ${appended} key(s) appended."
}

step_write_env() {
    log_step "Step 4 - Generate / merge backend/.env safely"

    if [[ ! -f "$ENV_EXAMPLE" ]]; then
        die "Missing ${ENV_EXAMPLE} - sanity check failed. Are you in the repo root?"
    fi

    local greenfield=1
    if [[ -f "$ENV_FILE" ]]; then
        greenfield=0
        log_info "${ENV_FILE} already exists; secrets will be PRESERVED, comments and unknown keys will be kept verbatim, only installer-managed keys will be refreshed."
        if (( OVERWRITE_SECRETS )); then
            log_warn "--overwrite-secrets is active - operator will be prompted per-secret."
        fi
    else
        log_info "${ENV_FILE} does not exist; will create from template."
    fi

    PLENORA_ENCRYPTION_KEY="$(resolve_secret "PLENORA_ENCRYPTION_KEY" "generate_secret")"
    JWT_SECRET_KEY="$(resolve_secret "JWT_SECRET_KEY" "generate_secret")"

    build_managed_values

    if (( DRY_RUN )); then
        if (( greenfield )); then
            log_info "[dry-run] would create ${ENV_FILE}; preview:"
            write_env_template "/dev/stdout"
        else
            log_info "[dry-run] would merge into ${ENV_FILE}; preview of merged file:"
            local tmp_preview
            tmp_preview="$(mktemp "${ENV_FILE}.preview.XXXXXX")"
            merge_env_into "$tmp_preview"
            cat "$tmp_preview"
            rm -f "$tmp_preview"
        fi
        return 0
    fi

    # Atomic write: tmp file + mv. chmod 600 BEFORE the mv so the file
    # never appears in its final location with permissive bits.
    local tmp
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    if (( greenfield )); then
        write_env_template "$tmp"
    else
        merge_env_into "$tmp"
    fi
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
    log_ok "Wrote ${ENV_FILE} (chmod 600)."
}

# Step 5 - Print next steps

step_next_steps() {
    log_step "Step 5 - Next steps (NOT performed by this prototype)"

    cat <<EOF

The prototype installer has finished its safe portion. The remaining
steps are operator-driven and documented in
docs/ops/install-non-docker.md (sectionsection7-9):

  1. Backend venv:
       cd ${BACKEND_DIR}
       python3.12 -m venv venv
       ./venv/bin/pip install --require-hashes -r requirements.lock

  2. SeaweedFS + systemd units (requires sudo; salta la unit SeaweedFS
     quando S3_ENDPOINT_URL punta a un provider esterno):
       archive="$(mktemp)"
       curl -fsSLo "\${archive}" https://github.com/seaweedfs/seaweedfs/releases/download/4.29/linux_amd64.tar.gz
       echo "f090992272353bbf0e00ea5a26c452beb81fb1deac6a240c768b5b22fcbb7202  \${archive}" | sha256sum -c -
       sudo tar -xzf "\${archive}" -C /usr/local/bin weed
       rm -f "\${archive}"
       sudo mkdir -p /home/ubuntu/plenora-object-storage
       sudo chown ubuntu:ubuntu /home/ubuntu/plenora-object-storage
       cd ${BACKEND_DIR}/deploy/systemd
       sudo cp plenora-object-storage.service plenora-backend.service plenora-worker-data.service \\
              plenora-worker-control.service plenora-beat.service \\
              /etc/systemd/system/
       sudo cp plenora-backup.service plenora-backup.timer \\
              plenora-backup-test.service plenora-backup-test.timer \\
              /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now \\
            plenora-object-storage.service plenora-backend.service plenora-worker-data.service \\
            plenora-worker-control.service plenora-beat.service \\
            plenora-backup.timer plenora-backup-test.timer

     IF host layout != /home/ubuntu/plenora-etl/backend, EDIT the unit
     files BEFORE the cp (see docs/ops/install-non-docker.md section7).

  3. Frontend build + reverse proxy:
       cd ${REPO_ROOT}/frontend
       npm ci
       npm run build
     Configure nginx/Caddy/Apache to:
       - serve ${REPO_ROOT}/frontend/dist/ at the public origin;
       - proxy /api/*, /health, /ready, /auth/* to 127.0.0.1:${BACKEND_PORT};

  4. Smoke test:
       curl -fsS http://127.0.0.1:${BACKEND_PORT}/health
       curl -fsS http://127.0.0.1:${BACKEND_PORT}/ready
     Both should return HTTP 200 with all checks green.

  5. Acceptance criteria (docs/ops/install-non-docker.md section0):
       systemctl is-active plenora-object-storage plenora-backend plenora-worker-data \\
            plenora-worker-control plenora-beat
     should print "active" five times (quattro con storage S3 esterno).

If /ready returns 503, inspect:
  journalctl -u plenora-backend -n 100 --no-pager
The four boot gates (validate_startup, validate_storage_config,
init_all_dbs+migrations, _repair_psycopg2_ssl_env) log loud on
failure.
EOF
}

# Main

main() {
    log_info "Plenora-ETL guided installer (prototype)"
    log_info "Repo root: ${REPO_ROOT}"
    log_info "Source of truth: docs/ops/install-non-docker.md"

    if (( CHECK_ONLY )); then
        log_info "--check: preflight only (no prompts, no writes, no service probes)."
        # Do not die on first failure; collect both signals.
        local prereq_rc=0 packaging_rc=0
        step_validate_prereqs || prereq_rc=$?
        step_validate_packaging || packaging_rc=$?
        step_check_env_status
        step_check_only_summary "$prereq_rc" "$packaging_rc"
    fi

    if (( SMOKE_ONLY )); then
        log_info "--smoke: post-install verification (no prompts, no writes, no service restarts)."
        # Collect every rc; never short-circuit so the operator sees
        # the full state of the harness in one run.
        local caps_rc=0 import_rc=0 hr_rc=0 ping_rc=0
        step_smoke_caps_visibility || caps_rc=$?
        step_smoke_app_import || import_rc=$?
        step_smoke_health_ready || hr_rc=$?
        step_smoke_cleanroom_ping || ping_rc=$?
        step_smoke_summary "$caps_rc" "$import_rc" "$hr_rc" "$ping_rc"
    fi

    step_validate_prereqs
    step_validate_packaging
    step_gather_config
    step_probe_services
    step_write_env
    step_next_steps

    log_ok "Prototype installer completed."
}

main "$@"
