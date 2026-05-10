"""File-source path materialization boundary.

This module owns the reusable ``file_source_id -> filesystem path`` workflow:
tenant visibility, metadata/blob lookup, and atomic cache writes. Domain
callers pass only the file source id and the already-resolved tenant context.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

import psycopg2

import app.core.files.db as file_sources_db
from app.core.auth.visibility import is_resource_visible
from app.core.files.exceptions import FileSourceResolutionError
from app.utils.logger import get_logger


GetFileSource = Callable[[str, bool], Optional[dict[str, Any]]]
GetFileSourceDir = Callable[[str], Path]
VisibilityCheck = Callable[..., bool]

logger = get_logger(__name__)


def _default_get_file_source(
    file_source_id: str,
    include_data: bool = False,
) -> Optional[dict[str, Any]]:
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
    return (
        row_meta.get("original_filename")
        or (row_meta.get("metadata") or {}).get("original_filename")
        or "input.bin"
    )


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
    except psycopg2.Error as exc:
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
    if target.is_file():
        return str(target.resolve())

    try:
        row_full = get_file_source(file_source_id, True)
    except psycopg2.Error as exc:
        raise FileSourceResolutionError(
            file_source_id=file_source_id,
            cause="lookup",
            reason="errore lettura blob file_source dal DB",
            original_error=exc,
        ) from exc

    if not row_full or not row_full.get("data"):
        return None

    raw = row_full["data"]
    try:
        source_dir.mkdir(parents=True, exist_ok=True)
        out_path = source_dir / expected_name
        tmp_path = source_dir / f".{expected_name}.{os.getpid()}.tmp"
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
