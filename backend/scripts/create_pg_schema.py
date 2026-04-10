"""
Crea tutte le tabelle URBY-ETL su PostgreSQL nello schema 'plenora'.
Solo struttura, zero dati.
"""
import psycopg2

conn = psycopg2.connect(
    host='15.161.59.22', port=5432,
    user='postgres',
    password='zAWMyAJTzEnDUOj9JyMKdmngZWrhlupyo94GGPOOT1o=',
    dbname='plenora',
    options='-c search_path=plenora'
)
conn.autocommit = True
cur = conn.cursor()

# Drop e ricrea schema pulito
cur.execute("DROP SCHEMA IF EXISTS plenora CASCADE")
cur.execute("CREATE SCHEMA plenora")

# Esegui TUTTO il DDL come un unico blocco con search_path settato
conn.autocommit = False
cur.execute("SET search_path TO plenora")

DDL = """
-- CONFIG
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    email TEXT, group_id TEXT NOT NULL REFERENCES groups(id),
    role TEXT NOT NULL DEFAULT 'user', is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL, project_type TEXT NOT NULL,
    canvas JSONB NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    connection_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id), retention_n INTEGER DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_group ON projects(group_id);
CREATE INDEX IF NOT EXISTS idx_projects_type ON projects(project_type);

CREATE TABLE IF NOT EXISTS comparison_keys (
    canvas_id TEXT NOT NULL, node_id TEXT NOT NULL,
    group_id TEXT NOT NULL REFERENCES groups(id),
    key_columns JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (canvas_id, node_id)
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL, db_type TEXT NOT NULL, host TEXT, port INTEGER,
    database TEXT, db_schema TEXT, username TEXT, password TEXT, extra JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_connections_group ON connections(group_id);

CREATE TABLE IF NOT EXISTS file_sources (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL, original_filename TEXT NOT NULL, format TEXT NOT NULL,
    data BYTEA NOT NULL, size_bytes INTEGER, metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_file_sources_group ON file_sources(group_id);

CREATE TABLE IF NOT EXISTS datasources (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL, ds_type TEXT NOT NULL, config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_datasources_group ON datasources(group_id);

CREATE TABLE IF NOT EXISTS arcgis_connections (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL, description TEXT DEFAULT '',
    server_url TEXT NOT NULL, server_type TEXT NOT NULL DEFAULT 'arcgis_server',
    auth_type TEXT NOT NULL DEFAULT 'token',
    username TEXT DEFAULT '', password TEXT DEFAULT '',
    client_id TEXT DEFAULT '', client_secret TEXT DEFAULT '', token_url TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_arcgis_connections_group ON arcgis_connections(group_id);

CREATE TABLE IF NOT EXISTS api_connections (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_api_connections_group ON api_connections(group_id);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    name TEXT NOT NULL, cron_expression TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE, config JSONB,
    last_run TIMESTAMPTZ, last_status TEXT, next_run TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_schedules_group ON schedules(group_id);

CREATE TABLE IF NOT EXISTS beat_heartbeat (id TEXT PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS eseguibili (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    canvas JSONB NOT NULL, expected_columns JSONB,
    default_output_format TEXT NOT NULL DEFAULT 'xlsx', default_output_name TEXT,
    icon TEXT, source_canvas_id TEXT,
    group_id TEXT NOT NULL REFERENCES groups(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eseguibili_group ON eseguibili(group_id);

-- SHARING
CREATE TABLE IF NOT EXISTS connection_shares (
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (connection_id, group_id)
);
CREATE TABLE IF NOT EXISTS datasource_shares (
    datasource_id TEXT NOT NULL REFERENCES datasources(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (datasource_id, group_id)
);
CREATE TABLE IF NOT EXISTS eseguibile_shares (
    eseguibile_id TEXT NOT NULL REFERENCES eseguibili(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (eseguibile_id, group_id)
);
CREATE TABLE IF NOT EXISTS api_connection_shares (
    api_connection_id TEXT NOT NULL REFERENCES api_connections(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_connection_id, group_id)
);
CREATE TABLE IF NOT EXISTS arcgis_connection_shares (
    arcgis_connection_id TEXT NOT NULL REFERENCES arcgis_connections(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (arcgis_connection_id, group_id)
);
CREATE TABLE IF NOT EXISTS file_source_shares (
    file_source_id TEXT NOT NULL REFERENCES file_sources(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (file_source_id, group_id)
);

-- RESULTS
CREATE TABLE IF NOT EXISTS results (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, group_id TEXT NOT NULL,
    filename TEXT NOT NULL, format TEXT NOT NULL, data BYTEA,
    size_bytes INTEGER, rows_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_by TEXT,
    UNIQUE (project_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_results_project ON results(project_id);
CREATE INDEX IF NOT EXISTS idx_results_group ON results(group_id);

CREATE TABLE IF NOT EXISTS execution_logs (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL, project_id TEXT,
    schedule_id TEXT, status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
    duration_s REAL, rows_count INTEGER, message TEXT, error TEXT, triggered_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_logs_project ON execution_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_exec_logs_group ON execution_logs(group_id);

-- INTERMEDIATE
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, canvas_id TEXT NOT NULL, group_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'running', pinned BOOLEAN NOT NULL DEFAULT FALSE,
    total_rows_in INTEGER, total_rows_out INTEGER, total_rows_dropped INTEGER,
    total_nodes INTEGER, nodes_ok INTEGER DEFAULT 0,
    nodes_error INTEGER DEFAULT 0, nodes_skipped INTEGER DEFAULT 0,
    total_exec_time REAL, input_hash TEXT, output_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_canvas ON runs(canvas_id);
CREATE INDEX IF NOT EXISTS idx_runs_group ON runs(group_id);

CREATE TABLE IF NOT EXISTS node_meta (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL, node_type TEXT, row_count INTEGER,
    col_schema JSONB, exec_time REAL, status TEXT, error_msg TEXT,
    PRIMARY KEY (run_id, node_id)
);

-- LOGGER
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL, module TEXT NOT NULL, op TEXT DEFAULT '',
    msg TEXT NOT NULL, project_id TEXT DEFAULT '', group_id TEXT DEFAULT '',
    run_id TEXT DEFAULT '', node_id TEXT DEFAULT '',
    details JSONB, error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_log_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_log_module ON logs(module);
CREATE INDEX IF NOT EXISTS idx_log_project_id ON logs(project_id);
CREATE INDEX IF NOT EXISTS idx_log_group_id ON logs(group_id);

CREATE TABLE IF NOT EXISTS log_runs (
    run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
    group_id TEXT DEFAULT '', module TEXT DEFAULT '',
    triggered_by TEXT DEFAULT 'manual',
    ts_start TIMESTAMPTZ NOT NULL DEFAULT NOW(), ts_end TIMESTAMPTZ,
    status TEXT DEFAULT 'running', rows_in INTEGER DEFAULT 0,
    rows_out INTEGER DEFAULT 0, duration_ms INTEGER DEFAULT 0,
    error_node_id TEXT, error_module TEXT, error_message TEXT,
    steps JSONB DEFAULT '[]', debug_payload JSONB
);
CREATE INDEX IF NOT EXISTS idx_log_runs_project ON log_runs(project_id);

-- SCHEDULER LOCKS
CREATE TABLE IF NOT EXISTS scheduler_locks (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at TIMESTAMPTZ
);
"""

# Esegui tutto come un singolo script (una transazione)
cur.execute(DDL)
conn.commit()
print("DDL eseguito.")

# Verifica
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'plenora' ORDER BY table_name
""")
tables = [r[0] for r in cur.fetchall()]
print(f"Tabelle create: {len(tables)}")
for t in tables:
    print(f"  {t}")

conn.close()
print("\nDone.")
