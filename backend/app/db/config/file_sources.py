"""
File sources DAL — estratto da `config_db_service.py` (scope micro Batch G #9,
invariants §6 commit `093d22b`).

Cluster `file_sources` (5 funzioni): CRUD sulla tabella `file_sources` con
dispatch FS-B1-large (DB blob vs S3 storage_key, ratifica ADR-0011).
`delete_file_source` include cleanup S3 best-effort via import lazy di
`get_object_storage` dal `cloud_backends` per rispettare §1.7 (entry point
canonico object storage).

Nota logger attribution: post-estrazione il logger name è
`app.db.config.file_sources` (vs legacy `app.db.config_db_service`).
Observable non-breaking (0 test verificano il name).
"""

import json
from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_file_source(file_id: str, include_data: bool = False, group_id: str = None) -> Optional[Dict]:
    """Recupera file source. group_id=None → no filter (backward compat)."""
    gid_clause = " AND group_id = ?" if group_id else ""
    gid_params = (group_id,) if group_id else ()
    if include_data:
        row = fetchone(
            f"SELECT * FROM file_sources WHERE id = ?{gid_clause}",
            (file_id,) + gid_params
        )
    else:
        row = fetchone(
            f"SELECT id, group_id, name, original_filename, format, size_bytes, metadata, created_at "
            f"FROM file_sources WHERE id = ?{gid_clause}",
            (file_id,) + gid_params
        )
    if row and row.get("metadata"):
        row["metadata"] = json.loads(row["metadata"])
    return row


def list_file_sources(group_id: Optional[str] = None) -> List[Dict]:
    """Lista file_sources. group_id=None → no filter (usato da superadmin)."""
    if group_id is None:
        return fetchall(
            "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources ORDER BY created_at DESC"
        )
    return fetchall(
        "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )


def save_file_source(file_id: str, group_id: str, name: str, original_filename: str,
                     fmt: str, data: Optional[bytes] = None,
                     metadata: dict = None, created_by: str = None,
                     storage_key: Optional[str] = None,
                     size_bytes: Optional[int] = None) -> Dict:
    """Salva/aggiorna file_source.

    Dispatch FS-B1-large:
      - `data` + `size_bytes=len(data)` + `storage_key=None` → blob in DB (BYTEA)
      - `data=None` + `storage_key="file-sources/..."` + `size_bytes=N` → blob
        su S3, la row tiene solo metadata + puntatore.

    Almeno uno tra `data` e `storage_key` deve essere non-None (check
    app-level, il DB non lo enforce perche' la migration rilassa NOT NULL
    su data).
    """
    from datetime import datetime, timezone
    if data is None and not storage_key:
        raise ValueError(
            "save_file_source: serve almeno 'data' o 'storage_key' "
            "(non entrambi None)"
        )
    now = datetime.now(timezone.utc).isoformat()
    effective_size = size_bytes if size_bytes is not None else (len(data) if data else 0)
    execute(
        "INSERT INTO file_sources (id, group_id, name, original_filename, format, data, storage_key, size_bytes, metadata, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, original_filename=EXCLUDED.original_filename, format=EXCLUDED.format, "
        "data=EXCLUDED.data, storage_key=EXCLUDED.storage_key, size_bytes=EXCLUDED.size_bytes, "
        "metadata=EXCLUDED.metadata, updated_at=EXCLUDED.updated_at",
        (file_id, group_id, name, original_filename, fmt, data, storage_key,
         effective_size, json.dumps(metadata or {}), now, now, created_by)
    )
    return get_file_source(file_id)


def update_file_source_metadata(file_id: str, metadata: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE file_sources SET metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(metadata), now, file_id)
    )


def delete_file_source(file_id: str) -> bool:
    """Elimina file_source dal DB + cleanup S3 se blob era S3-backed (FS-B1-large).

    Ordine: S3 prima (best-effort, log su warn se fallisce), poi DB.
    Se S3 fallisce ma DB no, l'orfano resta sul bucket — meglio la row
    DB via che un fantom. Cleanup manuale o lifecycle rule possono
    raccogliere gli orfani.
    """
    # Leggi storage_key prima del delete (ci serve per cleanup S3)
    row = fetchone(
        "SELECT storage_key FROM file_sources WHERE id = ?", (file_id,)
    )
    storage_key = row.get("storage_key") if row else None

    if storage_key:
        try:
            from app.shared.cloud_backends import get_object_storage
            storage = get_object_storage()
            storage.delete(storage_key)
        except Exception as e:
            logger.warning(
                f"delete_file_source: cleanup S3 fallito per "
                f"{file_id} key={storage_key}: {e}"
            )

    cur = execute("DELETE FROM file_sources WHERE id = ?", (file_id,))
    return cur.rowcount > 0
