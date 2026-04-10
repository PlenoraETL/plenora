-- ============================================================
-- PLENORA — Schema SQLite #3 (intermediate.db)
-- Dati intermedi di esecuzione — feature inspection e confronto run
-- Tabelle fisse + tabelle dinamiche generate a runtime
-- ============================================================

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA wal_autocheckpoint=1000;
PRAGMA foreign_keys=OFF;  -- FK cross-db non supportate da SQLite

-- ============================================================
-- RUNS — una riga per ogni esecuzione pipeline
-- Contiene summary per confronto rapido senza entrare nei nodi
-- ============================================================

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    canvas_id           TEXT NOT NULL,   -- corrisponde a project_id
    group_id            TEXT NOT NULL,   -- isolamento obbligatorio
    timestamp           TEXT NOT NULL,   -- ISO datetime avvio
    status              TEXT NOT NULL DEFAULT 'running',  -- running / completed / failed
    pinned              INTEGER NOT NULL DEFAULT 0,       -- 1 = escluso da retention FIFO

    -- Summary per confronto rapido L1
    total_rows_in       INTEGER,         -- righe in ingresso alla pipeline
    total_rows_out      INTEGER,         -- righe in uscita dalla pipeline
    total_rows_dropped  INTEGER,         -- differenza in/out
    total_nodes         INTEGER,
    nodes_ok            INTEGER DEFAULT 0,
    nodes_error         INTEGER DEFAULT 0,
    nodes_skipped       INTEGER DEFAULT 0,
    total_exec_time     REAL,            -- secondi totali

    -- Hash aggregati per confronto rapido
    -- Algoritmo: per colonna (count/sum/min/max per numeriche,
    -- count/distinti per testuali, bbox per geometriche),
    -- concatenati ordinati per nome colonna, SHA256 finale
    input_hash          TEXT,            -- hash dati in ingresso
    output_hash         TEXT             -- hash dati in uscita
);

CREATE INDEX IF NOT EXISTS idx_runs_canvas    ON runs(canvas_id);
CREATE INDEX IF NOT EXISTS idx_runs_group     ON runs(group_id);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_pinned    ON runs(pinned);

-- ============================================================
-- NODE_META — metadati per ogni nodo di ogni run
-- ============================================================

CREATE TABLE IF NOT EXISTS node_meta (
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL,
    node_type   TEXT,                    -- estrai / manipola / arricchisci / carica
    row_count   INTEGER,                 -- totale righe prodotte dal nodo
    col_schema  TEXT,                    -- JSON: {"col_name": "dtype", ...}
    exec_time   REAL,                    -- secondi impiegati dal nodo
    status      TEXT,                    -- ok / error / skipped
    error_msg   TEXT,                    -- messaggio di errore se status=error
    PRIMARY KEY (run_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_node_meta_run ON node_meta(run_id);

-- ============================================================
-- TABELLE DINAMICHE — generate a runtime dal writer task
-- Naming convention: run_{run_id}_{node_id}
-- Ogni tabella ha _row_hash + colonne reali del DataFrame
--
-- Esempio:
--   CREATE TABLE run_abc123_node_estrai (
--     _row_hash  TEXT,    -- MD5 valori ordinati per nome colonna
--     id         INTEGER,
--     paese      TEXT,
--     codice     TEXT
--   );
--
-- Cancellazione run: DROP TABLE run_{run_id}_*
-- ============================================================
