-- Phase 6 product-grade Batch 1 (2026-04-28): nuove colonne node_meta per
-- separare "persistenza righe" da "change detection".
--
-- Tutte additive e nullable -> record legacy (NULL) sono trattati come
-- legacy `full_pg` dai reader (vedi
-- app/core/intermediate/policy.persistence_mode_for_record).
--
-- Significato colonne:
--   node_hash             fingerprint dell'intero DataFrame del nodo
--   hash_algo             versione algoritmo (es. "pandas_hash_v1")
--   persistence_mode      mode con cui questo record e' stato scritto
--                         ("metadata_only" | "preview_only" | "full_pg" |
--                          "full_artifact" -- ultimo riservato Batch 5)
--   persisted_rows_count  righe effettivamente in node_rows per questo nodo
--   preview_truncated     true se persisted_rows_count < row_count
--   artifact_uri          URI dell'artifact full su object storage
--                         (NULL fino a Batch 5)
--   artifact_format       formato dell'artifact (es. "parquet")
--   artifact_status       "none" | "pending" | "complete" | "failed"
--   artifact_error        messaggio errore se artifact_status="failed"
--
-- Rollback semplice: nessuna constraint NOT NULL, nessun default critico.
-- DROP COLUMN possibile senza perdere dati legacy.

ALTER TABLE node_meta
    ADD COLUMN IF NOT EXISTS node_hash             TEXT,
    ADD COLUMN IF NOT EXISTS hash_algo             TEXT,
    ADD COLUMN IF NOT EXISTS persistence_mode      TEXT,
    ADD COLUMN IF NOT EXISTS persisted_rows_count  INTEGER,
    ADD COLUMN IF NOT EXISTS preview_truncated     BOOLEAN,
    ADD COLUMN IF NOT EXISTS artifact_uri          TEXT,
    ADD COLUMN IF NOT EXISTS artifact_format       TEXT,
    ADD COLUMN IF NOT EXISTS artifact_status       TEXT,
    ADD COLUMN IF NOT EXISTS artifact_error        TEXT;
