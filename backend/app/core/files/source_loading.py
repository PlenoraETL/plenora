"""File-source bytes resolution boundary (RAM-only).

Post-refactor: il file_source si risolve in `(bytes, filename)` direttamente
da DB / object storage. Niente piu' cache locale su `{FILES_DIR}/{file_source_id}/`.
Il caller (REST connector) consuma i bytes via `requests.post(files=...)`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import psycopg

import app.core.files.db as file_sources_db
from app.core.auth.visibility import is_resource_visible
from app.core.files.exceptions import FileSourceResolutionError
from app.shared.resources.path_policy import assert_path_within
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger


GetFileSource = Callable[[str, bool], Optional[JsonObject]]
VisibilityCheck = Callable[..., bool]

logger = get_logger(__name__)


def _default_get_file_source(
    file_source_id: str,
    include_data: bool = False,
) -> Optional[JsonObject]:
    return file_sources_db.get_file_source(
        file_source_id,
        include_data=include_data,
    )


def assert_file_source_group_access(
    file_source_id: str,
    expected_group_id: Optional[str],
    *,
    visibility_check: Optional[VisibilityCheck] = None,
) -> None:
    """Fail-closed visibility check for a persisted file source.

    ``expected_group_id`` must come from the caller's typed execution/request
    context. This boundary intentionally does not read legacy singleton state
    or infer a tenant from the file row.
    """
    if not expected_group_id:
        raise ValueError("Contesto di sicurezza non determinabile per file_source")

    check = visibility_check
    if check is None:
        check = is_resource_visible

    try:
        visible = check(
            "file_sources",
            file_source_id,
            expected_group_id,
            is_superadmin=False,
        )
    except Exception as exc:
        logger.warning(f"Cross-tenant check file_source fallito: {exc}")
        raise ValueError(
            f"Errore verifica accesso file_source '{file_source_id}'"
        ) from exc

    if not visible:
        logger.error(
            f"[SECURITY] Cross-tenant access blocked: file_source "
            f"{file_source_id} non visibile al gruppo {expected_group_id}",
        )
        raise ValueError(
            f"Accesso negato: il file sorgente '{file_source_id}' "
            f"non appartiene al tuo gruppo"
        )


def _expected_filename(row_meta: dict[str, Any]) -> str:
    """Estrai il filename atteso dalla riga DB, normalizzato a SOLO basename.

    Pre-fix (CVE-grade P0): ritornava `row_meta["original_filename"]` raw.
    Un record DB con `original_filename = "../../../etc/passwd"` (creato da
    upload malformato o DB corrotto) componeva un path che usciva dalla
    source_dir, esponendo sia READ che WRITE arbitrari sotto FILES_DIR.

    Post-fix: `Path(raw).name` taglia qualunque directory component,
    indipendentemente da `..`, `/`, `\\`, separatori URL-encoded, ecc.
    Post-Phase5 il path-cache e' stato eliminato (la funzione ritorna
    bytes), quindi il rischio path-traversal non esiste piu' by-design;
    il normalizer resta come defense-in-depth sul filename ritornato.
    """
    raw = (
        row_meta.get("original_filename")
        or (row_meta.get("metadata") or {}).get("original_filename")
        or "input.bin"
    )
    # `Path.name` ritorna solo il basename, scartando directory component
    # e mantenendo solo l'ultimo segmento del path. Funziona cross-platform
    # (su Windows accetta sia / sia \). Esempi:
    #   "../../etc/passwd"        -> "passwd"
    #   "/absolute/evil.csv"      -> "evil.csv"
    #   "dir/../sibling.parquet"  -> "sibling.parquet"
    #   "normal.csv"              -> "normal.csv"
    basename = Path(str(raw).replace("\\", "/")).name
    # Edge case: se il basename e' vuoto (raw = "../") o e' `..` puro
    # (Path("..").name == ".." su Windows e Linux), fallback al default
    # per non ritornare un path-component pericoloso.
    if not basename or basename in {"..", "."}:
        return "input.bin"
    guard_root = Path("/__plenora_filename_guard__")
    assert_path_within(guard_root / basename, [guard_root])
    return basename


def resolve_file_source_id_bytes(
    file_source_id: str,
    *,
    expected_group_id: Optional[str],
    get_file_source: GetFileSource = _default_get_file_source,
    visibility_check: Optional[VisibilityCheck] = None,
    **_legacy_kwargs: Any,
) -> Optional[tuple[bytes, str]]:
    """Risolve un file_source_id in `(bytes, filename)` RAM-only.

    Returns:
        `(blob_bytes, expected_filename)` se il file_source esiste e ha
        contenuto. `None` se metadata manca o blob vuoto.

    Raises:
        ValueError (via `assert_file_source_group_access`) su cross-tenant.
        `FileSourceResolutionError` su fault DB / object storage.

    Sostituisce la vecchia `resolve_file_source_id_path` che persisteva
    il blob su `{FILES_DIR}/{file_source_id}/{filename}` come cache
    locale. Il caller (REST connector) consuma i bytes direttamente in
    `requests.post(files=...)`, niente cache disco — trade-off: re-fetch
    da DB / object storage ad ogni invocazione (accettabile per il
    profilo Plenora: tipicamente una POST per file_source per task).

    `**_legacy_kwargs` swallow per backward-compat con test che ancora
    passano `get_file_source_dir=...` (no-op nel path bytes-first).
    """
    if not file_source_id:
        return None

    assert_file_source_group_access(
        file_source_id,
        expected_group_id,
        visibility_check=visibility_check,
    )

    try:
        row_meta = get_file_source(file_source_id, False)
    except psycopg.Error as exc:
        raise FileSourceResolutionError(
            file_source_id=file_source_id,
            cause="lookup",
            reason="errore lettura metadata file_source dal DB",
            original_error=exc,
        ) from exc

    if not row_meta:
        return None

    expected_name = _expected_filename(row_meta)

    try:
        row_full = get_file_source(file_source_id, True)
    except psycopg.Error as exc:
        raise FileSourceResolutionError(
            file_source_id=file_source_id,
            cause="lookup",
            reason="errore lettura blob file_source dal DB",
            original_error=exc,
        ) from exc

    if not row_full:
        return None

    raw = row_full.get("data")
    # STORAGE_POLICY=hybrid/object_storage: blob su S3/MinIO via
    # `storage_key`. Fallback al loader storage-aware quando data=NULL.
    if not raw and row_full.get("storage_key"):
        try:
            from app.core.resources.file_source_blob_access import (
                load_file_source_blob_bytes,
            )

            # `is_superadmin=False` fisso (cross-tenant guard upstream).
            # Il loader applica gia' il filtro SQL su group_id.
            raw = load_file_source_blob_bytes(
                file_source_id=file_source_id,
                expected_group_id=expected_group_id or "",
                is_superadmin=False,
            )
        except Exception as exc:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="lookup",
                reason="errore download blob da object storage",
                original_error=exc,
            ) from exc

    if not raw:
        return None

    blob_bytes = raw if isinstance(raw, bytes) else bytes(raw)
    return blob_bytes, expected_name


# Backward-compat alias: il nome `resolve_file_source_id_path` storicamente
# ritornava un path str di una cache locale. Post-Phase5 il path-cache e'
# stato eliminato; la funzione ritorna `(bytes, filename)`. Tenuto come
# alias per non rompere i test esistenti che importano il nome vecchio —
# i caller di produzione devono migrare a `resolve_file_source_id_bytes`.
resolve_file_source_id_path = resolve_file_source_id_bytes


__all__ = [
    "assert_file_source_group_access",
    "resolve_file_source_id_bytes",
    "resolve_file_source_id_path",
]
