-- ============================================================
-- PLENORA — Schema legacy (storico, ex-SQLite #1 / config.db)
-- ============================================================
--
-- LEGACY HISTORICAL REFERENCE — NON ESEGUITO A RUNTIME.
--
-- Questo file documenta la struttura tabellare originaria del
-- "config.db" SQLite pre-migrazione PostgreSQL. Le tabelle
-- effettive su PostgreSQL sono create dalle migrations in
-- `backend/migrations/` (entry-point
-- `0000_bootstrap_base_tables.sql` + add-on
-- `2026-XX-XX_*.sql`).
--
-- `init_db()` in `app/db/pg_connection.py` NON carica questo file
-- (verifica solo che le tabelle esistano nello schema applicativo).
--
-- Mantenuto come reference storico per:
-- - chi legge `app/core/eseguibili/db.py:4` ("La tabella e'
--   definita in app/db/config_schema.sql"): documentazione del
--   layout originale, non dichiarazione esecutiva;
-- - chi legge `app/db/config/file_sources.py:275` (commento sulle
--   indici introdotti contestualmente).
--
-- Le PRAGMA SQLite sotto sono no-op su PostgreSQL e restano solo
-- come testimonianza dell'architettura pre-migrazione.
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
    updated_at TEXT NOT NULL,
    created_by TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_connections_group      ON connections(group_id);
CREATE INDEX IF NOT EXISTS idx_connections_created_by ON connections(created_by);

CREATE TABLE IF NOT EXISTS file_sources (
    id                TEXT PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(id),
    name              TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    format            TEXT NOT NULL,
    -- Dispatch threshold (FS-B1-large): file < FILE_SOURCE_S3_THRESHOLD_BYTES
    -- vivono qui (BLOB); file >= threshold hanno data=NULL e storage_key
    -- puntato a S3 via cloud_backends. Il reader file_service risolve
    -- trasparente al caller.
    data              BLOB,
    storage_key       TEXT,
    size_bytes        INTEGER,
    metadata          TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    created_by        TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_file_sources_group ON file_sources(group_id);

-- BE-HARDEN-001C E4 (BE-DELETE-002): partial index su storage_key per
-- accelerare la sibling refcount lookup e SELECT FOR UPDATE durante
-- delete_file_source quando il blob e' shared (multi-layer upload).
-- Partial perche' BYTEA rows hanno storage_key=NULL e non vengono mai
-- interrogate by key.
CREATE INDEX IF NOT EXISTS idx_file_sources_storage_key
    ON file_sources(storage_key) WHERE storage_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS datasources (
    id         TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL REFERENCES groups(id),
    name       TEXT NOT NULL,
    ds_type    TEXT NOT NULL,
    config     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_datasources_group      ON datasources(group_id);
CREATE INDEX IF NOT EXISTS idx_datasources_created_by ON datasources(created_by);

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
    updated_at    TEXT NOT NULL,
    created_by    TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_arcgis_connections_group      ON arcgis_connections(group_id);
CREATE INDEX IF NOT EXISTS idx_arcgis_connections_created_by ON arcgis_connections(created_by);

CREATE TABLE IF NOT EXISTS api_connections (
    id         TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL REFERENCES groups(id),
    config     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_api_connections_group      ON api_connections(group_id);
CREATE INDEX IF NOT EXISTS idx_api_connections_created_by ON api_connections(created_by);

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
    updated_at      TEXT NOT NULL,
    created_by      TEXT REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_schedules_group      ON schedules(group_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled    ON schedules(is_enabled);
CREATE INDEX IF NOT EXISTS idx_schedules_created_by ON schedules(created_by);

-- Audit log catchup run emessi dal beat (C3 fix 3). Vedi
-- migrations/2026-04-16_add_schedule_catchup_history.sql per contesto
-- e per il cap anti-runaway consultato al momento dell'emit.
CREATE TABLE IF NOT EXISTS schedule_catchup_history (
    id              BIGSERIAL PRIMARY KEY,
    schedule_id     TEXT NOT NULL,
    emitted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    scheduled_for   TIMESTAMPTZ NOT NULL,
    leader_id       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_catchup_schedule_emitted
    ON schedule_catchup_history(schedule_id, emitted_at DESC);

CREATE TABLE IF NOT EXISTS beat_heartbeat (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL
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
    icon                  TEXT,                       -- emoji/carattere visualizzato sulla card (fallback: ▶)
    source_canvas_id      TEXT,                       -- tracking progetto originale (nullable)
    group_id              TEXT NOT NULL REFERENCES groups(id),
    created_by            TEXT NOT NULL REFERENCES users(id),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eseguibili_group ON eseguibili(group_id);

-- ============================================================
-- ALLOWED_GROUPS — tabelle di join per cross-group sharing.
-- Una risorsa appartiene al suo gruppo proprietario (resource.group_id)
-- E può essere condivisa con altri gruppi tramite righe in queste tabelle.
-- "ALL" è rappresentato dalla riga magica `group_id = '*'`.
-- ============================================================

CREATE TABLE IF NOT EXISTS connection_shares (
    connection_id TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (connection_id, group_id),
    FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_connection_shares_group ON connection_shares(group_id);

CREATE TABLE IF NOT EXISTS datasource_shares (
    datasource_id TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (datasource_id, group_id),
    FOREIGN KEY (datasource_id) REFERENCES datasources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_datasource_shares_group ON datasource_shares(group_id);

CREATE TABLE IF NOT EXISTS eseguibile_shares (
    eseguibile_id TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (eseguibile_id, group_id),
    FOREIGN KEY (eseguibile_id) REFERENCES eseguibili(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_eseguibile_shares_group ON eseguibile_shares(group_id);

CREATE TABLE IF NOT EXISTS api_connection_shares (
    api_connection_id TEXT NOT NULL,
    group_id          TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (api_connection_id, group_id),
    FOREIGN KEY (api_connection_id) REFERENCES api_connections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_connection_shares_group ON api_connection_shares(group_id);

CREATE TABLE IF NOT EXISTS arcgis_connection_shares (
    arcgis_connection_id TEXT NOT NULL,
    group_id             TEXT NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (arcgis_connection_id, group_id),
    FOREIGN KEY (arcgis_connection_id) REFERENCES arcgis_connections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_arcgis_connection_shares_group ON arcgis_connection_shares(group_id);

CREATE TABLE IF NOT EXISTS file_source_shares (
    file_source_id TEXT NOT NULL,
    group_id       TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY (file_source_id, group_id),
    FOREIGN KEY (file_source_id) REFERENCES file_sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_file_source_shares_group ON file_source_shares(group_id);

