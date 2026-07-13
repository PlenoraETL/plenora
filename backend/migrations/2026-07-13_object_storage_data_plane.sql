-- Plenora object-storage-only data plane.
-- NOT VALID preserves legacy rows until the online migration script moves
-- them, while PostgreSQL enforces the rule for every new INSERT/UPDATE.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'results_payload_external_only'
          AND conrelid = 'results'::regclass
    ) THEN
        ALTER TABLE results
            ADD CONSTRAINT results_payload_external_only
            CHECK (data IS NULL) NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'file_sources_payload_external_only'
          AND conrelid = 'file_sources'::regclass
    ) THEN
        ALTER TABLE file_sources
            ADD CONSTRAINT file_sources_payload_external_only
            CHECK (data IS NULL) NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'node_rows_legacy_read_only'
          AND conrelid = 'node_rows'::regclass
    ) THEN
        ALTER TABLE node_rows
            ADD CONSTRAINT node_rows_legacy_read_only
            CHECK (false) NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'task_results_no_tabular_payload'
          AND conrelid = 'task_results'::regclass
    ) THEN
        ALTER TABLE task_results
            ADD CONSTRAINT task_results_no_tabular_payload
            CHECK (payload IS NULL OR NOT (payload ? 'rows')) NOT VALID;
    END IF;
END $$;
