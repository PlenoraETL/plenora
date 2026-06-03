#!/usr/bin/env bash
# Setup di un PostgreSQL LOCALE dentro il container test (plenora-tests2-bind),
# cosi' i test che fanno I/O DB reale (contract/integration) girino verdi invece
# di fallire/hangare su "connection refused 127.0.0.1:5432".
#
# I test di tests2 usano il default di conftest.py:
#   DATABASE_URL = postgresql://test:test@127.0.0.1:5432/test   (DB_SCHEMA=plenora)
# quindi qui creiamo esattamente role/db `test` su 127.0.0.1:5432 e applichiamo
# tutte le migration (migrations_runner crea lo schema `plenora` + le tabelle).
#
# Idempotente: rieseguibile dopo ogni `docker start` del container (il cluster
# non riparte da solo: niente systemd). Eseguire COME ROOT dentro il container:
#   docker exec -u root plenora-tests2-bind bash /repo/backend/scripts/setup_test_postgres.sh
set -euo pipefail

PG_PORT="${PG_PORT:-5432}"
DB_USER="${TEST_DB_USER:-test}"
DB_PASS="${TEST_DB_PASS:-test}"
DB_NAME="${TEST_DB_NAME:-test}"
DB_SCHEMA="${DB_SCHEMA:-plenora}"
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${PG_PORT}/${DB_NAME}"

# 1) Installa il server se manca (solo client psql preinstallato nell'immagine).
if ! ls /usr/lib/postgresql/*/bin/pg_ctl >/dev/null 2>&1; then
  echo "[setup-pg] installo postgresql..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
fi

# 2) Avvia il cluster di default se down.
CLUSTER_LINE="$(pg_lsclusters -h | head -1)"
PG_VER="$(echo "$CLUSTER_LINE" | awk '{print $1}')"
PG_CLUSTER="$(echo "$CLUSTER_LINE" | awk '{print $2}')"
if ! echo "$CLUSTER_LINE" | grep -q online; then
  echo "[setup-pg] avvio cluster ${PG_VER}/${PG_CLUSTER}..."
  pg_ctlcluster "$PG_VER" "$PG_CLUSTER" start
fi

# 3) Role + DB `test` (idempotente).
su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\"" | grep -q 1 \
  || su postgres -c "psql -c \"CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}' SUPERUSER\""
su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'\"" | grep -q 1 \
  || su postgres -c "createdb -O ${DB_USER} ${DB_NAME}"

# 4) Applica le migration (crea schema ${DB_SCHEMA} + tabelle, idempotente).
echo "[setup-pg] applico migration su ${DATABASE_URL} (schema=${DB_SCHEMA})..."
cd /repo/backend
DATABASE_URL="$DATABASE_URL" DB_SCHEMA="$DB_SCHEMA" \
  python -c "from app.db.migrations_runner import apply_pending_migrations, list_applied_migrations; apply_pending_migrations(); print('[setup-pg] migration totali applicate:', len(list_applied_migrations()))"

echo "[setup-pg] OK — Postgres test pronto su 127.0.0.1:${PG_PORT} (db=${DB_NAME}, schema=${DB_SCHEMA})."
