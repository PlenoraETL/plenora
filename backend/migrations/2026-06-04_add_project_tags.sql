-- Tag liberi per organizzare i progetti nella dashboard (#10).
-- Array JSON di stringhe (es. ["cliente-x", "produzione"]). Default [].

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb;
