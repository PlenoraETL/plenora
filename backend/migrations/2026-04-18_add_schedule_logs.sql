-- FS-B3: migrazione log scheduler da filesystem a DB.
--
-- Prima: `data/scheduler_logs/YYYY-MM-DD/sched_*.json` file JSON.
-- Problemi: container-unfriendly (log persi al restart pod), paginazione
-- O(total_logs), niente transazionalita', retention gestita a mano.
--
-- Ora: tabella `plenora.schedule_logs` con indici su (schedule_id,
-- timestamp DESC) per list paginate e su (timestamp) per delete_before
-- retention. Campi strutturati (event, level, steps JSONB, error_message)
-- + blob JSONB per metadata esteso (schedule_info/pipeline_info/execution_info).
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS plenora.schedule_logs (
    log_id             TEXT PRIMARY KEY,
    schedule_id        TEXT NOT NULL,
    timestamp          TIMESTAMPTZ NOT NULL,
    level              TEXT NOT NULL,
    event              TEXT NOT NULL,
    triggered_by       TEXT NOT NULL,
    steps              JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_duration_ms  INTEGER NOT NULL DEFAULT 0,
    error_message      TEXT,
    schedule_info      JSONB,
    pipeline_info      JSONB,
    execution_info     JSONB
);

-- Index primario: list paginata per schedule + ordine inverso temporale.
-- Copre get_logs(schedule_id, ORDER BY timestamp DESC LIMIT N).
CREATE INDEX IF NOT EXISTS idx_schedule_logs_schedule_ts
    ON plenora.schedule_logs (schedule_id, timestamp DESC);

-- Index secondario: delete_logs_before(date) fa range scan su timestamp.
CREATE INDEX IF NOT EXISTS idx_schedule_logs_timestamp
    ON plenora.schedule_logs (timestamp);
