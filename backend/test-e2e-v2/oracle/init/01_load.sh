#!/bin/bash
# Carica schema + seed + volume nel PDB Oracle come APP_USER.
# Gli script .sql dell'immagine Oracle girano come utente amministrativo;
# questo wrapper forza invece la connessione allo schema applicativo.
set -euo pipefail

SEED_DIR="/seed"
DB="${ORACLE_DATABASE:-plenora_test}"
USER_NAME="${APP_USER:-plenora}"
USER_PASSWORD="${APP_USER_PASSWORD:-plenora_test_pwd}"

echo "[plenora-e2e] Waiting for Oracle app user on ${DB}..."
for _ in {1..120}; do
    if echo "select 1 from dual;" | sqlplus -s "${USER_NAME}/${USER_PASSWORD}@localhost:1521/${DB}" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

echo "[plenora-e2e] Loading Oracle SQL seed into ${USER_NAME}@${DB}..."
for sql_file in 00_schema.sql 01_seed.sql 02_volume.sql; do
    path="${SEED_DIR}/${sql_file}"
    if [ -f "${path}" ]; then
        echo "[plenora-e2e]   - ${sql_file}"
        sqlplus -s "${USER_NAME}/${USER_PASSWORD}@localhost:1521/${DB}" @"${path}"
    else
        echo "[plenora-e2e]   ! missing ${path}, skipped"
    fi
done

echo "[plenora-e2e] Oracle seed loaded."
