-- Eseguibili v2: identità stabile + versioni immutabili + run auditabili.
-- La migrazione è additiva e mantiene leggibili/eseguibili tutte le righe v1.

ALTER TABLE eseguibili
    ADD COLUMN IF NOT EXISTS project_type TEXT NOT NULL DEFAULT 'arricchisci';

ALTER TABLE eseguibili
    ADD COLUMN IF NOT EXISTS active_version_id TEXT;

CREATE TABLE IF NOT EXISTS eseguibile_versions (
    id                  TEXT PRIMARY KEY,
    eseguibile_id       TEXT NOT NULL REFERENCES eseguibili(id) ON DELETE CASCADE,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    schema_version      INTEGER NOT NULL DEFAULT 2 CHECK (schema_version >= 1),
    project_type        TEXT NOT NULL CHECK (project_type IN ('estrai', 'manipola', 'arricchisci')),
    source_project_id   TEXT,
    source_canvas_version_id TEXT,
    source_hash         TEXT NOT NULL,
    canvas              JSONB NOT NULL,
    manifest            JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'published'
                        CHECK (status IN ('draft', 'published', 'retired')),
    created_by          TEXT NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (eseguibile_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_eseguibile_versions_parent
    ON eseguibile_versions (eseguibile_id, version_number DESC);

CREATE TABLE IF NOT EXISTS eseguibile_runs (
    id                  TEXT PRIMARY KEY,
    eseguibile_id       TEXT NOT NULL REFERENCES eseguibili(id) ON DELETE CASCADE,
    version_id          TEXT NOT NULL REFERENCES eseguibile_versions(id),
    task_id             TEXT,
    group_id            TEXT NOT NULL REFERENCES groups(id),
    user_id             TEXT NOT NULL REFERENCES users(id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'success', 'failure', 'cancelled')),
    input_summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_metadata     JSONB,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_eseguibile_runs_parent
    ON eseguibile_runs (eseguibile_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_eseguibile_runs_task
    ON eseguibile_runs (task_id) WHERE task_id IS NOT NULL;

-- Backfill delle pubblicazioni precedenti. Il manifest v1 descrive il
-- contratto single-file senza mutare canvas o expected_columns.
INSERT INTO eseguibile_versions (
    id, eseguibile_id, version_number, schema_version, project_type,
    source_project_id, source_hash, canvas, manifest, status,
    created_by, created_at
)
SELECT
    e.id || '_v1',
    e.id,
    1,
    1,
    COALESCE(NULLIF(e.project_type, ''), 'arricchisci'),
    e.source_canvas_id,
    md5(e.canvas::text),
    e.canvas,
    jsonb_build_object(
        'schema_version', 1,
        'project_type', COALESCE(NULLIF(e.project_type, ''), 'arricchisci'),
        'source_project_id', COALESCE(e.source_canvas_id, ''),
        'inputs', jsonb_build_array(
            jsonb_build_object(
                'key', 'input_file',
                'label', 'File di input',
                'kind', 'file',
                'description', '',
                'required', true,
                'default_value', null,
                'multiple', false,
                'expected_columns', COALESCE(e.expected_columns, '[]'::jsonb),
                'validation', '{}'::jsonb,
                'order', 0
            )
        ),
        'bindings', '[]'::jsonb,
        'default_connection_id', null
    ),
    'published',
    e.created_by,
    e.created_at
FROM eseguibili e
WHERE NOT EXISTS (
    SELECT 1 FROM eseguibile_versions v WHERE v.eseguibile_id = e.id
);

UPDATE eseguibili e
SET active_version_id = v.id
FROM eseguibile_versions v
WHERE v.eseguibile_id = e.id
  AND v.version_number = 1
  AND e.active_version_id IS NULL;

DO $$
BEGIN
    ALTER TABLE eseguibili
        ADD CONSTRAINT eseguibili_active_version_fk
        FOREIGN KEY (active_version_id)
        REFERENCES eseguibile_versions(id)
        DEFERRABLE INITIALLY DEFERRED;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
