CREATE TABLE IF NOT EXISTS plenora.task_dispatch_outbox (
    task_id text PRIMARY KEY,
    actor_name text NOT NULL,
    options jsonb NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_dispatch_outbox_due
    ON plenora.task_dispatch_outbox (next_attempt_at, created_at);
