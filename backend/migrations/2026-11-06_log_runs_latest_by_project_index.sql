-- plenora:migration no-transaction
-- UI-1 (06/11/2026, audit C25): indici per GET /pipeline-runs/latest-by-project.
--
-- La dashboard chiede lo stato dell'ultima esecuzione di ogni progetto con
-- `SELECT DISTINCT ON (project_id) ... ORDER BY project_id, ts_start DESC`
-- (core/logs/runs_router.py). Senza indice PostgreSQL ordina tutta
-- log_runs a ogni apertura della dashboard; con (project_id, ts_start DESC)
-- legge le righe gia' in ordine. Gli utenti non superadmin filtrano anche
-- per group_id: l'indice con il gruppo in testa serve a loro.
--
-- SICUREZZA DELL'INSTALLAZIONE (conferma UI A-K11): log_runs e' scritta a
-- ogni esecuzione, quindi gli indici si creano CONCURRENTLY (nessun blocco
-- delle scritture durante l'installazione). La prima riga fa girare il file
-- fuori transazione, un'istruzione alla volta (app/db/migrations_runner.py);
-- nessun dato letto o modificato, rieseguibile (IF NOT EXISTS; un indice
-- rimasto INVALID da un'installazione interrotta il runner lo ricrea).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_log_runs_project_ts_start
    ON log_runs (project_id, ts_start DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_log_runs_group_project_ts_start
    ON log_runs (group_id, project_id, ts_start DESC);
