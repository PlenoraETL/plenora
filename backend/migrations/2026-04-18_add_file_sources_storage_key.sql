-- FS-B1-large follow-up: dispatch threshold per file_sources (analog FS-4 results).
--
-- Motivazione: oggi TUTTI i file_source vivono in `plenora.file_sources.data`
-- BYTEA. Un cliente che carica shapefile 500MB zippati gonfia indefinitamente
-- il DB (il pg_dump schema-only cresce linearmente, RAM spike su read).
--
-- Fix: threshold `FILE_SOURCE_S3_THRESHOLD_BYTES` (default 50MB). Sotto il
-- threshold resta tutto come prima (BYTEA, transazionale). Sopra il threshold
-- il blob va su S3 `file-sources/{file_id}/{original_filename}` e la row DB
-- tiene solo metadata + `storage_key`.
--
-- Pattern identico a FS-4 results (`plenora.results.storage_key`). Il reader
-- `file_service._temp_file_for` controlla `storage_key` prima di leggere
-- BYTEA: se set → download da S3 via cloud_backends, se NULL → legge `data`.
--
-- Idempotente: ADD COLUMN IF NOT EXISTS + ALTER COLUMN DROP NOT NULL con
-- check preventivo (Postgres 13+ non solleva se e' gia' nullable).

ALTER TABLE plenora.file_sources
  ADD COLUMN IF NOT EXISTS storage_key TEXT;

-- data deve essere NULL-able quando il blob e' su S3 (BYTEA NULL = sentinel
-- "cerca su storage_key"). Postgres non ha "IF NULLABLE" ma DROP NOT NULL
-- e' no-op se e' gia' nullable.
ALTER TABLE plenora.file_sources
  ALTER COLUMN data DROP NOT NULL;

-- Indice parziale per cleanup eager S3 (trova rapidamente file S3-backed
-- quando si droppa una row).
CREATE INDEX IF NOT EXISTS idx_file_sources_storage_key_notnull
  ON plenora.file_sources (storage_key)
  WHERE storage_key IS NOT NULL;
