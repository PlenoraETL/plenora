-- Audit log dei catchup run emessi dal beat scheduler (C3 fix 3).
--
-- Uno schedule con `catchup_missed_runs=True` puo' generare, ai tick
-- successivi a un downtime, uno o piu' run "di recupero". Questa tabella
-- li traccia per:
--   1. debug post-incident ("perche' quel task e' scattato alle 10 invece
--      che alle 6?");
--   2. cap anti-runaway: il beat legge la tabella per verificare quante
--      catchup emissions sono state fatte nei precedenti 10 minuti per
--      un dato schedule, e skippa (+warning) se supera la soglia.
--
-- scheduled_for = timestamp dell'expected_last cron tick (il "momento
-- logico" del run). emitted_at = quando il beat ha emesso effettivamente
-- il task (diverso e sempre >= scheduled_for).
-- leader_id = pid@hostname del beat che ha emesso, per risalire all'host.

CREATE TABLE IF NOT EXISTS schedule_catchup_history (
    id              BIGSERIAL PRIMARY KEY,
    schedule_id     TEXT NOT NULL,
    emitted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    scheduled_for   TIMESTAMPTZ NOT NULL,
    leader_id       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedule_catchup_schedule_emitted
    ON schedule_catchup_history(schedule_id, emitted_at DESC);
