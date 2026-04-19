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
                filename: str, fmt: str, data: Optional[bytes] = None,
                rows_count: int = None, created_by: str = None,
                storage_key: Optional[str] = None,
                size_bytes: Optional[int] = None) -> Dict:
    """Salva metadata result. Due mode:
      - DB blob: data=bytes, storage_key=None (size_bytes derivato)
      - S3-backed: data=None, storage_key='results/...', size_bytes esplicito
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    # size_bytes: esplicito se S3 (caller lo conosce), altrimenti da len(data)
    if size_bytes is None:
        size_bytes = len(data) if data else 0

    execute(
        "INSERT INTO results (id, project_id, group_id, filename, format, data, "
        "size_bytes, rows_count, created_at, created_by, storage_key) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, size_bytes=EXCLUDED.size_bytes, "
        "rows_count=EXCLUDED.rows_count, created_at=EXCLUDED.created_at, "
        "storage_key=EXCLUDED.storage_key",
        (result_id, project_id, group_id, filename, fmt,
         data, size_bytes, rows_count, now, created_by, storage_key)
    )
    return get_result(result_id)


def get_result(result_id: str) -> Optional[Dict]:
    return fetchone(
        "SELECT id, project_id, group_id, filename, format, size_bytes, "
        "rows_count, created_at, storage_key "
        "FROM results WHERE id = ?",
        (result_id,)
    )


def _s3_download_bytes(storage_key: str) -> Optional[bytes]:
    """Scarica un oggetto S3-backed come bytes. None su errore loggato."""
    try:
        # Lazy import per non forzare cloud_backends in ogni path
        from app.shared.cloud_backends import get_object_storage
        storage = get_object_storage()
        stream = storage.get_object_stream(storage_key)
        chunks = list(stream["body"])
        return b"".join(chunks)
    except Exception as e:
        logger.error(f"[FS-4] S3 download {storage_key} fallito: {e}")
        return None


def get_result_data(result_id: str, group_id: str = None) -> Optional[bytes]:
    """Recupera blob risultato. group_id=None → no filter (backward compat).

    FS-4: se `storage_key` e' presente sulla row, scarica da S3 invece
    che leggere BYTEA. Semantica caller-side invariata (bytes in memory).
    """
    if group_id:
        row = fetchone(
            "SELECT data, storage_key FROM results WHERE id = ? AND group_id = ?",
            (result_id, group_id)
        )
    else:
        row = fetchone(
            "SELECT data, storage_key FROM results WHERE id = ?",
            (result_id,),
        )
    if not row:
        return None

    storage_key = row.get("storage_key")
    if storage_key:
        return _s3_download_bytes(storage_key)

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


def _s3_delete_best_effort(storage_key: str) -> None:
    """Cleanup S3 object post-DELETE. Best-effort: errori loggati ma non
    sollevati (il record DB e' gia' andato, orfano S3 e' meno grave di
    fallire un delete che l'utente ha richiesto)."""
    try:
        from app.shared.cloud_backends import get_object_storage
        storage = get_object_storage()
        storage.delete(storage_key)
    except Exception as e:
        logger.warning(f"[FS-4] S3 delete {storage_key} fallito (non-fatal): {e}")


def delete_result(result_id: str) -> bool:
    """Elimina result. Se S3-backed, pulisce anche l'oggetto S3 (best-effort)."""
    # Prima leggo il record per sapere se e' S3-backed.
    row = fetchone(
        "SELECT storage_key FROM results WHERE id = ?",
        (result_id,),
    )
    cur = execute("DELETE FROM results WHERE id = ?", (result_id,))
    deleted = cur.rowcount > 0
    if deleted and row and row.get("storage_key"):
        _s3_delete_best_effort(row["storage_key"])
    return deleted


def delete_results_for_project(project_id: str) -> int:
    """Elimina tutti i risultati di un progetto. Ritorna il numero di righe eliminate.

    FS-4: pulisce anche gli oggetti S3 dei record S3-backed (best-effort).
    """
    # Pre-fetch storage_keys prima del DELETE cascata.
    s3_keys = [
        r["storage_key"]
        for r in fetchall(
            "SELECT storage_key FROM results "
            "WHERE project_id = ? AND storage_key IS NOT NULL",
            (project_id,),
        )
        if r.get("storage_key")
    ]
    cur = execute("DELETE FROM results WHERE project_id = ?", (project_id,))
    # Cleanup S3 dopo DELETE ok (anche se uno fallisce, altri provano)
    for key in s3_keys:
        _s3_delete_best_effort(key)
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
    # Hard cap per evitare full scan/OOM se il caller non passa un limite sensato
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
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
