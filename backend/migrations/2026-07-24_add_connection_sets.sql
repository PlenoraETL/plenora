-- Connection Set: routing sicuro selector utente -> connessione database.
-- La stringa di connessione resta nella tabella connections; i membri
-- conservano esclusivamente il relativo id.

CREATE TABLE IF NOT EXISTS connection_sets (
    id                   TEXT PRIMARY KEY,
    group_id             TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    description          TEXT NOT NULL DEFAULT '',
    selector_label       TEXT NOT NULL DEFAULT 'Cliente',
    created_by           TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, name)
);

CREATE INDEX IF NOT EXISTS idx_connection_sets_group
    ON connection_sets(group_id);

CREATE TABLE IF NOT EXISTS connection_set_members (
    connection_set_id    TEXT NOT NULL
                             REFERENCES connection_sets(id) ON DELETE CASCADE,
    selector_key         TEXT NOT NULL,
    label                TEXT NOT NULL,
    connection_id        TEXT NOT NULL
                             REFERENCES connections(id) ON DELETE RESTRICT,
    active               BOOLEAN NOT NULL DEFAULT true,
    sort_order           INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (connection_set_id, selector_key)
);

CREATE INDEX IF NOT EXISTS idx_connection_set_members_connection
    ON connection_set_members(connection_id);

CREATE INDEX IF NOT EXISTS idx_connection_set_members_active
    ON connection_set_members(connection_set_id, active, sort_order, label);
