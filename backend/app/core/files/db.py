"""Domain-owned file-source DB helpers."""

from datetime import datetime, timezone

import json
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from app.core.resources import storage_reconciliation as reconciliation
from app.db.pg_connection import unit_of_work
from app.infrastructure.storage import compensation as storage_compensation
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FileSourceRow(TypedDict, total=False):
    """Riga tipizzata per la persistenza di un FileSource (BE-SHAPE-001).

    Shape unica usata sia da `save_file_source` (single-row) che da
    `save_file_sources_batch` . Tutte le chiavi sono
    dichiarate `total=False` per consentire ai caller di omettere i
    campi opzionali; la presenza degli obbligatori e' verificata da
    `_validate_row` prima di qualunque side effect DB.

    Chiavi obbligatorie:
        file_id, group_id, name, original_filename, fmt
    Chiavi opzionali (con default):
        data (Optional[bytes]) blob in BYTEA path
        storage_key (Optional[str]) key in object_storage path
        size_bytes (int) derivato da len(data) se assente
        metadata (dict) serializzato a JSON dentro la fn
        created_by (Optional[str])

    Invariante app-level: ALMENO uno tra `data` e `storage_key` deve
    essere non-None (il DB non lo enforce).

    portable mapping:
        type FileSourceRow struct {
            FileID, GroupID, Name, OriginalFilename, Format string
            Data []byte
            StorageKey *string
            SizeBytes int64
            Metadata map[string]any
            CreatedBy *string
        }
        Repository.Save(ctx, FileSourceRow) error
        Repository.SaveBatch(ctx, []FileSourceRow) error
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


FileSourcePublicRow = FileSourceRow


def _fetchone(sql: str, params: tuple = ()) -> Optional[FileSourcePublicRow]:
    return fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[FileSourcePublicRow]:
    return fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    return execute(sql, params)


def fetchone(sql: str, params: tuple = ()) -> Optional[FileSourcePublicRow]:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def fetchall(sql: str, params: tuple = ()) -> list[FileSourcePublicRow]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def _validate_row(row: FileSourceRow, *, context: str) -> None:
    """Valida l'invariante "data o storage_key non-None" su una row.

    Usato da `save_file_source` e `save_file_sources_batch` per
    centralizzare il check. Solleva `ValueError` con context-tagged
    message; nessun side effect.

    Args:
        row: dict-shape FileSourceRow.
        context: tag prefix per il messaggio di errore (nome funzione
                 caller, es. "save_file_source" o "save_file_sources_batch").
    """
    if row.get("data") is None and not row.get("storage_key"):
        raise ValueError(
            f"{context}: row file_id={row.get('file_id')!r} "
            f"senza data ne storage_key (almeno uno richiesto)"
        )


def get_file_source(
    file_id: str,
    include_data: bool = False,
    group_id: str = None,
) -> Optional[FileSourcePublicRow]:
    """Recupera file source. group_id=None → no filter (backward compat)."""
    if include_data:
        if group_id:
            row = _fetchone(
                "SELECT * FROM file_sources WHERE id = %s AND group_id = %s",
                (file_id, group_id),
            )
        else:
            row = _fetchone(
                "SELECT * FROM file_sources WHERE id = %s",
                (file_id,),
            )
    elif group_id:
        row = _fetchone(
            "SELECT id, group_id, name, original_filename, format, size_bytes, metadata, created_at, created_by "
            "FROM file_sources WHERE id = %s AND group_id = %s",
            (file_id, group_id),
        )
    else:
        row = _fetchone(
            "SELECT id, group_id, name, original_filename, format, size_bytes, metadata, created_at, created_by "
            "FROM file_sources WHERE id = %s",
            (file_id,),
        )
    if row and row.get("metadata"):
        metadata = row["metadata"]
        # psycopg2 auto-deserializes JSONB into dict after the custom
        # JSONB_AS_TEXT typecaster was removed by the "Harden PostgreSQL
        # boundary" commit. Older text rows still arrive as str.
        if not isinstance(metadata, (dict, list)):
            row["metadata"] = json.loads(metadata)
    return row


def list_file_sources(group_id: Optional[str] = None) -> list[FileSourcePublicRow]:
    """Lista file_sources. group_id=None → no filter (usato da superadmin)."""
    if group_id is None:
        return _fetchall(
            "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources ORDER BY created_at DESC"
        )
    return _fetchall(
        "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources WHERE group_id = %s ORDER BY created_at DESC",
        (group_id,)
    )


def list_file_source_ids_for_group(group_id: str) -> List[str]:
    """List file_source ids owned by a group for storage-aware cleanup."""
    rows = _fetchall(
        "SELECT id FROM file_sources WHERE group_id = %s ORDER BY id",
        (group_id,),
    )
    ids: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            file_id = row.get("id")
        else:
            file_id = row[0] if row else None
        if file_id:
            ids.append(str(file_id))
    return ids


def save_file_source(file_id: str, group_id: str, name: str, original_filename: str,
                     fmt: str, data: Optional[bytes] = None,
                     metadata: dict = None, created_by: str = None,
                     storage_key: Optional[str] = None,
                     size_bytes: Optional[int] = None) -> FileSourcePublicRow:
    """Salva/aggiorna file_source.

    Dispatch FS-B1-large:
      - `data` + `size_bytes=len(data)` + `storage_key=None` → blob in DB (BYTEA)
      - `data=None` + `storage_key="file-sources/..."` + `size_bytes=N` → blob
        su S3, la row tiene solo metadata + puntatore.

    Almeno uno tra `data` e `storage_key` deve essere non-None (check
    app-level, il DB non lo enforce perche' la migration rilassa NOT NULL
    su data).
    """
    _validate_row(
        {"file_id": file_id, "data": data, "storage_key": storage_key},
        context="save_file_source",
    )
    now = datetime.now(timezone.utc).isoformat()
    effective_size = size_bytes if size_bytes is not None else (len(data) if data else 0)
    _execute(
        "INSERT INTO file_sources (id, group_id, name, original_filename, format, data, storage_key, size_bytes, metadata, created_at, updated_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
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
                - data (Optional[bytes]) shared blob in BYTEA path
                - storage_key (Optional[str]) shared key in S3 path
                - size_bytes (int)
                - metadata (dict) serialized to JSON dentro
                - created_by (Optional[str])

    Pre-transaction validation (fail-fast, zero side effect DB):
        Per ogni row almeno uno tra `data` e `storage_key` deve essere
        non-None (mirror del check di save_file_source single-row).
        ValueError -> nessuna connessione DB acquisita.

    Returns:
        None. Il caller possiede gia' i DTO costruiti client-side; nessuna
        SELECT post-insert (risparmio N round-trip rispetto a save_file_source
        legacy).

    portable boundary:
        FileSourceRepository.SaveBatch(ctx, []FileSourceRow) error
        - input shape esplicita (no kwargs);
        - errori espliciti via raise;
        - transaction boundary visibile (un solo `with unit_of_work`).
    """
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

    with unit_of_work() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)


def update_file_source_metadata(file_id: str, metadata: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _execute(
        "UPDATE file_sources SET metadata = %s, updated_at = %s WHERE id = %s",
        (json.dumps(metadata), now, file_id)
    )


def delete_file_source(file_id: str) -> bool:
    """Elimina file_source dal DB + cleanup S3 se blob era S3-backed (FS-B1-large).

    Flow (BE-HARDEN-001C E4 / BE-DELETE-002):

      1. Apre `unit_of_work` (single conn, autocommit off).
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
    # reconciliation entry handle
    # Set only when we are about to commit a "last reference" delete
    # that triggers post-commit storage cleanup. Stays None on the
    # shared-blob path (siblings exist) and on the BYTEA path
    # storage_key None) — neither needs reconciliation
    reconciliation_ref = None

    with unit_of_work() as conn:
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
                    # record cleanup
                    # intent inside the same transaction as the
                    # DELETE. Commit advances both atomically; if a
                    # process death follows the commit before the
                    # post-commit storage call, the pending row is
                    # the only application-side trace of the orphan
                    reconciliation_ref = reconciliation.record_pending(
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
    # on storage failure, DB row stays deleted
    # delegato all'helper unificato
    # outcome -> reconciliation transition
    if storage_key_to_clean:
        outcome = storage_compensation.compensating_delete(
            storage_key_to_clean,
            domain="file_source",
            reason="row_delete_post_commit",
            extras={"file_id": file_id},
            logger=logger,
        )
        if reconciliation_ref is not None:
            if outcome in ("ok", "skipped"):
                reconciliation.mark_done(reconciliation_ref.id)
            else:
                reconciliation.mark_failed_attempt(
                    reconciliation_ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted
