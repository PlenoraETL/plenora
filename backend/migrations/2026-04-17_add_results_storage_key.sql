-- FS-4: large results (>= RESULT_S3_THRESHOLD_BYTES) off-loaded su S3.
-- La riga DB tiene metadata + storage_key (S3 key), `data` BYTEA resta
-- NULL per questi record. Per file piccoli `data` e' popolato come prima
-- e storage_key resta NULL (backward compat).
--
-- Il reader get_result_data controlla storage_key prima di leggere BYTEA:
--  - storage_key NULL  -> ritorna data BYTEA
--  - storage_key SET   -> download da S3 via cloud_backends
--
-- Idempotente: ADD COLUMN IF NOT EXISTS (Postgres 9.6+).

ALTER TABLE plenora.results
  ADD COLUMN IF NOT EXISTS storage_key TEXT;

-- Indice parziale per trovare velocemente i record S3-backed (utile
-- per retention cleanup eager su S3 quando si droppa una row).
CREATE INDEX IF NOT EXISTS idx_results_storage_key_notnull
  ON plenora.results (storage_key)
  WHERE storage_key IS NOT NULL;
