-- N9 (2026-04-17): allineamento persistenza user_id anche su `runs`
-- (schema intermediate). Simmetrico al fix su log_runs.user_id.
--
-- Contesto: la nota originale del finding diceva "persisti user_id
-- nella run per audit". Abbiamo due tabelle "run" nello stack:
--   - plenora.log_runs       -> audit log run-level (timing, status, steps)
--   - plenora.runs (intermediate schema) -> stato runtime intermedio
--                                           (hash, rows in/out, per-node)
--
-- Coerenza: entrambe ora hanno user_id nullable + indice parziale per
-- query "le run di questo utente" che possono filtrare su entrambe
-- senza discrepanze.

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_runs_user_id
    ON runs(user_id)
    WHERE user_id IS NOT NULL;
