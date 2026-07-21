-- Persist the normalized CARICA destination on intermediate runs.
-- New executions cannot acquire a destination while an older run for the
-- same target is still running or has an operator-controlled uncertain outcome.

ALTER TABLE plenora.runs
  ADD COLUMN IF NOT EXISTS target_lock_key TEXT;

CREATE INDEX IF NOT EXISTS idx_runs_carica_target_barrier
  ON plenora.runs (target_lock_key, status)
  WHERE target_lock_key IS NOT NULL
    AND status IN ('running', 'completion_uncertain');
