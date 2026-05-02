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
  - Legge `os.getenv("FILE_SOURCE_S3_THRESHOLD_BYTES", default)`.
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

import os
from typing import Any, Dict


_DEFAULT_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
_OBJECT_STORAGE_PREFIX = "file-sources"


def dispatch_blob_storage(
    file_id: str,
    raw_data: bytes,
    filename: str,
    *,
    logger: Any,
) -> Dict[str, Any]:
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
        file_id:    identifier del file source (usato per la storage_key).
        raw_data:   bytes del blob da persistere.
        filename:   nome file originale (usato per la storage_key).
        logger:     LoggerProtocol-like; usato per warning/error/info
                    sul path object_storage. Passato dal caller per
                    evitare coupling con FileSourceService.

    Returns:
        dict con chiavi `data` (bytes|None), `storage_key` (str|None),
        `size_bytes` (int). Compatibile con la signature attuale di
        `db.save_file_source`.

    Raises:
        Eventuali eccezioni del client object storage (rilanciate
        per permettere l'orphan guard nel caller).
    """
    from app.config import settings

    size = len(raw_data)
    policy = (settings.get("STORAGE_POLICY", "hybrid") or "hybrid").strip().lower()

    if policy == "db":
        use_object_storage = False
    elif policy == "object_storage":
        use_object_storage = True
    else:
        # hybrid: soglia per-dominio file_source (separata da result)
        threshold = int(
            os.getenv("FILE_SOURCE_S3_THRESHOLD_BYTES", _DEFAULT_THRESHOLD_BYTES)
        )
        use_object_storage = size >= threshold

    if not use_object_storage:
        return {"data": raw_data, "storage_key": None, "size_bytes": size}

    # Object storage path: upload in `file-sources/{file_id}/{filename}` -
    # mirror di `eseguibili-outputs/` e `imdf-bundles/` nel naming bucket.
    storage_key = f"{_OBJECT_STORAGE_PREFIX}/{file_id}/{filename}"
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
