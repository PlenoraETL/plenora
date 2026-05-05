#!/usr/bin/env bash
# =============================================================================
# Plenora-ETL - Guided installer prototype (non-Docker, single-machine)
# =============================================================================
#
# BE-GO-READY-INSTALLATION-009-INSTALL-SCRIPT-PROTOTYPE.
#
# This is a FIRST PROTOTYPE. It is NOT a full production installer.
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
#   scripts/install.sh --dry-run      # print what would happen, no writes
#   scripts/install.sh --non-interactive  # all answers from env vars
#   scripts/install.sh --install-deps     # apt install pkg list (Ubuntu)
#   scripts/install.sh --overwrite-secrets  # rewrite even if already set
#   scripts/install.sh --help
#
# Env vars (consumed when present, prompted otherwise):
#   PLENORA_INSTALL_DATABASE_URL    Postgres DSN
#   PLENORA_INSTALL_DB_SCHEMA       default: plenora
#   PLENORA_INSTALL_REDIS_URL       Redis URL (broker = result backend)
#   PLENORA_INSTALL_STORAGE_POLICY  db | hybrid | object_storage
#   PLENORA_INSTALL_BACKUP_BUCKET   non-empty even on STORAGE_POLICY=db
#   PLENORA_INSTALL_OBJECT_STORAGE_BUCKET  required if STORAGE_POLICY != db
#   PLENORA_INSTALL_CLOUD_PROVIDER  aws | minio | none (default: aws)
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
# =============================================================================

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
ENV_FILE="${BACKEND_DIR}/.env"
ENV_EXAMPLE="${BACKEND_DIR}/.env.example"

DRY_RUN=0
NON_INTERACTIVE=0
INSTALL_DEPS=0
OVERWRITE_SECRETS=0

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

# ---------------------------------------------------------------------------
# Argv
# ---------------------------------------------------------------------------

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
        --help|-h)            usage ;;
        *)                    die "Unknown argument: $1 (use --help)" ;;
    esac
    shift
done

if (( DRY_RUN )); then
    log_info "DRY RUN: no files will be written, no commands beyond probes."
fi

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Step 1 - Prereq probes
# ---------------------------------------------------------------------------

require_cmd() {
    local cmd="$1" hint="${2:-}"
    if ! command -v "$cmd" &>/dev/null; then
        log_err "Missing command: ${cmd}${hint:+ - ${hint}}"
        return 1
    fi
    log_ok "Found: $(command -v "$cmd")"
}

step_validate_prereqs() {
    log_step "Step 1 - Validate host prerequisites"

    local missing=0

    # Python 3.11 specifically (per docs/ops/install-non-docker.md section1).
    if command -v python3.11 &>/dev/null; then
        log_ok "python3.11: $(python3.11 --version 2>&1)"
    else
        log_err "python3.11 not found - install via apt or pyenv (see docs/ops/install-non-docker.md section1)."
        missing=1
    fi

    require_cmd node "node LTS - see docs/ops/install-non-docker.md section1" || missing=1
    require_cmd npm "shipped with node" || missing=1
    require_cmd psql "postgresql-client-17 (apt PGDG repo on Ubuntu 24.04)" || missing=1
    require_cmd redis-cli "apt install redis-server" || missing=1
    require_cmd curl "apt install curl" || missing=1
    require_cmd git "apt install git" || missing=1

    if (( missing )); then
        if (( INSTALL_DEPS )); then
            log_warn "--install-deps was passed but auto-install is INTENTIONALLY not implemented in the prototype."
            log_warn "Run the apt install command from docs/ops/install-non-docker.md section1 manually, then re-run this script."
        fi
        die "Missing host prerequisites; see logs above."
    fi
}

# ---------------------------------------------------------------------------
# Step 2 - Gather configuration
# ---------------------------------------------------------------------------

step_gather_config() {
    log_step "Step 2 - Gather configuration"

    # Map env-prefixed inputs onto local working vars without clobbering
    # already-set inputs.
    DATABASE_URL="${PLENORA_INSTALL_DATABASE_URL:-}"
    DB_SCHEMA="${PLENORA_INSTALL_DB_SCHEMA:-}"
    REDIS_URL="${PLENORA_INSTALL_REDIS_URL:-}"
    STORAGE_POLICY="${PLENORA_INSTALL_STORAGE_POLICY:-}"
    BACKUP_BUCKET="${PLENORA_INSTALL_BACKUP_BUCKET:-}"
    OBJECT_STORAGE_BUCKET="${PLENORA_INSTALL_OBJECT_STORAGE_BUCKET:-}"
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

    prompt REDIS_URL \
        "Redis URL (CELERY_BROKER_URL = CELERY_RESULT_BACKEND)" \
        "redis://localhost:6379/0"

    prompt STORAGE_POLICY \
        "STORAGE_POLICY (db | hybrid | object_storage)" \
        "db"

    case "$STORAGE_POLICY" in
        db|hybrid|object_storage) ;;
        *) die "Invalid STORAGE_POLICY: ${STORAGE_POLICY}" ;;
    esac

    # validate_storage_config requires non-empty BACKUP_BUCKET on every
    # policy. A dummy is acceptable on STORAGE_POLICY=db.
    prompt BACKUP_BUCKET \
        "BACKUP_BUCKET (non-empty even on db; dummy ok)" \
        "plenora-backup-placeholder"

    if [[ "$STORAGE_POLICY" != "db" ]]; then
        prompt OBJECT_STORAGE_BUCKET \
            "OBJECT_STORAGE_BUCKET (distinct from BACKUP_BUCKET)" \
            ""
        if [[ "$OBJECT_STORAGE_BUCKET" == "$BACKUP_BUCKET" ]]; then
            die "OBJECT_STORAGE_BUCKET must be DISTINCT from BACKUP_BUCKET."
        fi
        prompt CLOUD_PROVIDER \
            "CLOUD_PROVIDER (aws | minio | none)" \
            "aws"
        if [[ "$CLOUD_PROVIDER" == "aws" ]]; then
            prompt AWS_REGION \
                "AWS_REGION" \
                "eu-south-1"
        fi
    fi

    prompt BACKEND_PORT \
        "Backend port" \
        "5004"

    log_ok "Configuration gathered."
}

# ---------------------------------------------------------------------------
# Step 3 - Connectivity probes (psql + redis-cli)
# ---------------------------------------------------------------------------

step_probe_services() {
    log_step "Step 3 - Probe DB and Redis connectivity"

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

    log_info "redis-cli PING against ${REDIS_URL}"
    if (( DRY_RUN )); then
        log_info "[dry-run] skipping redis-cli probe"
    else
        if [[ "$(redis-cli -u "$REDIS_URL" PING 2>&1 || true)" == "PONG" ]]; then
            log_ok "Redis reachable, PING returned PONG."
        else
            die "redis-cli PING failed against ${REDIS_URL}. Check the URL and that the server is running."
        fi
    fi
}

# ---------------------------------------------------------------------------
# Step 4 - Generate backend/.env safely (no secret overwrite)
# ---------------------------------------------------------------------------

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
# we required python3.11 in step 1). 48 bytes approx 64 chars.
generate_secret() {
    python3.11 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

# Resolve a secret: keep existing if non-empty unless --overwrite-secrets.
resolve_secret() {
    local key="$1" generator_cmd="$2"
    local existing
    existing="$(read_env_var "$key")"
    if [[ -n "$existing" && $OVERWRITE_SECRETS -eq 0 ]]; then
        log_info "${key} already present in ${ENV_FILE} - preserved (use --overwrite-secrets to rotate)."
        printf '%s' "$existing"
        return 0
    fi
    if [[ -n "$existing" && $OVERWRITE_SECRETS -eq 1 ]]; then
        log_warn "${key} EXISTS but --overwrite-secrets requested. Rotating will INVALIDATE encrypted records / sessions tied to the old value."
        if ! confirm "Confirm overwrite of ${key}?"; then
            log_info "${key} preserved."
            printf '%s' "$existing"
            return 0
        fi
    fi
    log_info "Generating new ${key}..."
    eval "$generator_cmd"
}

write_env_template() {
    local out="$1"
    cat > "$out" <<EOF
# Plenora-ETL - generated by scripts/install.sh on $(date -u +'%Y-%m-%dT%H:%M:%SZ')
# Source: docs/ops/install-non-docker.md section6 (decision matrix).
# Edit by hand for any tunable not in the prototype scope.

ENVIRONMENT=${ENVIRONMENT}

# --- Database ---
DATABASE_URL=${DATABASE_URL}
DB_SCHEMA=${DB_SCHEMA}

# --- Redis / Celery (single canonical URL) ---
CELERY_BROKER_URL=${REDIS_URL}
CELERY_RESULT_BACKEND=${REDIS_URL}

# --- Secrets (do NOT rotate post-encryption - see docs/ops/install-non-docker.md section6) ---
PLENORA_ENCRYPTION_KEY=${PLENORA_ENCRYPTION_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}

# --- Storage policy + backup ---
STORAGE_POLICY=${STORAGE_POLICY}
BACKUP_BUCKET=${BACKUP_BUCKET}
EOF
    if [[ "$STORAGE_POLICY" != "db" ]]; then
        cat >> "$out" <<EOF
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BUCKET=${OBJECT_STORAGE_BUCKET}
PLENORA_ARTIFACT_BUCKET=${OBJECT_STORAGE_BUCKET}
CLOUD_PROVIDER=${CLOUD_PROVIDER}
EOF
        if [[ "$CLOUD_PROVIDER" == "aws" ]]; then
            cat >> "$out" <<EOF
AWS_REGION=${AWS_REGION}
EOF
        fi
    fi
    cat >> "$out" <<EOF

# --- Backend port (frontend reverse proxy points here) ---
PLENORA_BACKEND_PORT=${BACKEND_PORT}
EOF
}

step_write_env() {
    log_step "Step 4 - Generate backend/.env safely"

    if [[ ! -f "$ENV_EXAMPLE" ]]; then
        die "Missing ${ENV_EXAMPLE} - sanity check failed. Are you in the repo root?"
    fi

    if [[ -f "$ENV_FILE" ]]; then
        log_info "${ENV_FILE} already exists; secrets will be PRESERVED."
        if (( OVERWRITE_SECRETS )); then
            log_warn "--overwrite-secrets is active - operator will be prompted per-secret."
        fi
    else
        log_info "${ENV_FILE} does not exist; will create."
    fi

    PLENORA_ENCRYPTION_KEY="$(resolve_secret "PLENORA_ENCRYPTION_KEY" "generate_secret")"
    JWT_SECRET_KEY="$(resolve_secret "JWT_SECRET_KEY" "generate_secret")"

    if (( DRY_RUN )); then
        log_info "[dry-run] would write ${ENV_FILE}; preview:"
        write_env_template "/dev/stdout"
        return 0
    fi

    # Atomic write: tmp file + mv. chmod 600 BEFORE the mv so the file
    # never appears in its final location with permissive bits.
    local tmp
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    write_env_template "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
    log_ok "Wrote ${ENV_FILE} (chmod 600)."
}

# ---------------------------------------------------------------------------
# Step 5 - Print next steps
# ---------------------------------------------------------------------------

step_next_steps() {
    log_step "Step 5 - Next steps (NOT performed by this prototype)"

    cat <<EOF

The prototype installer has finished its safe portion. The remaining
steps are operator-driven and documented in
docs/ops/install-non-docker.md (sectionsection7-9):

  1. Backend venv:
       cd ${BACKEND_DIR}
       python3.11 -m venv venv
       ./venv/bin/pip install --upgrade pip
       ./venv/bin/pip install -r requirements.txt

  2. systemd units (requires sudo):
       cd ${BACKEND_DIR}/deploy/systemd
       sudo cp plenora-backend.service plenora-celery.service \\
              plenora-beat.service /etc/systemd/system/
       sudo cp plenora-backup.service plenora-backup.timer \\
              plenora-backup-test.service plenora-backup-test.timer \\
              /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now \\
            plenora-backend.service plenora-celery.service \\
            plenora-beat.service \\
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
       systemctl is-active plenora-backend plenora-celery plenora-beat
     should print "active" three times.

If /ready returns 503, inspect:
  journalctl -u plenora-backend -n 100 --no-pager
The four boot gates (validate_startup, validate_storage_config,
init_all_dbs+migrations, _repair_psycopg2_ssl_env) log loud on
failure.
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    log_info "Plenora-ETL guided installer (prototype)"
    log_info "Repo root: ${REPO_ROOT}"
    log_info "Source of truth: docs/ops/install-non-docker.md"

    step_validate_prereqs
    step_gather_config
    step_probe_services
    step_write_env
    step_next_steps

    log_ok "Prototype installer completed."
}

main "$@"
