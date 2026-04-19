#!/bin/bash
# ============================================================
# URBY-ETL Deploy Script
# Uso: bash deploy.sh [backend|frontend|all]
# ============================================================

PEM="C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem"
SERVER="ubuntu@15.160.219.93"
BACKEND_LOCAL="C:/Users/Marco/Desktop/int_efm/URBY-ETL/backend"
FRONTEND_LOCAL="C:/Users/Marco/Desktop/int_efm/URBY-ETL/frontend"

MODE="${1:-all}"

deploy_backend() {
    echo "=== BACKEND: commit + push + deploy ==="

    # 1. Commit e push locale
    cd "$BACKEND_LOCAL" || exit 1

    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        git commit -m "deploy: $(date '+%Y-%m-%d %H:%M')"
        echo ">>> Committato"
    else
        echo ">>> Nessuna modifica locale"
    fi

    git push origin main || { echo "ERRORE: push fallito"; exit 1; }
    echo ">>> Pushato su GitHub"

    # 2. Pull sul server + sync dipendenze venv + restart
    # NB: pip install e' idempotente — quando requirements.txt non cambia,
    # finisce in ~1s con "Requirement already satisfied" per ogni pacchetto.
    # Fail-fast: se pip fallisce (dipendenza rotta) i servizi NON vengono
    # riavviati — preferiamo lasciare il vecchio vivo piuttosto che un
    # restart con import che esplode.
    ssh -i "$PEM" "$SERVER" "
        cd /home/ubuntu/plenora-etl/backend &&
        git pull origin main &&
        venv/bin/pip install -q -r requirements.txt &&
        sudo systemctl restart plenora-backend plenora-celery plenora-beat
    "
    echo ">>> Server aggiornato (venv sincronizzato) e riavviato"
    echo ""
}

deploy_frontend() {
    echo "=== FRONTEND: build + upload ==="

    cd "$FRONTEND_LOCAL" || exit 1

    # 1. Build produzione
    npm run build || { echo "ERRORE: build fallito"; exit 1; }
    echo ">>> Build completato"

    # 2. Svuota la dist remota prima di caricare la nuova.
    # Senza questa pulizia, scp -r accumula i bundle vecchi (ogni build Vite
    # ha nuovi hash nei nomi) e la dir cresce indefinitamente con file morti.
    ssh -i "$PEM" "$SERVER" "rm -rf /home/ubuntu/plenora-etl/frontend/dist/* /home/ubuntu/plenora-etl/frontend/dist/.[!.]*" \
        || { echo "ERRORE: pulizia dist remota fallita"; exit 1; }
    echo ">>> Dist remota pulita"

    # 3. Upload dist
    scp -i "$PEM" -r dist/* "$SERVER:/home/ubuntu/plenora-etl/frontend/dist/" \
        || { echo "ERRORE: upload fallito"; exit 1; }
    echo ">>> Frontend deployato"
    echo ""
}

check_deps() {
    echo "=== CHECK DEPS sul server ==="
    # Probe non-distruttivo per dipendenze sistema critiche.
    # Aggiungere qui ogni nuova lib di sistema (libmagic1, libgdal, ecc.)
    # che il backend usa via import — cosi' il check resta scriptabile
    # e auditabile invece di SSH ad-hoc fuori da deploy.sh.
    ssh -i "$PEM" "$SERVER" '
        echo "--- libmagic1 (python-magic) ---"
        dpkg -l libmagic1 2>/dev/null | tail -1 || echo "NON INSTALLATO"
        echo ""
        echo "--- python-magic (venv) ---"
        /home/ubuntu/plenora-etl/backend/venv/bin/python -c "import magic; print(\"OK:\", magic.__version__ if hasattr(magic, \"__version__\") else \"installed\")" 2>&1 || echo "NON INSTALLATO nel venv"
        echo ""
        echo "--- libgdal (geopandas/fiona) ---"
        dpkg -l libgdal-dev libgdal34t64 2>/dev/null | grep -E "^ii" | head -3 || echo "non rilevato (geopandas potrebbe usare wheels statiche)"
    '
    echo ""
}

case "$MODE" in
    backend)    deploy_backend ;;
    frontend)   deploy_frontend ;;
    all)        deploy_backend; deploy_frontend ;;
    check-deps) check_deps ;;
    *)          echo "Uso: bash deploy.sh [backend|frontend|all|check-deps]" ;;
esac

echo "=== DEPLOY COMPLETATO ==="
