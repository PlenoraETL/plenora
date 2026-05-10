#!/bin/sh
# Sidecar che crea i bucket di default su MinIO una volta che è healthy.
# Idempotente: se i bucket esistono già, non rifa nulla.
set -eu

ALIAS="local"
ENDPOINT="http://minio:9000"
USER="${MINIO_ROOT_USER:-plenora_minio}"
PASS="${MINIO_ROOT_PASSWORD:-plenora_minio_pwd}"
BUCKETS="${MINIO_DEFAULT_BUCKETS:-plenora-test,plenora-artifacts}"

echo "[plenora-e2e] Configuring mc alias..."
mc alias set "${ALIAS}" "${ENDPOINT}" "${USER}" "${PASS}"

echo "[plenora-e2e] Creating buckets: ${BUCKETS}"
OLDIFS=$IFS
IFS=','
FIRST_BUCKET=""
for bucket in ${BUCKETS}; do
    bucket=$(echo "${bucket}" | tr -d ' ')
    [ -z "${FIRST_BUCKET}" ] && FIRST_BUCKET="${bucket}"
    if mc ls "${ALIAS}/${bucket}" >/dev/null 2>&1; then
        echo "[plenora-e2e]   - ${bucket} (exists)"
    else
        mc mb "${ALIAS}/${bucket}"
        echo "[plenora-e2e]   - ${bucket} (created)"
    fi
done
IFS=$OLDIFS

# Pre-popola il primo bucket coi fixture di test-e2e/files/ (idempotente).
if [ -n "${FIRST_BUCKET}" ] && [ -d /fixtures ]; then
    if mc ls "${ALIAS}/${FIRST_BUCKET}/fixtures/" >/dev/null 2>&1; then
        echo "[plenora-e2e] fixtures già caricate in ${FIRST_BUCKET}, skip."
    else
        echo "[plenora-e2e] Loading fixtures into ${FIRST_BUCKET}/fixtures/..."
        mc cp --recursive /fixtures/ "${ALIAS}/${FIRST_BUCKET}/fixtures/"
        echo "[plenora-e2e] fixtures loaded."
    fi
fi

echo "[plenora-e2e] MinIO ready."
