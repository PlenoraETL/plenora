-- Stored canvas-validation flag (Epic Canvas Validation §12).
-- True once the project chain validates with no errors via POST /{id}/validate;
-- reset to False on any canvas save and cascaded to dependent projects.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS canvas_validated boolean NOT NULL DEFAULT false;
