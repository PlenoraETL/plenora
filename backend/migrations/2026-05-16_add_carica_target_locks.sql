-- CARICA distributed destination lock store.
--
-- Serializes writes that target the same tenant-scoped database table,
-- REST endpoint, or ArcGIS service/layer across worker processes.

CREATE TABLE IF NOT EXISTS plenora.carica_target_locks (
  lock_key        TEXT PRIMARY KEY,
  group_id        TEXT NOT NULL DEFAULT '',
  target_kind     TEXT NOT NULL,
  target_identity TEXT NOT NULL,
  owner_token     TEXT NOT NULL,
  acquired_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_carica_target_locks_group_kind
  ON plenora.carica_target_locks (group_id, target_kind);

CREATE INDEX IF NOT EXISTS idx_carica_target_locks_expires_at
  ON plenora.carica_target_locks (expires_at);
