"""
Results DB Service — accesso a PostgreSQL (ex SQLite results.db).

Contiene: results (output pipeline), execution_logs.
Migrato a PostgreSQL: usa pg_connection pool.
"""

from typing import Dict, List, Optional
from app.utils.logger import get_logger

# Importa tutto da pg_connection
from app.db.pg_connection import (
    fetchone, fetchall, execute,
    transaction, close_connection,
)

logger = get_logger(__name__)


def init_db():
    """No-op per PostgreSQL: tabelle già create."""
    pass


# ============================================================
# RESULTS
# ============================================================

def save_result(result_id: str, project_id: str, group_id: str,
                filename: str, fmt: str, data: bytes,
                rows_count: int = None, created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO results (id, project_id, group_id, filename, format, data, "
        "size_bytes, rows_count, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, size_bytes=EXCLUDED.size_bytes, "
        "rows_count=EXCLUDED.rows_count, created_at=EXCLUDED.created_at",
        (result_id, project_id, group_id, filename, fmt,
         data, len(data) if data else 0, rows_count, now, created_by)
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
    if not row:
        return None
    data = row.get("data")
    # PostgreSQL BYTEA: psycopg2 ritorna memoryview, convertiamo a bytes
    if isinstance(data, memoryview):
        return bytes(data)
    return data


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
        "SELECT id, project_id, filename, format, size_bytes, rows_count, created_at "
        "FROM results WHERE project_id = ? ORDER BY created_at DESC",
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
                  message: str = None, triggered_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO execution_logs (id, group_id, project_id, schedule_id, status, "
        "started_at, message, triggered_by) VALUES (?,?,?,?,?,?,?,?)",
        (log_id, group_id, project_id, schedule_id, status, now, message, triggered_by)
    )
    return {"id": log_id, "status": status}


def update_log(log_id: str, status: str, finished_at: str = None,
               duration_s: float = None, rows_count: int = None,
               message: str = None, error: str = None) -> None:
    from datetime import datetime, timezone
    finished = finished_at or datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE execution_logs SET status=?, finished_at=?, duration_s=?, "
        "rows_count=?, message=?, error=? WHERE id=?",
        (status, finished, duration_s, rows_count, message, error, log_id)
    )


def list_logs(project_id: str = None, group_id: str = None,
              limit: int = 50, offset: int = 0) -> List[Dict]:
    conditions = []
    params = []
    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if group_id:
        conditions.append("group_id = ?")
        params.append(group_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetchall(
        f"SELECT * FROM execution_logs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset)
    )
