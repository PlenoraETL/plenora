-- Plenora 2.0 (Fase 2): result/state backend dei task su PostgreSQL.
--
-- Rimpiazza il result backend Redis di Celery. Stato, progress e
-- risultato finale di ogni task vivono qui e vengono letti
-- dall'orchestratore via TaskRuntimePort.get_result(). Una sola colonna
-- `payload` JSONB riproduce la semantica Celery `AsyncResult.info ==
-- .result`: durante PROGRESS contiene il dict di progresso, sugli stati
-- terminali (SUCCESS/FAILURE) il valore di ritorno del task.
--
-- TTL: `expires_at` rimpiazza CELERY_RESULT_EXPIRES; uno sweep di
-- manutenzione cancella le righe scadute (la scadenza non era piu'
-- gestita da Redis).

CREATE TABLE IF NOT EXISTS plenora.task_results (
  task_id     TEXT PRIMARY KEY,
  state       TEXT NOT NULL,
  name        TEXT,
  payload     JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_task_results_expires_at
  ON plenora.task_results (expires_at);
