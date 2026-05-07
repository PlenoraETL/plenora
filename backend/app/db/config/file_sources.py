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
from typing import Any, Dict, List, Optional, TypedDict

from app.db.pg_connection import fetchone, fetchall, execute, transaction
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FileSourceRow(TypedDict, total=False):
    """Riga tipizzata per la persistenza di un FileSource (BE-SHAPE-001).

    Shape unica usata sia da `save_file_source` (single-row) che da
    `save_file_sources_batch` (batch atomic). Tutte le chiavi sono
    dichiarate `total=False` per consentire ai caller di omettere i
    campi opzionali; la presenza degli obbligatori e' verificata da
    `_validate_row` prima di qualunque side effect DB.

    Chiavi obbligatorie:
        file_id, group_id, name, original_filename, fmt
    Chiavi opzionali (con default):
        data         (Optional[bytes])    blob in BYTEA path
        storage_key  (Optional[str])      key in object_storage path
        size_bytes   (int)                derivato da len(data) se assente
        metadata     (dict)               serializzato a JSON dentro la fn
        created_by   (Optional[str])

    Invariante app-level: ALMENO uno tra `data` e `storage_key` deve
    essere non-None (il DB non lo enforce).

    Go-ready mapping:
        type FileSourceRow struct {
            FileID, GroupID, Name, OriginalFilename, Format string
            Data       []byte
            StorageKey *string
            SizeBytes  int64
            Metadata   map[string]any
            CreatedBy  *string
        }
        Repository.Save(ctx, FileSourceRow)         error
        Repository.SaveBatch(ctx, []FileSourceRow)  error
    """

    file_id: str
    group_id: str
    name: str
    original_filename: str
    fmt: str
    data: Optional[bytes]
    storage_key: Optional[str]
    size_bytes: int
    metadata: Dict[str, Any]
    created_by: Optional[str]


def _validate_row(row: Dict[str, Any], *, context: str) -> None:
    """Valida l'invariante "data o storage_key non-None" su una row.

    Usato da `save_file_source` e `save_file_sources_batch` per
    centralizzare il check. Solleva `ValueError` con context-tagged
    message; nessun side effect.

    Args:
        row:     dict-shape FileSourceRow.
        context: tag prefix per il messaggio di errore (nome funzione
                 caller, es. "save_file_source" o "save_file_sources_batch").
    """
    if row.get("data") is None and not row.get("storage_key"):
        raise ValueError(
            f"{context}: row file_id={row.get('file_id')!r} "
            f"senza data ne storage_key (almeno uno richiesto)"
        )


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
    _validate_row(
        {"file_id": file_id, "data": data, "storage_key": storage_key},
        context="save_file_source",
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


def save_file_sources_batch(rows: List[FileSourceRow]) -> None:
    """Salva N file_sources in UNA SOLA transazione (BE-HARDEN-001C E3).

    All-or-nothing: se l'INSERT fallisce per qualsiasi row, la transazione
    fa rollback e zero row vengono persistite. Closes MULTI-ATOMIC-001
    per il path multi-layer upload.

    Args:
        rows: lista di dict con shape esplicita per ogni file_source da
              creare. Chiavi richieste:
                - file_id (str)
                - group_id (str)
                - name (str)
                - original_filename (str)
                - fmt (str)
                - data (Optional[bytes])         shared blob in BYTEA path
                - storage_key (Optional[str])    shared key in S3 path
                - size_bytes (int)
                - metadata (dict)                serialized to JSON dentro
                - created_by (Optional[str])

    Pre-transaction validation (fail-fast, zero side effect DB):
        Per ogni row almeno uno tra `data` e `storage_key` deve essere
        non-None (mirror del check di save_file_source single-row).
        ValueError -> nessuna connessione DB acquisita.

    Returns:
        None. Il caller possiede gia' i DTO costruiti client-side; nessuna
        SELECT post-insert (risparmio N round-trip rispetto a save_file_source
        legacy).

    Go-ready boundary:
        FileSourceRepository.SaveBatch(ctx, []FileSourceRow) error
        - input shape esplicita (no kwargs);
        - errori espliciti via raise;
        - transaction boundary visibile (un solo `with transaction()`).
    """
    from datetime import datetime, timezone
    if not rows:
        return

    for r in rows:
        _validate_row(r, context="save_file_sources_batch")

    now = datetime.now(timezone.utc).isoformat()
    sql = (
        "INSERT INTO file_sources "
        "(id, group_id, name, original_filename, format, data, storage_key, "
        "size_bytes, metadata, created_at, updated_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, original_filename=EXCLUDED.original_filename, "
        "format=EXCLUDED.format, data=EXCLUDED.data, "
        "storage_key=EXCLUDED.storage_key, size_bytes=EXCLUDED.size_bytes, "
        "metadata=EXCLUDED.metadata, updated_at=EXCLUDED.updated_at"
    )
    params_list = [
        (
            r["file_id"], r["group_id"], r["name"], r["original_filename"],
            r["fmt"], r.get("data"), r.get("storage_key"),
            r.get("size_bytes") if r.get("size_bytes") is not None
            else (len(r["data"]) if r.get("data") else 0),
            json.dumps(r.get("metadata") or {}),
            now, now, r.get("created_by"),
        )
        for r in rows
    ]

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)


def update_file_source_metadata(file_id: str, metadata: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE file_sources SET metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(metadata), now, file_id)
    )


def delete_file_source(file_id: str) -> bool:
    """Elimina file_source dal DB + cleanup S3 se blob era S3-backed (FS-B1-large).

    Flow (BE-HARDEN-001C E4 / BE-DELETE-002):

      1. Apre `transaction()` (single conn, autocommit off).
      2. `SELECT storage_key WHERE id=? FOR UPDATE` - lock pessimistico
         sulla row da cancellare.
      3. Se `storage_key` valorizzato:
         `SELECT id WHERE storage_key=? AND id<>? FOR UPDATE` - lock
         pessimistico anche sui sibling che condividono la key.
         Se almeno 1 sibling esiste -> skip storage cleanup + warning
         strutturato (`event=delete_skip_storage_shared_ref`).
         Se nessun sibling -> ricorda `storage_key` per cleanup
         post-commit.
      4. `DELETE FROM file_sources WHERE id=?` dentro la transazione.
      5. Commit (uscita dal CM).
      6. Cleanup S3 SOLO se step 3 ha decretato "ultimo ref"
         (best-effort, fuori transazione, errore loggato senza
         alterare la row gia' cancellata -> ADR-0011 contract preserved).

    Properties post-E4:
      - **Race A.2 (concurrent shared delete)**: serializzata. Le N delete
        sequenzialmente acquisiscono il lock; l'ultimo vede refcount=0
        e fa cleanup. **Zero orphan** sul caso 2-sibling.
      - **Race D (DB fail dopo storage call)**: ELIMINATA. La storage call
        avviene dopo il commit, quindi un fail DB non lascia mai una row
        che punta a un blob gia' eliminato.
      - **Race C (storage fail post-commit)**: invariata vs ADR-0011
        ("orphan resta sul bucket, row DB cancellata").
      - **Race E (capability off mid-delete)**: invariata, debug log.

    Backward-compat: BYTEA mode (`storage_key=None`) salta la sibling
    query per costruzione; comportamento singolo-row invariato.

    Schema dependency: usa l'indice parziale `idx_file_sources_storage_key`
    introdotto contestualmente (`config_schema.sql`) per evitare seq scan
    sotto FOR UPDATE.
    """
    storage_key_to_clean = None
    deleted = False
    # BE-GO-READY-STORAGE-ENGINE-005: reconciliation entry handle.
    # Set only when we are about to commit a "last reference" delete
    # that triggers post-commit storage cleanup. Stays None on the
    # shared-blob path (siblings exist) and on the BYTEA path
    # (storage_key None) — neither needs reconciliation.
    reconciliation_ref = None

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_key FROM file_sources WHERE id = %s FOR UPDATE",
                (file_id,),
            )
            row = cur.fetchone()
            storage_key = row["storage_key"] if row else None

            if storage_key:
                cur.execute(
                    "SELECT id FROM file_sources "
                    "WHERE storage_key = %s AND id <> %s FOR UPDATE",
                    (storage_key, file_id),
                )
                siblings = cur.fetchall()
                remaining_refs = len(siblings)

                if remaining_refs > 0:
                    logger.warning(
                        f"event=delete_skip_storage_shared_ref "
                        f"file_id={file_id} storage_key={storage_key!r} "
                        f"remaining_refs={remaining_refs}"
                    )
                else:
                    storage_key_to_clean = storage_key
                    # BE-GO-READY-STORAGE-ENGINE-005: record cleanup
                    # intent inside the same transaction as the
                    # DELETE. Commit advances both atomically; if a
                    # process death follows the commit before the
                    # post-commit storage call, the pending row is
                    # the only application-side trace of the orphan.
                    from app.shared.storage_reconciliation import (
                        record_pending,
                    )
                    reconciliation_ref = record_pending(
                        cur,
                        domain="file_source",
                        storage_key=storage_key,
                        operation="delete",
                        reason="row_delete_post_commit",
                        extras={"file_id": file_id},
                    )

            cur.execute(
                "DELETE FROM file_sources WHERE id = %s", (file_id,)
            )
            deleted = cur.rowcount > 0

    # Storage cleanup AFTER DB commit (ADR-0011 contract: orphan accepted
    # on storage failure, DB row stays deleted).
    # BE-GO-READY-STORAGE-ENGINE-002: delegato all'helper unificato.
    # BE-GO-READY-STORAGE-ENGINE-005: outcome -> reconciliation transition.
    if storage_key_to_clean:
        from app.shared.storage_compensation import compensating_delete
        outcome = compensating_delete(
            storage_key_to_clean,
            domain="file_source",
            reason="row_delete_post_commit",
            extras={"file_id": file_id},
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
                mark_failed_attempt(
                    reconciliation_ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted
