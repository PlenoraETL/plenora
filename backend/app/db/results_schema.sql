-- ============================================================
-- PLENORA — Schema legacy (storico, ex-SQLite #2 / results.db)
-- Risultati finali pipeline + log esecuzioni
-- ============================================================
--
-- LEGACY HISTORICAL REFERENCE — NON ESEGUITO A RUNTIME.
--
-- Questo file documenta la struttura tabellare originaria del
-- "results.db" SQLite pre-migrazione PostgreSQL. Le tabelle
-- effettive su PostgreSQL sono create dalle migrations in
-- `backend/migrations/`. Il `db_service` facade (vedi
-- `app/db/results_db_service.py` docstring) instrada le query
-- al pool PG.
--
-- `init_db()` NON carica questo file. Le PRAGMA SQLite sotto
-- sono no-op su PostgreSQL e restano solo come testimonianza
-- del modello di concorrenza pre-migrazione (FK cross-db non
-- enforced perche' i tre SQLite erano file separati; su PG le
-- FK sono enforced perche' tutte le tabelle vivono nello stesso
-- schema applicativo).
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=OFF;  -- FK cross-db non supportate da SQLite
PRAGMA busy_timeout=5000;

-- ============================================================
-- RISULTATI (output finale pipeline)
-- ============================================================

CREATE TABLE IF NOT EXISTS results (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    filename    TEXT NOT NULL,
    format      TEXT NOT NULL,
    data        BLOB,
    size_bytes  INTEGER,
    rows_count  INTEGER,
    created_at  TEXT NOT NULL,
    created_by  TEXT
    -- Volutamente senza UNIQUE(project_id, filename): la retention policy
    -- conserva piu' risultati per progetto con stesso filename.
);

CREATE INDEX IF NOT EXISTS idx_results_project   ON results(project_id);
CREATE INDEX IF NOT EXISTS idx_results_group     ON results(group_id);
CREATE INDEX IF NOT EXISTS idx_results_created   ON results(created_at);

-- ============================================================
-- LOG ESECUZIONI
-- ============================================================

CREATE TABLE IF NOT EXISTS execution_logs (
    id           TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL,
    project_id   TEXT,
    schedule_id  TEXT,
    status       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_s   REAL,
    rows_count   INTEGER,
    message      TEXT,
    error        TEXT,
    triggered_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_project  ON execution_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_logs_group    ON execution_logs(group_id);
CREATE INDEX IF NOT EXISTS idx_logs_started  ON execution_logs(started_at);
