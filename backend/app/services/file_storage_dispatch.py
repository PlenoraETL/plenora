"""Storage dispatch policy-aware per file source blob (STRUCT-001 Seam C).

Estratto da `FileSourceService._dispatch_blob_storage` senza alcun
cambio comportamentale. Rimane un solo punto di decisione su come
persistere il blob:

  - `STORAGE_POLICY=db`             -> sempre DB BYTEA
  - `STORAGE_POLICY=object_storage` -> sempre object storage
  - `STORAGE_POLICY=hybrid` (def.)  -> threshold-based su
                                       FILE_SOURCE_S3_THRESHOLD_BYTES
                                       (default 50 MB)

API pubblica:
    dispatch_blob_storage(file_id, raw_data, filename, *, logger) -> dict
        Ritorna kwargs per `db.save_file_source`:
        {"data": bytes|None, "storage_key": str|None, "size_bytes": int}

Logger e' passato come parametro (LoggerProtocol-compatibile, vedi
`app.services.file_service_deps`): la funzione non importa
FileSourceService, evita coupling al ciclo di vita del service e
resta facilmente unit-testabile con un MagicMock logger.

Side-effect:
  - Legge `app.config.settings` (env-aware via `settings.get`).
  - Legge `settings.get("FILE_SOURCE_S3_THRESHOLD_BYTES")` (default
    tipato in `Settings.DEFAULTS`).
  - Per object_storage path: chiama
    `app.shared.cloud_backends.get_runtime_object_storage().upload_bytes(...)`.
    Eccezioni dell'upload vengono LOGGATE e RILANCIATE - il caller
    (FileSourceService) e' responsabile dell'orphan guard
    (no row DB se l'upload fallisce).

Cycle: leaf module rispetto a `app.services.file_service`. Importa
solo stdlib, app.config, app.shared.cloud_backends. Nessun import
da `app.services.*`.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from app.shared.storage_layout import FILE_SOURCES_PREFIX


# Alias modulare verso `app.shared.storage_layout.FILE_SOURCES_PREFIX`
# (BE-DATA-PLANE-001A): single source-of-truth per i prefix object
# storage. Mantenuto come name privato per backward-compat dei test
# che leggono `_OBJECT_STORAGE_PREFIX`.
_OBJECT_STORAGE_PREFIX = FILE_SOURCES_PREFIX


class BlobDispatchResult(TypedDict):
    """Risultato tipizzato di `dispatch_blob_storage` (BE-SHAPE-002).

    Shape compatibile 1:1 con i kwargs accettati da `db.save_file_source`
    e da `db.save_file_sources_batch` (chiavi `data`, `storage_key`,
    `size_bytes`). Ritorna sempre tutti e tre i campi.

    Path BYTEA  -> data=raw_bytes,  storage_key=None,  size_bytes=N
    Path object -> data=None,       storage_key="..", size_bytes=N

    Go-ready mapping:
        type BlobDispatchResult struct {
            Data       []byte
            StorageKey *string
            SizeBytes  int64
        }
    Caller pattern attuale (`**blob_kwargs`) resta invariato a runtime:
    TypedDict e' un `dict` regolare.
    """

    data: Optional[bytes]
    storage_key: Optional[str]
    size_bytes: int


def dispatch_blob_storage(
    file_id: str,
    raw_data: bytes,
    filename: str,
    *,
    logger: Any,
    storage_key_id: Optional[str] = None,
) -> BlobDispatchResult:
    """Dispatch policy-aware del blob (A1).

    Legge `STORAGE_POLICY` e ramifica:
      - `db`             -> sempre DB BYTEA (niente object storage)
      - `object_storage` -> sempre object storage (niente BYTEA)
      - `hybrid`         -> threshold-based su
                            `FILE_SOURCE_S3_THRESHOLD_BYTES`
                            (default 50 MB, comportamento attuale)

    Ritorna kwargs per `db.save_file_source`: `data`, `storage_key`,
    `size_bytes`. Se upload object storage fallisce, rilancia -
    caller deve gestire (orphan guard: no row DB se upload fallisce).

    Args:
        file_id:        identifier del file source. Usato per la storage_key
                        SE `storage_key_id` non e' fornito (backward-compat
                        single-file). Sempre usato nei log per audit trail.
        raw_data:       bytes del blob da persistere.
        filename:       nome file originale (usato per la storage_key).
        logger:         LoggerProtocol-like; warning/error/info sul path
                        object_storage. Passato dal caller per evitare
                        coupling con FileSourceService.
        storage_key_id: opzionale (BE-HARDEN-001C E1). Se valorizzato,
                        sostituisce `file_id` come segmento path nello
                        storage_key. Usato dal multi-layer upload per
                        condividere 1 blob fisico tra N FileSource logici
                        (storage_key=`file-sources/{storage_key_id}/{filename}`,
                        identico per tutte le N row del batch).
                        Default None -> naming basato su file_id
                        (comportamento single-file invariato).

    Returns:
        dict con chiavi `data` (bytes|None), `storage_key` (str|None),
        `size_bytes` (int). Compatibile con la signature attuale di
        `db.save_file_source`.

    Raises:
        Eventuali eccezioni del client object storage (rilanciate
        per permettere l'orphan guard nel caller).
    """
    from app.config import settings
    from app.shared.storage_dispatch import resolve_payload_target

    size = len(raw_data)
    policy = (settings.get("STORAGE_POLICY", "hybrid") or "hybrid").strip().lower()

    # Soglia per-dominio file_source (separata dal threshold result; ops
    # puo' tunarle in modo indipendente). Letta sempre, ma la decisione
    # la usa solo nel branch hybrid (la pure function ignora la threshold
    # quando policy=db/object_storage). BE-CONFIG-001B: default tipato in
    # `Settings.DEFAULTS["FILE_SOURCE_S3_THRESHOLD_BYTES"]`; override env
    # continua a funzionare via `Settings._converti_tipo_env` (coercion
    # `int`).
    threshold = int(settings.get("FILE_SOURCE_S3_THRESHOLD_BYTES"))

    # BE-DATA-PLANE-001B: decisione db vs object_storage delegata a
    # `app.shared.storage_dispatch.resolve_payload_target` (pure
    # function). Comportamento bit-per-bit identico al pre-001B.
    use_object_storage = resolve_payload_target(
        policy=policy,
        size_bytes=size,
        threshold_bytes=threshold,
    ) == "object_storage"

    if not use_object_storage:
        return {"data": raw_data, "storage_key": None, "size_bytes": size}

    # Object storage path. BE-HARDEN-001C E1: il segmento path puo' essere
    # `file_id` (default, single-file) oppure `storage_key_id` (multi-layer
    # con shared blob). Pattern speculare a `eseguibili-outputs/` e
    # `imdf-bundles/` nel naming bucket.
    key_id = storage_key_id if storage_key_id is not None else file_id
    storage_key = f"{_OBJECT_STORAGE_PREFIX}/{key_id}/{filename}"
    try:
        from app.shared.cloud_backends import get_runtime_object_storage
        storage = get_runtime_object_storage()
        storage.upload_bytes(raw_data, storage_key)
    except Exception as e:
        logger.error(
            f"[FS-B1-large] Upload object storage fallito per {file_id} "
            f"(policy={policy}, {size // (1024 * 1024)} MB): {e}"
        )
        raise
    logger.info(
        f"[FS-B1-large] File source {file_id} su object storage "
        f"(policy={policy}, {size // (1024 * 1024)} MB): {storage_key}"
    )
    return {"data": None, "storage_key": storage_key, "size_bytes": size}
