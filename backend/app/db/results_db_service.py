"""
Results DB Service — accesso a SQLite #2 (results.db).

Contiene: results (output pipeline), execution_logs.
Dati in crescita — soggetti a retention policy.

Thread-safe tramite threading.local() per le connessioni.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(settings.DATA_DIR) / "results.db"

_local = threading.local()


@contextmanager
def transaction():
    conn = get_connection()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_connection() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            str(DB_PATH),
            timeout=30,
            check_same_thread=False,
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).parent / "results_schema.sql"
    conn = get_connection()
    if schema_path.exists():
        schema = schema_path.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.commit()
        logger.info(f"Results DB inizializzato: {DB_PATH}", op="init_db")
    else:
        logger.warning("results_schema.sql non trovato", op="init_db")


# ============================================================
# HELPERS
# ============================================================

def _row_to_dict(row: sqlite3.Row) -> Dict:
    return dict(row) if row else {}


def fetchone(sql: str, params: tuple = ()) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def fetchall(sql: str, params: tuple = ()) -> List[Dict]:
    conn = get_connection()
    cur = conn.execute(sql, params)
    return [_row_to_dict(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


# ============================================================
# RESULTS
# ============================================================

def save_result(result_id: str, project_id: str, group_id: str,
                filename: str, fmt: str, data: bytes,
                rows_count: int = None, created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO results (id, project_id, group_id, filename, format, data, size_bytes, rows_count, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (result_id, project_id, group_id, filename, fmt, data, len(data), rows_count, now, created_by)
    )
    return get_result(result_id)


def get_result(result_id: str) -> Optional[Dict]:
    return fetchone(
        "SELECT id, project_id, group_id, filename, format, size_bytes, rows_count, created_at FROM results WHERE id = ?",
        (result_id,)
    )


def get_result_data(result_id: str, group_id: str = None) -> Optional[bytes]:
    """Recupera blob risultato. group_id=None → no filter (backward compat)."""
    if group_id:
        row = fetchone(
            "SELECT data FROM results WHERE id = ? AND group_id = ?",
            (result_id, group_id)
        )
    else:
        row = fetchone("SELECT data FROM results WHERE id = ?", (result_id,))
    return row["data"] if row else None


def get_latest_result(project_id: str, group_id: str = None) -> Optional[Dict]:
    """Recupera ultimo risultato per progetto. group_id=None → no filter."""
    if group_id:
        return fetchone(
            "SELECT id, project_id, group_id, filename, format, size_bytes, rows_count, created_at "
            "FROM results WHERE project_id = ? AND group_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id, group_id)
        )
    return fetchone(
        "SELECT id, project_id, group_id, filename, format, size_bytes, rows_count, created_at "
        "FROM results WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
        (project_id,)
    )


def list_results(project_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, project_id, filename, format, size_bytes, rows_count, created_at FROM results WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,)
    )


def delete_result(result_id: str) -> bool:
    cur = execute("DELETE FROM results WHERE id = ?", (result_id,))
    return cur.rowcount > 0


def delete_results_for_project(project_id: str) -> int:
    """Elimina tutti i risultati di un progetto. Ritorna il numero di righe eliminate."""
    cur = execute("DELETE FROM results WHERE project_id = ?", (project_id,))
    return cur.rowcount


# ============================================================
# EXECUTION LOGS
# ============================================================

def log_execution(log_id: str, group_id: str, project_id: str = None,
                  schedule_id: str = None, status: str = "running",
                  triggered_by: str = "manual") -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO execution_logs (id, group_id, project_id, schedule_id, status, started_at, triggered_by) VALUES (?,?,?,?,?,?,?)",
        (log_id, group_id, project_id, schedule_id, status, now, triggered_by)
    )
    return fetchone("SELECT * FROM execution_logs WHERE id = ?", (log_id,))


def update_log(log_id: str, status: str, rows_count: int = None,
               message: str = None, error: str = None) -> None:
    from datetime import datetime, timezone, datetime as dt
    now = datetime.now(timezone.utc).isoformat()
    row = fetchone("SELECT started_at FROM execution_logs WHERE id = ?", (log_id,))
    duration = None
    if row:
        try:
            start = dt.fromisoformat(row["started_at"])
            end = dt.fromisoformat(now)
            duration = (end - start).total_seconds()
        except Exception:
            pass
    execute(
        "UPDATE execution_logs SET status=?, finished_at=?, duration_s=?, rows_count=?, message=?, error=? WHERE id=?",
        (status, now, duration, rows_count, message, error, log_id)
    )


def list_logs(group_id: str, project_id: str = None, limit: int = 100) -> List[Dict]:
    if project_id:
        return fetchall(
            "SELECT * FROM execution_logs WHERE group_id = ? AND project_id = ? ORDER BY started_at DESC LIMIT ?",
            (group_id, project_id, limit)
        )
    return fetchall(
        "SELECT * FROM execution_logs WHERE group_id = ? ORDER BY started_at DESC LIMIT ?",
        (group_id, limit)
    )
