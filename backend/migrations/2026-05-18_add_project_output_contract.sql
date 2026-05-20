-- Public project-to-project column contract.
-- This is intentionally separate from canvas editor state.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS output_contract JSONB;
