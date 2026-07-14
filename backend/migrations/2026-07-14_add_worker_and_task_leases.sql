-- Runtime leases shared by Windows, Linux and container deployments.

CREATE TABLE IF NOT EXISTS plenora.worker_heartbeats (
    worker_id text PRIMARY KEY,
    role text NOT NULL CHECK (role IN ('data-plane', 'control-plane')),
    queues jsonb NOT NULL DEFAULT '[]'::jsonb,
    hostname text NOT NULL,
    pid integer NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_role_heartbeat
    ON plenora.worker_heartbeats (role, heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS plenora.task_execution_claims (
    task_id text PRIMARY KEY,
    actor_name text NOT NULL,
    worker_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('running', 'completed', 'failed')),
    acquired_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_execution_claims_stale
    ON plenora.task_execution_claims (state, lease_expires_at)
    WHERE state = 'running';

CREATE INDEX IF NOT EXISTS idx_task_execution_claims_finished
    ON plenora.task_execution_claims (finished_at)
    WHERE finished_at IS NOT NULL;
