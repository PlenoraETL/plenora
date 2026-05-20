#!/bin/sh
# Carica schema + seed + volume nel DB di default (POSTGRES_DB).
# Eseguito da `docker-entrypoint.sh` come parte di initdb.d, una sola volta
# al primo avvio (volume vuoto). Per ricaricare: `docker compose down -v`.
set -eu

SEED_DIR="/seed"
DB="${POSTGRES_DB:-plenora_test}"
USER="${POSTGRES_USER:-plenora}"

echo "[plenora-e2e] Loading SQL seed into ${DB}..."

for sql_file in 00_schema.sql 01_seed.sql 02_volume.sql; do
    path="${SEED_DIR}/${sql_file}"
    if [ -f "${path}" ]; then
        echo "[plenora-e2e]   - ${sql_file}"
        psql -v ON_ERROR_STOP=1 -U "${USER}" -d "${DB}" -f "${path}"
    else
        echo "[plenora-e2e]   ! missing ${path}, skipped"
    fi
done

echo "[plenora-e2e] Seed loaded."
