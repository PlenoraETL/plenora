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

    # 2. Pull sul server + restart
    ssh -i "$PEM" "$SERVER" "
        cd /home/ubuntu/urby-etl/backend &&
        git pull origin main &&
        sudo systemctl restart urby-backend urby-celery urby-beat
    "
    echo ">>> Server aggiornato e riavviato"
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
    ssh -i "$PEM" "$SERVER" "rm -rf /home/ubuntu/urby-etl/frontend/dist/* /home/ubuntu/urby-etl/frontend/dist/.[!.]*" \
        || { echo "ERRORE: pulizia dist remota fallita"; exit 1; }
    echo ">>> Dist remota pulita"

    # 3. Upload dist
    scp -i "$PEM" -r dist/* "$SERVER:/home/ubuntu/urby-etl/frontend/dist/" \
        || { echo "ERRORE: upload fallito"; exit 1; }
    echo ">>> Frontend deployato"
    echo ""
}

case "$MODE" in
    backend)  deploy_backend ;;
    frontend) deploy_frontend ;;
    all)      deploy_backend; deploy_frontend ;;
    *)        echo "Uso: bash deploy.sh [backend|frontend|all]" ;;
esac

echo "=== DEPLOY COMPLETATO ==="
