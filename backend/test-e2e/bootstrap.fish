#!/usr/bin/env fish
# Plenora E2E — bootstrap operativo dei prerequisiti.
#
# Cosa fa (tutto idempotente, sicuro da rilanciare):
#   1. Crea la docker network condivisa `plenora-e2e`
#   2. Verifica che il compose root abbia la network plenora-e2e (WARN se manca)
#   3. Verifica che il compose root e quello e2e siano sintatticamente validi
#   4. Crea il venv del runner pytest (test-e2e/runner/.venv)
#   5. pip install -r requirements.txt nel venv
#   6. Genera lo script SQL per il seed dell'utente test (NON lo esegue)
#   7. Stampa i comandi successivi (avvio container + lancio pytest)
#
# Cosa NON fa (richiede decisione/azione esplicita):
#   - `docker compose up` di nessuno dei due stack
#   - patch del root compose (l'utente la sta evolvendo)
#   - INSERT dell'utente test nel DB applicativo plenora
#   - `pytest` di alcun test
#
# Uso:
#     ./test-e2e/bootstrap.fish
#     ./test-e2e/bootstrap.fish --skip-venv     # salta install runner deps
#     ./test-e2e/bootstrap.fish --check-only    # solo verifiche, niente write

set -l SCRIPT_DIR (status dirname)
set -l REPO_ROOT (cd $SCRIPT_DIR/../.. && pwd)
set -l E2E_DIR $REPO_ROOT/backend/test-e2e
set -l RUNNER_DIR $E2E_DIR/runner
set -l ROOT_COMPOSE $REPO_ROOT/docker-compose.yml
set -l E2E_COMPOSE $E2E_DIR/docker/docker-compose.yml
set -l NETWORK_NAME plenora-e2e

set -l skip_venv 0
set -l check_only 0
for arg in $argv
    switch $arg
        case --skip-venv
            set skip_venv 1
        case --check-only
            set check_only 1
        case '*'
            echo "Argomento sconosciuto: $arg" >&2
            exit 2
    end
end

function _step
    echo ""
    echo "── $argv[1] ─────────────────────────────────────────────"
end

function _ok
    echo "  ✓ $argv[1]"
end

function _warn
    echo "  ⚠ $argv[1]"
end

function _err
    echo "  ✗ $argv[1]" >&2
end

# ── 1. Docker network ───────────────────────────────────────────────────
_step "1. Docker network `$NETWORK_NAME`"

if docker network inspect $NETWORK_NAME >/dev/null 2>&1
    _ok "Network già esistente"
else
    if test $check_only -eq 1
        _warn "Mancante. Da creare: docker network create $NETWORK_NAME"
    else
        echo "  → docker network create $NETWORK_NAME"
        if docker network create $NETWORK_NAME >/dev/null
            _ok "Creata"
        else
            _err "Creazione fallita"
            exit 1
        end
    end
end

# ── 2. Root compose ha la network plenora-e2e? ─────────────────────────
_step "2. Root compose `$ROOT_COMPOSE`"

if not test -f $ROOT_COMPOSE
    _err "Root compose non trovato: $ROOT_COMPOSE"
    exit 1
end

if grep -qE 'plenora-e2e' $ROOT_COMPOSE
    _ok "Riferimento a `plenora-e2e` presente nel root compose"
else
    _warn "Root compose NON ha la network `plenora-e2e`."
    echo "    Senza questa patch, l'app plenora non vede plenora-e2e-postgres / fake-rest / ecc."
    echo ""
    echo "    Patch suggerita (in coda al root compose):"
    echo ""
    echo "      networks:"
    echo "        default:"
    echo "        plenora-e2e:"
    echo "          external: true"
    echo ""
    echo "    + su OGNI servizio (postgres, redis, backend, worker, beat, frontend):"
    echo "        networks:"
    echo "          - default"
    echo "          - plenora-e2e"
end

# ── 3. Validazione sintattica dei due compose ───────────────────────────
_step "3. Validazione sintattica compose"

if docker compose -f $ROOT_COMPOSE config -q 2>/dev/null
    _ok "Root compose OK"
else
    _warn "Root compose ha errori (`docker compose config` exit ≠ 0)"
end

if docker compose -f $E2E_COMPOSE --profile full config -q 2>/dev/null
    _ok "E2E compose (profile=full) OK"
else
    _warn "E2E compose ha errori"
end

# ── 4. Venv del runner ──────────────────────────────────────────────────
_step "4. Runner venv ($RUNNER_DIR/.venv)"

if test $skip_venv -eq 1
    _warn "Saltato (--skip-venv)"
else if test $check_only -eq 1
    if test -d $RUNNER_DIR/.venv
        _ok "Venv già presente"
    else
        _warn "Venv mancante"
    end
else
    if not test -d $RUNNER_DIR/.venv
        echo "  → python3 -m venv .venv"
        python3 -m venv $RUNNER_DIR/.venv
        _ok "Venv creato"
    else
        _ok "Venv già presente"
    end

    set -l PIP $RUNNER_DIR/.venv/bin/pip
    if test -x $PIP
        echo "  → pip install -r requirements.txt"
        $PIP install --quiet --upgrade pip
        $PIP install --quiet -r $RUNNER_DIR/requirements.txt
        _ok "Dipendenze installate"
    else
        _err "pip non trovato in $RUNNER_DIR/.venv/bin/"
    end
end

# ── 5. Seed utente test SQL ─────────────────────────────────────────────
_step "5. SQL seed utente test"

set -l SEED_SQL $E2E_DIR/scripts/seed_test_user.sql
if test -f $SEED_SQL
    _ok "Script seed presente: $SEED_SQL"
else
    _warn "Script seed assente. Va creato manualmente."
end
echo "  Per applicarlo (quando l'app stack è up):"
echo "    docker exec -i plenora-postgres-1 psql -U plenora -d plenora < $SEED_SQL"

# ── 6. Riepilogo + comandi successivi ───────────────────────────────────
_step "6. Prossimi passi (eseguire manualmente)"

printf '%s\n' \
    "  a) Avvia stack app plenora:" \
    "       cd $REPO_ROOT" \
    "       docker compose up -d" \
    "" \
    "  b) Avvia stack fixture e2e:" \
    "       cd $E2E_DIR/docker" \
    "       docker compose up -d            # profilo \"core\" (default)" \
    "       # oppure: docker compose --profile full up -d   per tutto" \
    "" \
    "  c) Verifica healthcheck:" \
    "       docker compose -f $E2E_COMPOSE ps" \
    "" \
    "  d) Crea l'utente test (una volta):" \
    "       docker exec -i plenora-postgres-1 psql -U plenora -d plenora < $SEED_SQL" \
    "" \
    "  e) Lancia i 6 contract test:" \
    "       cd $RUNNER_DIR" \
    "       source .venv/bin/activate.fish" \
    "       pytest -m contract -v" \
    "" \
    "  f) Se i contract sono verdi → 50 must:" \
    "       pytest -m \"smoke or (profile_core and requires_app)\" -v" \
    "" \
    "  g) Tear-down (quando hai finito):" \
    "       cd $E2E_DIR/docker  &&  docker compose down" \
    "       cd $REPO_ROOT       &&  docker compose down"

echo ""
echo "Bootstrap completato (no container avviato, no test eseguito)."
