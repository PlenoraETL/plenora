-- Pulizie complete di task ancora in corso (UI conferma finale F8/G3, 06/11/2026).
--
-- Regola applicativa (app/core/orchestrator/task_cleanup.py): la pulizia
-- completa di un task (DELETE /tasks/{id}/full-cleanup, l'esecutore che
-- chiude la pagina) tocca SOLO un task terminale. Un task ancora in corso si
-- annulla; se non si ferma entro TASK_CLEANUP_CANCEL_WAIT_SECONDS la pulizia
-- resta qui e la fa lo sweep del beat appena il task e' terminale, o dopo
-- deadline_at (task mai finito). Una riga per task, tolta a pulizia riuscita.
--
-- SICUREZZA DELL'INSTALLAZIONE: solo CREATE ... IF NOT EXISTS su una tabella
-- nuova e vuota; nessun dato esistente viene letto o modificato, quindi la
-- migrazione non puo' fallire per lo stato del DB e si puo' rieseguire.
CREATE TABLE IF NOT EXISTS task_cleanup_requests (
    task_id       TEXT PRIMARY KEY,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline_at   TIMESTAMPTZ NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT
);
