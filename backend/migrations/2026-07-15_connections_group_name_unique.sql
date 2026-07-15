-- Enforce the invariant already exposed by ConnectionService under concurrency.
-- If this migration reports duplicates, reconcile them explicitly before retrying;
-- silently deleting credential records would be unsafe.
CREATE UNIQUE INDEX IF NOT EXISTS ux_connections_group_name
    ON plenora.connections (group_id, name);
