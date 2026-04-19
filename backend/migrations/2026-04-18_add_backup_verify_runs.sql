-- Observability refactor: fonte di verita' DB-based per stato backup/restore.
-- Prima: admin dashboard leggeva da systemctl/journalctl -> host-local, cieco in container.
-- Ora: scripts/backup_db.py e scripts/test_restore.py scrivono una row qui a fine
--      job; il service layer legge da qui.
--
-- Shape uguale per entrambe le tabelle. Colonne:
--   started_at   NOT NULL  - quando il job ha iniziato (INSERT a inizio)
--   finished_at  NULL      - popolato a fine job (NULL = in corso o crash senza cleanup)
--   exit_code    NULL      - 0=ok, >=1=errore; NULL se il job non e' finito
--   result       NOT NULL  - 'running' | 'success' | 'failed'
--   bucket_key   NULL      - S3 key del dump caricato (solo backup, NULL per verify)
--   size_bytes   NULL      - dimensione dump (solo backup)
--   log_tail     NULL      - ultime righe di log catturate dallo script (diagnostic)
--   error_message NULL     - stringa breve con errore se failed
--
-- Idempotente: IF NOT EXISTS ovunque.

CREATE TABLE IF NOT EXISTS plenora.backup_runs (
  id            BIGSERIAL PRIMARY KEY,
  started_at    TIMESTAMPTZ NOT NULL,
  finished_at   TIMESTAMPTZ,
  exit_code     INTEGER,
  result        TEXT NOT NULL CHECK (result IN ('running', 'success', 'failed')),
  bucket_key    TEXT,
  size_bytes    BIGINT,
  log_tail      TEXT,
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at
  ON plenora.backup_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS plenora.verify_runs (
  id            BIGSERIAL PRIMARY KEY,
  started_at    TIMESTAMPTZ NOT NULL,
  finished_at   TIMESTAMPTZ,
  exit_code     INTEGER,
  result        TEXT NOT NULL CHECK (result IN ('running', 'success', 'failed')),
  log_tail      TEXT,
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_verify_runs_started_at
  ON plenora.verify_runs (started_at DESC);
