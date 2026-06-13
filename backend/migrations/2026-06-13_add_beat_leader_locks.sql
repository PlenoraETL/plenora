-- Plenora 2.0 (Fase 3): leader election del beat su PostgreSQL.
--
-- Rimpiazza il lock leader Redis (SETNX + EXPIRE su "scheduler:beat:leader").
-- Pattern TTL + refresh identico ad admin_backup_manual_locks: il beat
-- leader rinnova expires_at a ogni tick; se muore, il lock scade entro TTL
-- e un altro beat lo riacquisisce. Pool-safe (niente advisory lock legato
-- alla sessione). Un passo verso la rimozione di Redis (Fase 5).

CREATE TABLE IF NOT EXISTS plenora.beat_leader_locks (
  lock_name   TEXT PRIMARY KEY,
  owner_token TEXT NOT NULL,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_beat_leader_locks_expires_at
  ON plenora.beat_leader_locks (expires_at);
