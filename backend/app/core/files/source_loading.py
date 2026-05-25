"""File-source path materialization boundary.

This module owns the reusable ``file_source_id -> filesystem path`` workflow:
tenant visibility, metadata/blob lookup, and atomic cache writes. Domain
callers pass only the file source id and the already-resolved tenant context.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

import psycopg

import app.core.files.db as file_sources_db
from app.core.auth.visibility import is_resource_visible
from app.core.files.exceptions import FileSourceResolutionError
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger


GetFileSource = Callable[[str, bool], Optional[JsonObject]]
GetFileSourceDir = Callable[[str], Path]
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


def _default_get_file_source_dir(file_source_id: str) -> Path:
    from app.config import settings

    return settings.get_file_source_dir(file_source_id)


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
    upload malformato o DB corrotto) costruiva un path `source_dir / "../../../etc/passwd"`
    che, su filesystem, usciva dalla `source_dir`. La validazione
    `assert_path_within` arrivava DOPO il primo `target.is_file()` (riga
    136) e DOPO `tmp_path.write_bytes()` (riga 185), permettendo sia READ
    sia WRITE arbitrari fuori dalla source_dir.

    Post-fix: `Path(raw).name` taglia qualunque directory component,
    indipendentemente da `..`, `/`, `\\`, separatori URL-encoded, ecc.
    Backstop: l'`assert_path_within` rimane come defense-in-depth a valle.
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
    basename = Path(str(raw)).name
    # Edge case: se il basename e' vuoto (raw = "../") o e' `..` puro
    # (Path("..").name == ".." su Windows e Linux), fallback al default
    # per non ritornare un path-component pericoloso.
    if not basename or basename in {"..", "."}:
        return "input.bin"
    return basename


def resolve_file_source_id_path(
    file_source_id: str,
    *,
    expected_group_id: Optional[str],
    get_file_source: GetFileSource = _default_get_file_source,
    get_file_source_dir: GetFileSourceDir = _default_get_file_source_dir,
    visibility_check: Optional[VisibilityCheck] = None,
) -> Optional[str]:
    """Materialize a file-source blob into the stable file-source cache.

    Returns ``None`` only for legitimate data absence: missing metadata row or
    empty blob. DB/FS failures raise ``FileSourceResolutionError`` with the
    original exception chained.
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
    source_dir = get_file_source_dir(file_source_id)
    target = source_dir / expected_name
    # Defense-in-depth: `_expected_filename` ora ritorna solo basename
    # via `Path.name`, ma manteniamo l'assert come backstop per i casi
    # edge in cui il `get_file_source_dir` ritorni un path-like esotico
    # o per future regressioni del normalizer. Anti-regression P0 path
    # traversal etl-sources cluster 5.
    from app.shared.resources.path_policy import assert_path_within
    assert_path_within(target, [source_dir])
    if target.is_file():
        return str(target.resolve())

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
    # In STORAGE_POLICY=hybrid/object_storage il blob non e' in BYTEA
    # ma su S3/MinIO referenziato da `storage_key`. La query precedente
    # ritorna sempre la riga (anche con data=NULL); per recuperare il
    # blob serve il loader storage-aware. Senza questo fallback i
    # caller (rest_file connector, materialize) ricevevano None e
    # fallivano con "nessun file di input collegato".
    if not raw and row_full.get("storage_key"):
        try:
            from app.core.resources.file_source_blob_access import (
                load_file_source_blob_bytes,
            )

            # Pre-fix (CVE-grade P0): `is_superadmin=expected_group_id is None`
            # significa: "se nessun tenant e' stato specificato, leggi il blob
            # come superadmin (no group_id filter)". Combinato con un guard
            # `assert_file_source_group_access` che fallisce solo se
            # `expected_group_id` e' falsy, la sequenza era:
            #   1. expected_group_id=None -> guard solleva ValueError -> OK
            # MA se un caller esterno bypassava il guard (es. iniezione
            # diretta o refactor che chiama questa funzione skip-step), il
            # blob veniva letto in modalita' superadmin: bypass cross-tenant.
            # Post-fix: `is_superadmin=False` fisso. Il superadmin non ha
            # ruolo in questo path di cache: chi e' superadmin passa per
            # un endpoint admin dedicato, non per `resolve_file_source_id_path`.
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
    try:
        source_dir.mkdir(parents=True, exist_ok=True)
        out_path = source_dir / expected_name
        tmp_path = source_dir / f".{expected_name}.{os.getpid()}.tmp"
        # Defense-in-depth pre-write: stesso assert del read path sopra
        # — protegge anche il `write_bytes` se per qualche reason
        # `expected_name` arrivasse non-normalizzato (es. monkeypatch in
        # un test che bypassa _expected_filename, o futuro refactor).
        assert_path_within(out_path, [source_dir])
        assert_path_within(tmp_path, [source_dir])
        try:
            tmp_path.write_bytes(raw if isinstance(raw, bytes) else bytes(raw))
            os.replace(str(tmp_path), str(out_path))
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception as tmp_err:
                    logger.debug(
                        "resolve_file_source_id_path: unlink tmp_path "
                        f"best-effort fallito: {tmp_err}"
                    )
    except OSError as exc:
        raise FileSourceResolutionError(
            file_source_id=file_source_id,
            cause="materialization",
            reason="scrittura cache file_source su filesystem fallita",
            original_error=exc,
        ) from exc

    logger.debug(
        f"file_source {file_source_id}: blob materializzato in cache -> {out_path}"
    )
    return str(out_path.resolve())


__all__ = [
    "assert_file_source_group_access",
    "resolve_file_source_id_path",
]
