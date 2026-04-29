#!/bin/bash
# ============================================================
# URBY-ETL Deploy Script
# Uso:
#   bash deploy.sh [--profile=<name>] [--check-only] [backend|frontend|all|check-deps]
#
# Profilo target (Rank1/Step0 batch 1, multi-environment foundation):
#   - default: prod (deploy/profiles/prod.conf)
#   - override env:  PLENORA_DEPLOY_PROFILE=<name>
#   - override CLI:  --profile=<name>
#
# Secret per-operatore (mai nei profili):
#   - PLENORA_DEPLOY_PEM   path al PEM SSH
#
# Modalita' dry-run (post-incident_note_step0_unintended_restart.md):
#   - Flag CLI:  --check-only
#   - Env var:   DRY_RUN=1
# In dry-run lo script:
#   - carica e valida il profilo + required vars
#   - verifica PEM (env + file esistente)
#   - applica branch + working-tree guard per i mode che deployano
#   - stampa cosa farebbe (banner + lista comandi remoti pianificati)
#   - NON esegue: ssh, scp, npm, git push, systemctl, rm sul server
# Exit code 0 se tutti i guard passano; >0 se un guard fallisce.
# ============================================================

set -e
set -u
set -o pipefail

# ------------------------------------------------------------
# Locazioni invarianti (path locali sviluppatore, non per-environment).
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_DIR="${SCRIPT_DIR}/deploy/profiles"
BACKEND_LOCAL="${SCRIPT_DIR}/backend"
FRONTEND_LOCAL="${SCRIPT_DIR}/frontend"

# ------------------------------------------------------------
# Argomenti CLI: parser minimale (--profile=<name> + --check-only + MODE).
# Ordine accettato (i flag possono apparire prima/dopo MODE):
#   bash deploy.sh
#   bash deploy.sh backend
#   bash deploy.sh --profile=prod backend
#   bash deploy.sh backend --profile=prod
#   bash deploy.sh --check-only backend
#   DRY_RUN=1 bash deploy.sh backend
# ------------------------------------------------------------
MODE=""
CLI_PROFILE=""
CLI_CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --profile=*)
            CLI_PROFILE="${arg#--profile=}"
            ;;
        --check-only|--dry-run)
            CLI_CHECK_ONLY=1
            ;;
        backend|frontend|all|check-deps)
            MODE="$arg"
            ;;
        *)
            echo "Argomento non riconosciuto: $arg" >&2
            echo "Uso: bash deploy.sh [--profile=<name>] [--check-only] [backend|frontend|all|check-deps]" >&2
            exit 1
            ;;
    esac
done
MODE="${MODE:-all}"

# Risoluzione dry-run: --check-only / --dry-run  >  env DRY_RUN=1  >  off (0)
if [ "$CLI_CHECK_ONLY" -eq 1 ] || [ "${DRY_RUN:-0}" = "1" ]; then
    DRY_RUN=1
else
    DRY_RUN=0
fi

# Risoluzione profilo: --profile=  >  env PLENORA_DEPLOY_PROFILE  >  prod
PROFILE="${CLI_PROFILE:-${PLENORA_DEPLOY_PROFILE:-prod}}"

# ------------------------------------------------------------
# load_profile — carica deploy/profiles/<PROFILE>.conf, valida required vars,
# applica defaults per opzionali. Fail-fast su mancanze.
# ------------------------------------------------------------
load_profile() {
    PROFILE_FILE="${PROFILES_DIR}/${PROFILE}.conf"
    if [ ! -f "$PROFILE_FILE" ]; then
        echo "ERRORE: profilo '${PROFILE}' non trovato in ${PROFILE_FILE}" >&2
        echo "Profili disponibili:" >&2
        ls -1 "${PROFILES_DIR}"/*.conf 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.conf$//' | sed 's/^/  - /' >&2 || true
        exit 1
    fi

    # shellcheck disable=SC1090
    source "$PROFILE_FILE"

    # Required vars: lo script si rifiuta di partire se mancano.
    local required=(
        SERVER
        REMOTE_BACKEND_PATH
        REMOTE_FRONTEND_PATH
        APP_HEALTH_URL
        APP_READY_URL
        SYSTEMD_UNITS
        ALLOWED_BRANCHES
    )
    local missing=()
    for var in "${required[@]}"; do
        if [ -z "${!var:-}" ]; then
            missing+=("$var")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "ERRORE: profilo '${PROFILE}' incompleto. Variabili obbligatorie mancanti:" >&2
        for v in "${missing[@]}"; do echo "  - $v" >&2; done
        exit 1
    fi

    # Defaults per opzionali.
    READY_TIMEOUT_SEC="${READY_TIMEOUT_SEC:-60}"
    READY_POLL_INTERVAL_SEC="${READY_POLL_INTERVAL_SEC:-2}"
    RETAIN_FRONTEND_VERSIONS="${RETAIN_FRONTEND_VERSIONS:-3}"
}

load_profile

# ------------------------------------------------------------
# PEM SSH: solo via env operatore. Mai nei profili, mai default
# nello script (no path personali nel file versionato).
# ------------------------------------------------------------
# Solo `check-deps` puo' funzionare senza PEM? No: e' SSH read-only ma
# richiede comunque la chiave per autenticarsi. PEM e' obbligatorio per
# qualunque mode che faccia rete sul server.
if [ -z "${PLENORA_DEPLOY_PEM:-}" ]; then
    echo "ERRORE: variabile d'ambiente PLENORA_DEPLOY_PEM non impostata." >&2
    echo "Imposta il path al PEM SSH dell'operatore prima di lanciare deploy.sh:" >&2
    echo "  export PLENORA_DEPLOY_PEM=/path/al/tuo/plenora.pem" >&2
    exit 1
fi
PEM="${PLENORA_DEPLOY_PEM}"
if [ ! -f "$PEM" ]; then
    echo "ERRORE: PEM non trovato sul filesystem locale: ${PEM}" >&2
    echo "Verifica PLENORA_DEPLOY_PEM e che il file esista." >&2
    exit 1
fi

# ------------------------------------------------------------
# Banner audit: cosa stiamo facendo, verso cosa.
# ------------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
    DRY_LABEL=" [DRY-RUN: nessun comando remoto verra' eseguito]"
else
    DRY_LABEL=""
fi
echo "=========================================================="
echo " Deploy Plenora-ETL${DRY_LABEL}"
echo "   profile : ${PROFILE}"
echo "   server  : ${SERVER}"
echo "   PEM     : $(basename "$PEM")"
echo "   mode    : ${MODE}"
echo "=========================================================="

# ------------------------------------------------------------
# run_or_dry — esegue il comando passato come argomenti, oppure (in
# DRY_RUN) lo stampa con prefisso `[DRY-RUN] would run:` e ritorna 0.
# Usato per: ssh, scp, npm run build, git push.
# Per `sleep` dentro polling lasciamo l'esecuzione reale (no side-effect
# remoto) ma il polling stesso non parte in DRY_RUN (vedi
# post_restart_health_gate).
# ------------------------------------------------------------
run_or_dry() {
    if [ "$DRY_RUN" -eq 1 ]; then
        # Stampa il comando in forma quotata leggibile.
        printf '[DRY-RUN] would run:'
        for a in "$@"; do
            printf ' %q' "$a"
        done
        printf '\n'
        return 0
    fi
    "$@"
}

# ------------------------------------------------------------
# Branch guard parametrico: rifiuta deploy da branch fuori allowlist.
# ------------------------------------------------------------
guard_git_state() {
    cd "$BACKEND_LOCAL"
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERRORE: working tree sporco in ${BACKEND_LOCAL}." >&2
        echo "Committa o stash le modifiche prima del deploy." >&2
        exit 1
    fi

    local current_branch
    current_branch="$(git rev-parse --abbrev-ref HEAD)"
    local allowed_match=0
    for b in $ALLOWED_BRANCHES; do
        if [ "$b" = "$current_branch" ]; then
            allowed_match=1
            break
        fi
    done
    if [ "$allowed_match" -ne 1 ]; then
        echo "ERRORE: branch '${current_branch}' non in ALLOWED_BRANCHES='${ALLOWED_BRANCHES}' del profilo '${PROFILE}'." >&2
        echo "Cambia branch oppure aggiorna il profilo se l'ambiente lo permette." >&2
        exit 1
    fi

    CURRENT_BRANCH="$current_branch"
}

# ------------------------------------------------------------
# pre_deploy_check — verifiche read-only sul server target prima del deploy.
# Tutti i check sono SSH read-only: niente mutazione persistente.
# Failure -> exit 1 prima del git pull.
# ------------------------------------------------------------
pre_deploy_check() {
    echo "=== PRE-DEPLOY CHECK (read-only sul server) ==="

    # 1. validate_storage_config — fail-fast se policy/bucket coerenza rotta.
    echo "--- validate_storage_config ---"
    run_or_dry ssh -i "$PEM" "$SERVER" \
        "cd '${REMOTE_BACKEND_PATH}' && venv/bin/python -c 'from app.shared.cloud_backends import validate_storage_config; validate_storage_config(); print(\"validate_storage_config: ok\")'" \
        || { echo "ERRORE: validate_storage_config fallita sul server." >&2; exit 1; }

    # 2. check_deps — riusa la routine esistente (dipendenze sistema).
    check_deps_inner

    # 3. Health baseline — verifica che il servizio attuale sia sano PRIMA
    # di deployare sopra. Se 503/timeout: investigare prima di proseguire.
    echo "--- /health baseline (pre-deploy) ---"
    run_or_dry ssh -i "$PEM" "$SERVER" "curl -fsS --max-time 5 '${APP_HEALTH_URL}'" \
        || { echo "ERRORE: /health baseline NON ok pre-deploy. Investigare prima di deployare." >&2; exit 1; }
    echo ""
    echo ">>> Pre-deploy check: OK"
    echo ""
}

# ------------------------------------------------------------
# post_restart_health_gate — polling /ready dopo restart + is-active sui unit.
# Exit code:
#   1 = ready timeout (servizio non torna 200 entro READY_TIMEOUT_SEC)
#   2 = un systemd unit non e' in stato 'active'
# ------------------------------------------------------------
post_restart_health_gate() {
    echo "=== POST-RESTART HEALTH GATE ==="

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] would poll: ssh ${SERVER} curl ${APP_READY_URL} (timeout=${READY_TIMEOUT_SEC}s, interval=${READY_POLL_INTERVAL_SEC}s)"
        echo "[DRY-RUN] would check: ssh ${SERVER} systemctl is-active ${SYSTEMD_UNITS}"
        echo ">>> Skip in dry-run."
        echo ""
        return 0
    fi

    local elapsed=0
    local ready=0
    local last_response=""
    while [ "$elapsed" -lt "$READY_TIMEOUT_SEC" ]; do
        if last_response="$(ssh -i "$PEM" "$SERVER" "curl -fsS --max-time 5 '${APP_READY_URL}'" 2>&1)"; then
            ready=1
            break
        fi
        sleep "$READY_POLL_INTERVAL_SEC"
        elapsed=$((elapsed + READY_POLL_INTERVAL_SEC))
        echo "  ... /ready non ancora 200 (elapsed=${elapsed}s/${READY_TIMEOUT_SEC}s)"
    done

    if [ "$ready" -ne 1 ]; then
        echo "ERRORE: /ready non ha risposto 200 entro ${READY_TIMEOUT_SEC}s." >&2
        echo "Ultimo output:" >&2
        echo "${last_response}" >&2
        exit 1
    fi
    echo ">>> /ready 200 entro ${elapsed}s"

    # systemctl is-active sui unit del profilo. Quoting + remote check
    # tramite un piccolo loop in remoto, output unit:status atteso 'active'.
    echo "--- systemctl is-active ---"
    local units_check
    units_check="$(ssh -i "$PEM" "$SERVER" "
        for u in ${SYSTEMD_UNITS}; do
            state=\$(systemctl is-active \"\$u\" 2>&1 || true)
            echo \"\$u:\$state\"
        done
    ")"
    echo "$units_check"
    if echo "$units_check" | grep -vE ':active$' >/dev/null 2>&1; then
        echo "ERRORE: almeno un systemd unit non e' in stato 'active'." >&2
        exit 2
    fi
    echo ">>> Tutti i systemd unit sono 'active'"
    echo ""
}

deploy_backend() {
    echo "=== BACKEND: push + deploy ==="

    # NB: working-tree-clean + branch guard sono gia' stati applicati
    # globalmente (vedi case dispatch in coda al file) per qualunque mode
    # che faccia deploy. Qui ci limitiamo al pre-deploy check + flusso.

    # 1. Pre-deploy check (read-only sul server)
    pre_deploy_check

    # 2. Push del branch corrente (no auto-commit: rifiutiamo working tree sporco a monte).
    run_or_dry git push origin "$CURRENT_BRANCH" || { echo "ERRORE: push fallito" >&2; exit 1; }
    if [ "$DRY_RUN" -ne 1 ]; then
        echo ">>> Pushato su GitHub (branch: ${CURRENT_BRANCH})"
    fi

    # 3. Pull sul server + sync dipendenze venv + restart
    # NB: pip install e' idempotente — quando requirements.txt non cambia,
    # finisce in ~1s con "Requirement already satisfied" per ogni pacchetto.
    # Fail-fast: se pip fallisce (dipendenza rotta) i servizi NON vengono
    # riavviati — preferiamo lasciare il vecchio vivo piuttosto che un
    # restart con import che esplode.
    run_or_dry ssh -i "$PEM" "$SERVER" \
        "cd '${REMOTE_BACKEND_PATH}' && git pull origin '${CURRENT_BRANCH}' && venv/bin/pip install -q -r requirements.txt && sudo systemctl restart ${SYSTEMD_UNITS}"
    if [ "$DRY_RUN" -ne 1 ]; then
        echo ">>> Server aggiornato (venv sincronizzato) e riavviato"
    fi

    # 4. Post-restart health gate (polling /ready + is-active).
    post_restart_health_gate

    echo ""
}

deploy_frontend() {
    echo "=== FRONTEND: build + upload ==="

    cd "$FRONTEND_LOCAL" || exit 1

    # 1. Build produzione
    run_or_dry npm run build || { echo "ERRORE: build fallito" >&2; exit 1; }
    if [ "$DRY_RUN" -ne 1 ]; then
        echo ">>> Build completato"
    fi

    # 2. Svuota la dist remota prima di caricare la nuova.
    # Senza questa pulizia, scp -r accumula i bundle vecchi (ogni build Vite
    # ha nuovi hash nei nomi) e la dir cresce indefinitamente con file morti.
    # NB: pre-swap distruttivo, sara' sostituito da symlink swap atomico in
    # batch 2 (P4). Vedi rank1_step0_batch1_patch_plan.md §6 out-of-scope.
    run_or_dry ssh -i "$PEM" "$SERVER" "rm -rf '${REMOTE_FRONTEND_PATH}/dist'/* '${REMOTE_FRONTEND_PATH}/dist'/.[!.]*" \
        || { echo "ERRORE: pulizia dist remota fallita" >&2; exit 1; }
    if [ "$DRY_RUN" -ne 1 ]; then
        echo ">>> Dist remota pulita"
    fi

    # 3. Upload dist
    if [ "$DRY_RUN" -eq 1 ]; then
        # In dry-run la dist locale potrebbe non esistere (no npm build).
        # Stampiamo solo cosa faremmo, senza richiedere file presenti.
        echo "[DRY-RUN] would run: scp -i \$PEM -r dist/* ${SERVER}:${REMOTE_FRONTEND_PATH}/dist/"
    else
        scp -i "$PEM" -r dist/* "${SERVER}:${REMOTE_FRONTEND_PATH}/dist/" \
            || { echo "ERRORE: upload fallito" >&2; exit 1; }
        echo ">>> Frontend deployato"
    fi
    echo ""
}

# Inner: la routine SSH usata sia da `check-deps` standalone sia da
# `pre_deploy_check`. Stesso comportamento, refactor minimale per riuso.
check_deps_inner() {
    echo "--- CHECK DEPS sul server ---"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] would run: ssh ${SERVER} (probe libmagic1 / python-magic / libgdal)"
        echo ""
        return 0
    fi
    # Probe non-distruttivo per dipendenze sistema critiche.
    ssh -i "$PEM" "$SERVER" '
        echo "--- libmagic1 (python-magic) ---"
        dpkg -l libmagic1 2>/dev/null | tail -1 || echo "NON INSTALLATO"
        echo ""
        echo "--- python-magic (venv) ---"
        '"${REMOTE_BACKEND_PATH}"'/venv/bin/python -c "import magic; print(\"OK:\", magic.__version__ if hasattr(magic, \"__version__\") else \"installed\")" 2>&1 || echo "NON INSTALLATO nel venv"
        echo ""
        echo "--- libgdal (geopandas/fiona) ---"
        dpkg -l libgdal-dev libgdal34t64 2>/dev/null | grep -E "^ii" | head -3 || echo "non rilevato (geopandas potrebbe usare wheels statiche)"
    '
    echo ""
}

check_deps() {
    echo "=== CHECK DEPS sul server ==="
    check_deps_inner
}

# ------------------------------------------------------------
# Guard comune ai mode che fanno deploy (backend/frontend/all).
# `check-deps` resta read-only: niente push, niente restart, niente swap
# frontend -> non richiede branch/working-tree guard.
# ------------------------------------------------------------
case "$MODE" in
    backend|frontend|all)
        guard_git_state
        ;;
esac

case "$MODE" in
    backend)    deploy_backend ;;
    frontend)   deploy_frontend ;;
    all)        deploy_backend; deploy_frontend ;;
    check-deps) check_deps ;;
    *)          echo "Uso: bash deploy.sh [--profile=<name>] [--check-only] [backend|frontend|all|check-deps]" >&2; exit 1 ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
    echo "=== DRY-RUN COMPLETATO (nessun comando remoto eseguito) ==="
else
    echo "=== DEPLOY COMPLETATO ==="
fi
