"""
Results DB Service — accesso a PostgreSQL (ex SQLite results.db).

Contiene: results (output pipeline), execution_logs.
Migrato a PostgreSQL: usa pg_connection pool.
"""

import time
from typing import Dict, List, Optional
from app.utils.logger import get_logger


def _short_key(storage_key: Optional[str], max_len: int = 60) -> str:
    """Abbrevia una storage_key per log: prime 12 + '...' + ultimi 30 char.
    Evita di loggare path interi che possano contenere project_id/result_id
    sensibili o filenames lunghi rumorosi."""
    if not storage_key:
        return "<none>"
    if len(storage_key) <= max_len:
        return storage_key
    return f"{storage_key[:12]}...{storage_key[-30:]}"

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
    # RB.#19 Step A: timing log per profilare hot path source loading.
    # Misura il roundtrip S3 GET + concat chunks.
    _t0 = time.monotonic()
    try:
        # Lazy import per non forzare cloud_backends in ogni path
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
        )
        try:
            storage = get_runtime_object_storage()
        except ObjectStorageDisabledError as _disabled:
            # Batch 1.5: capability off -> non possiamo scaricare nulla.
            # Il caller (get_result_data) tratta None come "non disponibile".
            duration_ms = round((time.monotonic() - _t0) * 1000, 1)
            logger.warning(
                f"[FS-4] S3 download skip su {_short_key(storage_key)}: "
                f"object storage disabilitato (duration_ms={duration_ms}). {_disabled}"
            )
            return None
        stream = storage.get_object_stream(storage_key)
        chunks = list(stream["body"])
        data = b"".join(chunks)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] s3_download op=get_bytes storage_key={_short_key(storage_key)} "
            f"size_bytes={len(data)} duration_ms={duration_ms}"
        )
        return data
    except Exception as e:
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        logger.error(
            f"[FS-4] S3 download {_short_key(storage_key)} fallito "
            f"(duration_ms={duration_ms}): {e}",
            exc_info=True,
        )
        return None


def get_result_data(result_id: str, group_id: str = None) -> Optional[bytes]:
    """Recupera blob risultato. group_id=None → no filter (backward compat).

    FS-4: se `storage_key` e' presente sulla row, scarica da S3 invece
    che leggere BYTEA. Semantica caller-side invariata (bytes in memory).
    """
    # RB.#19 Step A: timing log per separare DB lookup da S3 download.
    _t0 = time.monotonic()
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
        # _s3_download_bytes logga il proprio timing dettagliato; qui
        # logghiamo il tempo totale (DB lookup + object storage fetch).
        data = _s3_download_bytes(storage_key)
        duration_ms = round((time.monotonic() - _t0) * 1000, 1)
        size = len(data) if data else 0
        logger.info(
            f"[PERF] get_result_data op=fetch_blob backend=object_storage "
            f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
        )
        return data

    # Fallback BYTEA: row legacy pre-migration object storage. Mantenuto
    # per retro-compatibilita' (storage_key=NULL + data BYTEA presente).
    # backend=db_legacy distingue dal path nuovo (sempre object_storage
    # quando STORAGE_POLICY=object_storage).
    data = row.get("data")
    # PostgreSQL BYTEA: psycopg2 ritorna memoryview, convertiamo a bytes
    if isinstance(data, memoryview):
        data = bytes(data)
    duration_ms = round((time.monotonic() - _t0) * 1000, 1)
    size = len(data) if data else 0
    logger.info(
        f"[PERF] get_result_data op=fetch_blob backend=db_legacy "
        f"result_id={result_id} size_bytes={size} duration_ms={duration_ms}"
    )
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
    """Elimina result. Se S3-backed, pulisce anche l'oggetto S3 (best-effort).

    Flow (BE-GO-READY-STORAGE-ENGINE-007 — W1 race coverage):

      1. `transaction()`: SELECT storage_key + DELETE row + (se
         S3-backed) `record_pending` ⇒ tutto atomico.
      2. Commit (uscita CM): row DB sparita + entry pending nel
         reconciliation log.
      3. `compensating_delete` (best-effort, fuori transazione).
      4. Outcome:
         - `ok`/`skipped` ⇒ `mark_done(reconciliation_ref.id)`;
         - `failed` ⇒ `mark_failed_attempt(...)` ⇒ il reconciler
           beat hook (BE-GO-READY-STORAGE-ENGINE-006) re-tenterà
           secondo backoff esponenziale, fino a `mark_lost`.

    ADR-0011 contract preservato: storage failure NON propaga; la
    row DB resta cancellata. La differenza vs pre-batch 007 e' che
    l'orphan e' ora **tracked** + retry-able.

    Backward-compat: row con `storage_key=None` (BYTEA legacy) salta
    `record_pending` e il post-commit cleanup — comportamento
    invariato.
    """
    storage_key_to_clean = None
    deleted = False
    reconciliation_ref = None

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_key FROM results WHERE id = %s FOR UPDATE",
                (result_id,),
            )
            row = cur.fetchone()
            storage_key = row["storage_key"] if row else None

            if storage_key:
                storage_key_to_clean = storage_key
                # BE-GO-READY-STORAGE-ENGINE-007: record cleanup intent
                # inside the same transaction as the DELETE. Commit
                # advances both atomically; if a process death follows
                # the commit before the post-commit storage call, the
                # pending row is the only application-side trace of
                # the orphan. Order canonical W1 (allineato a
                # `delete_file_source`): record_pending PRECEDE DELETE.
                from app.shared.storage_reconciliation import (
                    record_pending,
                )
                reconciliation_ref = record_pending(
                    cur,
                    domain="result_blob",
                    storage_key=storage_key,
                    operation="delete",
                    reason="row_delete_post_commit",
                    extras={"result_id": result_id},
                )

            cur.execute("DELETE FROM results WHERE id = %s", (result_id,))
            deleted = cur.rowcount > 0

    if deleted and storage_key_to_clean:
        from app.shared.storage_compensation import compensating_delete
        outcome = compensating_delete(
            storage_key_to_clean,
            domain="result_blob",
            reason="row_delete_post_commit",
            extras={"result_id": result_id},
            logger=logger,
        )
        if reconciliation_ref is not None:
            from app.shared.storage_reconciliation import (
                mark_done,
                mark_failed_attempt,
            )
            if outcome in ("ok", "skipped"):
                mark_done(reconciliation_ref.id)
            else:
                # outcome == "failed": ADR-0011 posture preserved
                # (orphan accepted, DB row stays deleted), but now
                # persisted for the reconciler beat hook (batch 006).
                mark_failed_attempt(
                    reconciliation_ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted


def delete_results_for_project(project_id: str) -> int:
    """Elimina tutti i risultati di un progetto. Ritorna il numero di
    righe eliminate.

    Flow (BE-GO-READY-STORAGE-ENGINE-007): pattern bulk W1.
      1. Pre-fetch `(id, storage_key)` di tutti gli S3-backed.
      2. `transaction()`: per ognuno `record_pending` + DELETE row.
      3. Post-commit: per ogni reconciliation_ref, `compensating_delete`
         + `mark_done`/`mark_failed_attempt` per outcome.

    L'INSERT N pending + DELETE all WHERE project_id=? e' atomico: o
    si committano TUTTE le pending entries con la cancellazione, o
    nessuna (rollback). Cosi' il reconciler beat hook puo' coprire
    la finestra "DB committed → process death → S3 orphan" anche
    sulla bulk path.

    ADR-0011 preservato: storage failure NON propaga. Il caller
    riceve sempre il `rowcount` finale.
    """
    # Pre-fetch storage_keys + result_id prima del DELETE cascata.
    rows = [
        r for r in fetchall(
            "SELECT id, storage_key FROM results "
            "WHERE project_id = ? AND storage_key IS NOT NULL",
            (project_id,),
        )
        if r.get("storage_key")
    ]

    deleted_count = 0
    # (storage_key, result_id, reconciliation_id) per il post-commit.
    pending_cleanups: List[tuple] = []

    with transaction() as conn:
        with conn.cursor() as cur:
            if rows:
                from app.shared.storage_reconciliation import (
                    record_pending,
                )
                for r in rows:
                    ref = record_pending(
                        cur,
                        domain="result_blob",
                        storage_key=r["storage_key"],
                        operation="delete",
                        reason="row_delete_post_commit",
                        extras={
                            "result_id": r["id"],
                            "project_id": project_id,
                        },
                    )
                    pending_cleanups.append(
                        (r["storage_key"], r["id"], ref.id)
                    )

            cur.execute(
                "DELETE FROM results WHERE project_id = %s", (project_id,)
            )
            deleted_count = cur.rowcount

    # Cleanup S3 dopo DELETE ok (anche se uno fallisce, altri provano).
    if pending_cleanups:
        from app.shared.storage_compensation import compensating_delete
        from app.shared.storage_reconciliation import (
            mark_done,
            mark_failed_attempt,
        )
        for storage_key, result_id, reconciliation_id in pending_cleanups:
            outcome = compensating_delete(
                storage_key,
                domain="result_blob",
                reason="row_delete_post_commit",
                extras={
                    "result_id": result_id,
                    "project_id": project_id,
                },
                logger=logger,
            )
            if outcome in ("ok", "skipped"):
                mark_done(reconciliation_id)
            else:
                mark_failed_attempt(
                    reconciliation_id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted_count


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
