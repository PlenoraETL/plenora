-- Cross-host lease for consume-once executable output downloads.

CREATE TABLE IF NOT EXISTS plenora.eseguibili_download_claims (
    task_id text NOT NULL,
    group_id text NOT NULL,
    owner_token text NOT NULL,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_eseguibili_download_claims_expiry
    ON plenora.eseguibili_download_claims (lease_expires_at);
