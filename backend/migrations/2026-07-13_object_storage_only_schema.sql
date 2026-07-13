-- Final object-storage-only cutover.
-- PostgreSQL remains the catalog/control plane; user payloads live only in
-- S3-compatible object storage.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM results WHERE data IS NOT NULL) THEN
        RAISE EXCEPTION 'results.data contiene ancora payload: eseguire migrate_payloads_to_object_storage.py --apply';
    END IF;
    IF EXISTS (SELECT 1 FROM results WHERE storage_key IS NULL) THEN
        RAISE EXCEPTION 'results contiene righe senza storage_key';
    END IF;
    IF EXISTS (SELECT 1 FROM file_sources WHERE data IS NOT NULL) THEN
        RAISE EXCEPTION 'file_sources.data contiene ancora payload: eseguire migrate_payloads_to_object_storage.py --apply';
    END IF;
    IF EXISTS (SELECT 1 FROM file_sources WHERE storage_key IS NULL) THEN
        RAISE EXCEPTION 'file_sources contiene righe senza storage_key';
    END IF;
    IF EXISTS (SELECT 1 FROM task_results WHERE jsonb_exists(payload, 'rows')) THEN
        RAISE EXCEPTION 'task_results contiene ancora preview tabulari inline';
    END IF;
    IF EXISTS (SELECT 1 FROM node_rows) THEN
        RAISE EXCEPTION 'node_rows contiene ancora payload intermedi';
    END IF;
END
$$;

-- Le installazioni precedenti non valorizzavano persistence_mode. Poiche'
-- node_rows e' vuota, questi record descrivono ormai soltanto metadata.
UPDATE node_meta
SET persistence_mode = 'metadata_only',
    persisted_rows_count = 0,
    preview_truncated = false
WHERE persistence_mode IS NULL
   OR persistence_mode IN ('preview_only', 'full_pg');

ALTER TABLE results DROP CONSTRAINT IF EXISTS results_payload_external_only;
ALTER TABLE file_sources DROP CONSTRAINT IF EXISTS file_sources_payload_external_only;

ALTER TABLE results ALTER COLUMN storage_key SET NOT NULL;
ALTER TABLE file_sources ALTER COLUMN storage_key SET NOT NULL;

ALTER TABLE results DROP COLUMN data;
ALTER TABLE file_sources DROP COLUMN data;

DROP TABLE node_rows;

ALTER TABLE node_meta
    ADD CONSTRAINT node_meta_object_storage_modes_only
    CHECK (persistence_mode IN ('metadata_only', 'full_artifact'))
    NOT VALID;
ALTER TABLE node_meta VALIDATE CONSTRAINT node_meta_object_storage_modes_only;
