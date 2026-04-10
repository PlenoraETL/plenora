"""
Intermediate DB — accesso a PostgreSQL (ex SQLite intermediate.db).

Gestisce runs, node_meta e tabelle dinamiche run_{run_id}_{node_id}.
Migrato da SQLite a PostgreSQL: usa pg_connection pool.
"""

import json
import re
from typing import Dict, List, Optional

from app.utils.logger import get_logger

# Importa da pg_connection (stesso pool di config/results)
from app.db.pg_connection import (
    fetchone, fetchall, execute, executemany,
    get_connection, put_connection, close_connection as _pg_close,
    transaction,
)

logger = get_logger(__name__)


def close_connection() -> None:
    """No-op per PostgreSQL: il pool gestisce le connessioni.

    Mantiene compatibilità con i task Celery che chiamano
    close_connection() in finally.
    """
    _pg_close()


def init_db():
    """No-op per PostgreSQL: tabelle runs/node_meta già create."""
    logger.info("Intermediate DB: PostgreSQL (tabelle già presenti)", op="init_db")


# fetchone, fetchall, execute sono importati da pg_connection (top del file)

# ============================================================
# RUNS
# ============================================================

def create_run(run_id: str, canvas_id: str, group_id: str,
               total_nodes: int = 0) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        """INSERT INTO runs
           (run_id, canvas_id, group_id, timestamp, status, total_nodes)
           VALUES (?,?,?,?,?,?)""",
        (run_id, canvas_id, group_id, now, "running", total_nodes)
    )
    return get_run(run_id)


def get_run(run_id: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM runs WHERE run_id = ?", (run_id,))


def list_runs(canvas_id: str, group_id: str, limit: int = 50) -> List[Dict]:
    """Lista run per canvas, isolate per gruppo, più recenti prima."""
    return fetchall(
        "SELECT * FROM runs WHERE canvas_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT ?",
        (canvas_id, group_id, limit)
    )


# Whitelist campi aggiornabili sulla tabella `runs`. Anti-injection: impedisce
# che kwargs arbitrari finiscano in un UPDATE dinamico (vedi update_run).
# Esclude run_id, canvas_id, group_id, timestamp, pinned (immutabili o gestiti
# da operazioni dedicate come pin_run).
_ALLOWED_RUN_UPDATE_FIELDS = {
    "status",
    "input_hash", "output_hash",
    "total_exec_time",
    "total_rows_in", "total_rows_out", "total_rows_dropped",
    "total_nodes", "nodes_ok", "nodes_error", "nodes_skipped",
}


def update_run(run_id: str, **fields) -> None:
    """Aggiorna i campi consentiti di una run.

    Solo i campi in `_ALLOWED_RUN_UPDATE_FIELDS` sono accettati. Solleva
    ValueError se un caller passa un campo non consentito (es. tentativo di
    cambiare canvas_id/group_id, che cambierebbe la proprietà della run).
    """
    if not fields:
        return
    invalid = set(fields) - _ALLOWED_RUN_UPDATE_FIELDS
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE runs: {invalid}. "
            f"Consentiti: {_ALLOWED_RUN_UPDATE_FIELDS}"
        )
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    execute(
        f"UPDATE runs SET {set_clause} WHERE run_id = ?",
        tuple(fields.values()) + (run_id,)
    )


def pin_run(run_id: str, group_id: str, pinned: bool = True) -> bool:
    """Pinna o spinna una run. Verifica group_id per sicurezza."""
    cur = execute(
        "UPDATE runs SET pinned = ? WHERE run_id = ? AND group_id = ?",
        (pinned, run_id, group_id)
    )
    return cur.rowcount > 0


# ============================================================
# NODE META
# ============================================================

def upsert_node_meta(run_id: str, node_id: str, node_type: str = None,
                     row_count: int = None, col_schema: dict = None,
                     exec_time: float = None, status: str = None,
                     error_msg: str = None) -> None:
    execute(
        """INSERT INTO node_meta
           (run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (run_id, node_id) DO UPDATE SET
           node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count,
           col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time,
           status=EXCLUDED.status, error_msg=EXCLUDED.error_msg""",
        (run_id, node_id, node_type, row_count,
         json.dumps(col_schema) if col_schema else None,
         exec_time, status, error_msg)
    )


def get_node_meta(run_id: str, node_id: str) -> Optional[Dict]:
    row = fetchone(
        "SELECT * FROM node_meta WHERE run_id = ? AND node_id = ?",
        (run_id, node_id)
    )
    if row and row.get("col_schema"):
        row["col_schema"] = json.loads(row["col_schema"])
    return row


def list_node_meta(run_id: str) -> List[Dict]:
    rows = fetchall("SELECT * FROM node_meta WHERE run_id = ?", (run_id,))
    for r in rows:
        if r.get("col_schema"):
            r["col_schema"] = json.loads(r["col_schema"])
    return rows


# ============================================================
# TABELLE DINAMICHE
# ============================================================

def _safe_table_name(run_id: str, node_id: str) -> str:
    """
    Genera nome tabella sicuro per SQL.
    Sostituisce caratteri non alfanumerici con underscore.
    """
    safe_run = re.sub(r"[^a-zA-Z0-9]", "_", run_id)
    safe_node = re.sub(r"[^a-zA-Z0-9]", "_", node_id)
    return f"run_{safe_run}_{safe_node}"


def _pandas_dtype_to_sql(dtype_str: str) -> str:
    """Converte dtype pandas in tipo SQLite."""
    dtype_str = str(dtype_str).lower()
    if "int" in dtype_str:
        return "INTEGER"
    if "float" in dtype_str:
        return "REAL"
    if "bool" in dtype_str:
        return "INTEGER"
    if "datetime" in dtype_str:
        return "TEXT"
    return "TEXT"


def create_node_table(run_id: str, node_id: str, col_schema: dict) -> str:
    """
    Crea tabella dinamica run_{run_id}_{node_id} con le colonne reali del DataFrame.
    col_schema: {"col_name": "dtype_str", ...} — da df.dtypes.astype(str).to_dict()
    Ritorna il nome tabella creato.
    """
    table_name = _safe_table_name(run_id, node_id)

    col_defs = ["_row_hash TEXT"]
    for col_name, dtype_str in col_schema.items():
        safe_col = re.sub(r"[^a-zA-Z0-9_]", "_", col_name)
        sql_type = _pandas_dtype_to_sql(dtype_str)
        col_defs.append(f'"{safe_col}" {sql_type}')

    ddl = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(col_defs)})"
    execute(ddl)
    return table_name


def insert_node_rows(run_id: str, node_id: str,
                     rows: List[Dict], col_schema: dict) -> None:
    """
    Bulk insert di tutte le righe nella tabella dinamica.
    rows: lista di dict con _row_hash già calcolato.
    """
    if not rows:
        return

    # FIX BUG-6: safety net — crea la tabella se non esiste già
    # (evita OperationalError: no such table in caso di race condition)
    create_node_table(run_id, node_id, col_schema)

    table_name = _safe_table_name(run_id, node_id)
    cols = ["_row_hash"] + [
        re.sub(r"[^a-zA-Z0-9_]", "_", c) for c in col_schema.keys()
    ]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(f'"{c}"' for c in cols)
    sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    def _to_sqlite(v):
        """Converte tipi non supportati da SQLite in tipi nativi.

        SQLite accetta nativamente bytes/bytearray come BLOB, non serve
        scartarli. Prima il codice ritornava None silenziosamente perdendo
        la colonna intera (silent data loss su DataFrame con BLOB).
        """
        if v is None:
            return None
        # pandas Timestamp / datetime / date
        if hasattr(v, "isoformat"):
            return v.isoformat()
        # decimal.Decimal
        import decimal
        if isinstance(v, decimal.Decimal):
            return float(v)
        # numpy / pandas scalar
        t = type(v).__name__
        if "int" in t:
            return int(v)
        if "float" in t:
            return float(v)
        if "bool" in t:
            return int(v)
        # bytes/bytearray → SQLite li gestisce come BLOB (nativo).
        # bytearray va convertito a bytes per il driver sqlite3.
        if isinstance(v, bytearray):
            return bytes(v)
        if isinstance(v, bytes):
            return v
        return v

    data = []
    for row in rows:
        safe_row = {re.sub(r"[^a-zA-Z0-9_]", "_", k): _to_sqlite(v) for k, v in row.items()}
        data.append(tuple(safe_row.get(c) for c in cols))

    # Usa pg_connection.executemany (pool-based)
    executemany(sql, data)


def drop_run_tables(run_id: str) -> int:
    """
    Elimina tutte le tabelle dinamiche di una run.
    Ritorna il numero di tabelle eliminate.

    Difesa in profondità: anche se i nomi vengono da sqlite_master (e quindi
    sono stati creati da `create_node_table` con `_safe_table_name`),
    rivalidiamo OGNI nome contro un pattern strict prima di interpolarlo nel
    DDL. Questo blocca eventuali tabelle create out-of-band con nomi malevoli
    o future regressioni del sanitizer.
    """
    safe_run = re.sub(r"[^a-zA-Z0-9]", "_", run_id)
    prefix = f"run_{safe_run}_"

    # Pattern strict per nomi tabella validi (lettere/numeri/underscore, max 200 char)
    valid_name = re.compile(r"^[a-zA-Z0-9_]{1,200}$")

    # PostgreSQL: query information_schema invece di sqlite_master
    tables = fetchall(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'plenora' AND table_name LIKE ?",
        (f"{prefix}%",)
    )

    count = 0
    for t in tables:
        table_name = t["table_name"]
        if not valid_name.match(table_name) or not table_name.startswith(prefix):
            logger.warning(
                f"drop_run_tables: nome tabella sospetto skippato: {table_name!r}"
            )
            continue
        execute(f"DROP TABLE IF EXISTS {table_name}")
        count += 1
    return count


def get_node_table_rows(run_id: str, node_id: str,
                        group_id: str, limit: int = None) -> List[Dict]:
    """
    Legge righe dalla tabella dinamica di un nodo.
    Verifica group_id tramite runs per sicurezza.
    """
    # Verifica ownership
    run = fetchone(
        "SELECT run_id FROM runs WHERE run_id = ? AND group_id = ?",
        (run_id, group_id)
    )
    if not run:
        return []

    table_name = _safe_table_name(run_id, node_id)

    # Verifica che la tabella esista (PostgreSQL information_schema)
    exists = fetchone(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'plenora' AND table_name = ?",
        (table_name,)
    )
    if not exists:
        return []

    sql = f"SELECT * FROM {table_name}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    return fetchall(sql)


# ============================================================
# RETENTION POLICY
# ============================================================

DEFAULT_RUNS_RETENTION = 5


def apply_runs_retention(canvas_id: str, group_id: str) -> None:
    """
    Applica retention FIFO sulle run del canvas.
    Conserva le N più recenti non pinnate, elimina le più vecchie.
    N viene letto da projects.retention_n (config.db), default DEFAULT_RUNS_RETENTION.

    Chiamare dopo ogni close_run.
    """
    try:
        # Legge retention_n dal progetto in config.db
        try:
            import app.db.config_db_service as cfg_db
            retention_n = cfg_db.get_project_retention_n(canvas_id)
            if retention_n is None:
                retention_n = DEFAULT_RUNS_RETENTION
        except Exception as e:
            logger.warning("apply_runs_retention: lettura retention_n fallita per %s, uso default: %s", canvas_id, e)
            retention_n = DEFAULT_RUNS_RETENTION

        # Lista run del canvas ordinate dalla più recente
        all_runs = list_runs(canvas_id, group_id, limit=1000)

        # Separa pinnate (escluse da retention) e non pinnate
        unpinned = [r for r in all_runs if not r.get("pinned")]

        if len(unpinned) <= retention_n:
            return  # nulla da eliminare

        # Le più vecchie sono in fondo (list_runs ordina DESC)
        to_delete = unpinned[retention_n:]
        for run in to_delete:
            _delete_run(run["run_id"])

        from app.utils.logger import get_logger
        get_logger(__name__).info(
            f"Retention intermediate: canvas={canvas_id} "
            f"conservate={retention_n} eliminate={len(to_delete)}",
            op="retention"
        )

    except Exception as e:
        from app.utils.logger import get_logger
        get_logger(__name__).warning(
            f"Errore retention intermediate ({canvas_id}): {e}",
            op="retention"
        )


def _delete_run(run_id: str) -> None:
    """Elimina una run: tabelle dinamiche + node_meta + riga in runs."""
    drop_run_tables(run_id)
    execute("DELETE FROM node_meta WHERE run_id = ?", (run_id,))
    execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def delete_all_runs_for_canvas(canvas_id: str) -> int:
    """
    Elimina tutte le run di un canvas (chiamata da delete_project).
    Rimuove tabelle dinamiche, node_meta e righe in runs.
    Ritorna il numero di run eliminate.
    """
    try:
        rows = fetchall(
            "SELECT run_id FROM runs WHERE canvas_id = ?",
            (canvas_id,)
        )
        if not rows:
            return 0
        count = 0
        for row in rows:
            run_id = row["run_id"] if isinstance(row, dict) else row[0]
            try:
                _delete_run(run_id)
                count += 1
            except Exception as e:
                logger.warning(
                    f"delete_all_runs_for_canvas: errore su run {run_id}: {e}",
                    op="delete_project"
                )
        logger.info(
            f"delete_all_runs_for_canvas: canvas={canvas_id} → {count} run eliminate",
            op="delete_project"
        )
        return count
    except Exception as e:
        logger.warning(
            f"delete_all_runs_for_canvas: errore canvas={canvas_id}: {e}",
            op="delete_project"
        )
        return 0
