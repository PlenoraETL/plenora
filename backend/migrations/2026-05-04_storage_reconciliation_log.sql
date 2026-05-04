-- BE-GO-READY-STORAGE-ENGINE-005-RECONCILIATION-LOG-FOUNDATION
--
-- Persistent substrate for storage compensating-delete reconciliation.
-- Tracks intent ("delete this storage_key") + outcome (done|failed)
-- across the W1 race window: post-commit DB delete -> process death
-- -> blob orphaned. ADR-0011 posture preserved (storage failure
-- accepted, NOT propagated to user) but now persisted, so a future
-- reconciler beat hook (batch 006) can act on it.
--
-- This migration only adds the substrate. The reconciler beat hook,
-- the storage-side scan, and the upload-intent pattern (W2 race) are
-- separate batches per BE_STORAGE_ENGINE_AUDIT.md §4.7 roadmap.
--
-- Idempotente: tutti gli statement IF NOT EXISTS, re-run safe.
-- Schema-qualified `plenora.<table>` come gli altri migration files.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS plenora.storage_reconciliation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain          TEXT NOT NULL,
    storage_key     TEXT NOT NULL,
    operation       TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    reason          TEXT NOT NULL,
    extras          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts        INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_retry_at   TIMESTAMPTZ,
    CONSTRAINT storage_reconciliation_log_state_chk
        CHECK (state IN ('pending', 'done', 'orphan_deleted', 'lost'))
);

-- Pending-only partial index per il futuro reconciler:
-- ORDER BY next_retry_at NULLS FIRST per pickup ordinato.
CREATE INDEX IF NOT EXISTS idx_storage_recon_pending
    ON plenora.storage_reconciliation_log (state, next_retry_at)
    WHERE state = 'pending';

-- Lookup per storage_key per investigations / dedup manuale.
CREATE INDEX IF NOT EXISTS idx_storage_recon_storage_key
    ON plenora.storage_reconciliation_log (storage_key);
