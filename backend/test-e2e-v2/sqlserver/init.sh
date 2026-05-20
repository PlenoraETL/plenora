#!/bin/bash
# Sidecar che inizializza il DB SQL Server una volta che sqlserver è (healthy).
# SQL Server non supporta initdb.d, quindi l'init avviene da qui.
# Idempotente: se il DB esiste già e ha già le tabelle, salta.
set -euo pipefail

SQLCMD=""
for candidate in /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
    if [ -x "${candidate}" ]; then
        SQLCMD="${candidate}"
        break
    fi
done
if [ -z "${SQLCMD}" ]; then
    echo "[plenora-e2e] sqlcmd non trovato" >&2
    exit 1
fi

# tools18 vuole -C (trust cert); tools15 no.
if echo "${SQLCMD}" | grep -q tools18; then
    SQLCMD_ARGS="-C"
else
    SQLCMD_ARGS=""
fi

SERVER="sqlserver"
PASS="${MSSQL_SA_PASSWORD}"
DB="${MSSQL_DATABASE:-plenora_test}"
SEED_DIR="/seed"

echo "[plenora-e2e] Waiting for SQL Server to accept connections..."
for i in $(seq 1 30); do
    if ${SQLCMD} ${SQLCMD_ARGS} -S "${SERVER}" -U sa -P "${PASS}" -Q "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

echo "[plenora-e2e] Ensuring database ${DB} exists..."
${SQLCMD} ${SQLCMD_ARGS} -S "${SERVER}" -U sa -P "${PASS}" -Q "
IF DB_ID('${DB}') IS NULL CREATE DATABASE [${DB}];
"

# Skip se già seedato (presenza tabella regions).
ALREADY=$(${SQLCMD} ${SQLCMD_ARGS} -S "${SERVER}" -U sa -P "${PASS}" -d "${DB}" -h-1 -W -Q "
SET NOCOUNT ON;
SELECT COUNT(*) FROM sys.tables WHERE name = 'regions';
" | tr -d '[:space:]')

if [ "${ALREADY}" = "1" ] && [ "${PLENORA_E2E_RESEED:-0}" != "1" ]; then
    echo "[plenora-e2e] DB già seedato, skip."
    exit 0
fi

echo "[plenora-e2e] Loading SQL seed into ${DB}..."
for sql_file in 00_schema.sql 01_seed.sql 02_volume.sql; do
    path="${SEED_DIR}/${sql_file}"
    if [ -f "${path}" ]; then
        echo "[plenora-e2e]   - ${sql_file}"
        ${SQLCMD} ${SQLCMD_ARGS} -S "${SERVER}" -U sa -P "${PASS}" -d "${DB}" -i "${path}"
    else
        echo "[plenora-e2e]   ! missing ${path}, skipped"
    fi
done

echo "[plenora-e2e] Seed loaded."
