-- Prevent result metadata from outliving (or being written after) its project.
--
-- NOT VALID deliberately preserves any historical orphan rows so the
-- application cleanup can remove their object-storage blobs through the
-- reconciliation workflow. PostgreSQL still enforces the constraint for all
-- new INSERT/UPDATE/DELETE operations immediately.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'results_project_id_fkey'
          AND conrelid = 'results'::regclass
    ) THEN
        ALTER TABLE results
            ADD CONSTRAINT results_project_id_fkey
            FOREIGN KEY (project_id)
            REFERENCES projects(id)
            ON DELETE RESTRICT
            NOT VALID;
    END IF;
END
$$;

COMMENT ON CONSTRAINT results_project_id_fkey ON results IS
    'Prevents result orphans; historical rows are cleaned via admin orphan cleanup before validation.';
