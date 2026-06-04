-- Versioning canvas (#12): snapshot manuali del canvas di un progetto con
-- possibilità di ripristino. Cascade-delete col progetto.

CREATE TABLE IF NOT EXISTS project_canvas_versions (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    group_id    TEXT,
    label       TEXT,
    canvas      JSONB NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_canvas_versions_project
    ON project_canvas_versions (project_id, created_at DESC);
