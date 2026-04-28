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
    -- Phase 6 Batch 1 (2026-04-28): policy persistenza intermedi.
    -- Tutte nullable -> record legacy = full_pg.
    node_hash             TEXT,           -- fingerprint DataFrame (es. SHA256)
    hash_algo             TEXT,           -- es. "pandas_hash_v1"
    persistence_mode      TEXT,           -- metadata_only | preview_only | full_pg | full_artifact
    persisted_rows_count  INTEGER,        -- righe in node_rows per questo nodo
    preview_truncated     BOOLEAN,        -- true se persisted_rows_count < row_count
    artifact_uri          TEXT,           -- pointer artifact full (full_artifact mode)
    artifact_format       TEXT,           -- es. "parquet"
    artifact_status       TEXT,           -- none | pending | complete | failed
    artifact_error        TEXT,           -- messaggio errore artifact
    PRIMARY KEY (run_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_node_meta_run ON node_meta(run_id);

-- ============================================================
-- NODE_ROWS — dati intermedi dei nodi (unificato JSONB)
-- ============================================================
-- In passato ogni nodo di ogni run aveva una tabella fisica dedicata
-- (naming: run_{uuid}_{node_id}) che causava bloat del pg_catalog,
-- lock contention su CREATE/DROP e orphan tables. Ora tutto finisce in
-- una singola tabella JSONB con FK ON DELETE CASCADE.
--
-- Vantaggi:
--   - 1 tabella invece di N × righe_di_run in pg_class
--   - retention atomica: DELETE FROM runs → node_rows svuotata
--   - query cross-run (diff tra due run dello stesso nodo) via indice hash
--   - schema uniforme indipendente dal canvas

CREATE TABLE IF NOT EXISTS node_rows (
    id        BIGSERIAL PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_id   TEXT NOT NULL,
    row_idx   INTEGER NOT NULL,       -- ordine originale nel DataFrame
    row_hash  TEXT NOT NULL,          -- MD5 riga (per diff cross-run)
    row_data  JSONB NOT NULL          -- valori della riga
);

-- Lookup per-nodo di tutte le righe di una run.
-- Phase 4 A3 — idempotency invariant (no schema change required):
-- `app/core/intermediate/db.py` garantisce che la coppia
-- (DELETE FROM node_rows WHERE run_id=? AND node_id=?) + (INSERT bulk)
-- sia atomica e venga ripetuta a ogni invocazione di insert_node_rows o
-- write_node_atomic. Effetto: invocazioni N >= 2 sullo stesso
-- (run_id, node_id) lasciano sempre solo l'ultima scrittura. Nessun
-- vincolo UNIQUE aggiunto: dati legacy potrebbero contenere duplicati
-- da retry pre-fix, e una migration richiederebbe cleanup batch
-- documentato. L'idempotency e' applicata application-side senza schema
-- breaking change.
CREATE INDEX IF NOT EXISTS idx_node_rows_run_node
    ON node_rows(run_id, node_id);

-- Confronto L2 cross-run: il predicato NOT EXISTS joina su (run_id, node_id, row_hash)
CREATE INDEX IF NOT EXISTS idx_node_rows_hash
    ON node_rows(run_id, node_id, row_hash);
