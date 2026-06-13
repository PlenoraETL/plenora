-- Plenora 2.0 (Fase 5 F5.2): annullamento task cooperativo su PostgreSQL.
--
-- Rimpiazza `celery app.control.revoke`: Dramatiq non revoca per id un task
-- gia' in coda/esecuzione. Qui l'annullamento e' un flag su PG; i task lo
-- controllano ai checkpoint di progress (report_task_progress) e abortiscono
-- sollevando TaskRevoked, che gli actor dichiarano come `throws` (niente
-- retry) e il middleware finalizza nello stato REVOKED.
--
-- expires_at per lo sweep di manutenzione (un flag stantio non serve piu'
-- una volta che il task e' terminato); la correttezza non dipende dalla
-- pulizia.

CREATE TABLE IF NOT EXISTS plenora.task_revocations (
  task_id    TEXT PRIMARY KEY,
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_task_revocations_expires_at
  ON plenora.task_revocations (expires_at);
