-- Persist datasource scheduler scan state in PostgreSQL.
--
-- The legacy filesystem file under DATA_DIR/datasources_scan remains a
-- read-only migration fallback in app.core.datasources.service.

ALTER TABLE plenora.datasources
    ADD COLUMN IF NOT EXISTS scan_state JSONB;
