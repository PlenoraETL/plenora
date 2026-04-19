-- N9 (2026-04-17): persistenza user_id nella tabella log_runs per audit.
--
-- Contesto: pipeline_task ora accetta user_id al pickup del worker
-- Celery e fa re-check permessi via assert_project_access. Per rendere
-- l'audit trail utile post-facto, persistiamo l'utente che ha lanciato
-- la run. Task cron/scheduled senza user umano -> user_id NULL (non viola
-- vincoli).
--
-- Nullable + indice parziale: indicizziamo solo le righe con user_id
-- popolato (le task scheduled non popolano la colonna e non ci interessa
-- cercarle per utente).

ALTER TABLE log_runs
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_log_runs_user_id
    ON log_runs(user_id)
    WHERE user_id IS NOT NULL;
