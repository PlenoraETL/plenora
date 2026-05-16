-- Manual admin backup lock store.
--
-- Replaces the host-local /tmp lock file with a PostgreSQL row so multiple
-- backend instances share the same concurrency boundary.

CREATE TABLE IF NOT EXISTS plenora.admin_backup_manual_locks (
  lock_name   TEXT PRIMARY KEY,
  owner_token TEXT NOT NULL,
  owner_pid   INTEGER,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_backup_manual_locks_expires_at
  ON plenora.admin_backup_manual_locks (expires_at);
