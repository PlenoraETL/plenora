-- ============================================================
-- PLENORA — Schema SQLite #1 (config.db)
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    email         TEXT,
    group_id      TEXT NOT NULL REFERENCES groups(id),
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    group_id      TEXT NOT NULL REFERENCES groups(id),
    name          TEXT NOT NULL,
    project_type  TEXT NOT NULL,
    canvas        TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    connection_id TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    created_by    TEXT REFERENCES users(id),
    retention_n   INTEGER DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_group ON projects(group_id);
CREATE INDEX IF NOT EXISTS idx_projects_type  ON projects(project_type);

CREATE TABLE IF NOT EXISTS comparison_keys (
    canvas_id    TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    group_id     TEXT NOT NULL REFERENCES groups(id),
    key_columns  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (canvas_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_comparison_keys_group ON comparison_keys(group_id);

CREATE TABLE IF NOT EXISTS connections (
    id        TEXT PRIMARY KEY,
    group_id  TEXT NOT NULL REFERENCES groups(id),
    name      TEXT NOT NULL,
    db_type   TEXT NOT NULL,
    host      TEXT,
    port      INTEGER,
    database  TEXT,
    db_schema TEXT,
    username  TEXT,
    password  TEXT,
    extra     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_connections_group ON connections(group_id);

CREATE TABLE IF NOT EXISTS file_sources (
    id                TEXT PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(id),
    name              TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    format            TEXT NOT NULL,
    data              BLOB NOT NULL,
    size_bytes        INTEGER,
    metadata          TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    created_by        TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_file_sources_group ON file_sources(group_id);

CREATE TABLE IF NOT EXISTS datasources (
    id         TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL REFERENCES groups(id),
    name       TEXT NOT NULL,
    ds_type    TEXT NOT NULL,
    config     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_datasources_group ON datasources(group_id);

CREATE TABLE IF NOT EXISTS arcgis_connections (
    id            TEXT PRIMARY KEY,
    group_id      TEXT NOT NULL REFERENCES groups(id),
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    server_url    TEXT NOT NULL,
    server_type   TEXT NOT NULL DEFAULT 'arcgis_server',
    auth_type     TEXT NOT NULL DEFAULT 'token',
    username      TEXT DEFAULT '',
    password      TEXT DEFAULT '',
    client_id     TEXT DEFAULT '',
    client_secret TEXT DEFAULT '',
    token_url     TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_arcgis_connections_group ON arcgis_connections(group_id);

CREATE TABLE IF NOT EXISTS api_connections (
    id         TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL REFERENCES groups(id),
    config     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_connections_group ON api_connections(group_id);

CREATE TABLE IF NOT EXISTS schedules (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL REFERENCES groups(id),
    project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    is_enabled      INTEGER NOT NULL DEFAULT 1,
    config          TEXT,
    last_run        TEXT,
    last_status     TEXT,
    next_run        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_group   ON schedules(group_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(is_enabled);

CREATE TABLE IF NOT EXISTS beat_heartbeat (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ============================================================
-- ESEGUIBILI — Catene pubblicate come template on-demand
-- Isolato da projects: canvas cloneato al momento della creazione,
-- indipendente dal progetto sorgente originale.
-- ============================================================
CREATE TABLE IF NOT EXISTS eseguibili (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT,
    canvas                TEXT NOT NULL,              -- JSON del canvas clonato
    expected_columns      TEXT,                       -- JSON: [{name, type, required, used_by}]
    default_output_format TEXT NOT NULL DEFAULT 'xlsx',
    default_output_name   TEXT,
    source_canvas_id      TEXT,                       -- tracking progetto originale (nullable)
    group_id              TEXT NOT NULL REFERENCES groups(id),
    created_by            TEXT NOT NULL REFERENCES users(id),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eseguibili_group ON eseguibili(group_id);

