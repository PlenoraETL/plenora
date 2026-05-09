"""Blob read and tempfile materialization for file sources.

This module owns the storage-engine blob read and the temporary file
materialization used by FileSourceService. Side-effect collaborators are
passed explicitly to keep the service facade stable and testable.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional


AssertVisible = Callable[[str, str], None]
DbGetFileSource = Callable[..., Optional[dict[str, Any]]]
GetBlobFromDb = Callable[[str, Optional[str]], Optional[bytes]]


def get_blob_from_db(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    assert_visible: AssertVisible,
    superadmin_bypass: str,
    logger: Any,
) -> Optional[bytes]:
    """Read the file_source blob, fail-soft.

    Tenant visibility remains fail-closed: callers inject the same
    FileSourceService._assert_visible callback used before separation.
    """
    from app.core.resources.file_source_blob_access import (
        load_file_source_blob_bytes,
    )
    from app.core.resources.exceptions import MemoryLimitExceededError

    # `_assert_visible` re-raises PermissionError to the caller. After it
    # returns, the caller has been authorized for this file_source.
    assert_visible(file_id, expected_group_id or "")

    is_superadmin_caller = expected_group_id == superadmin_bypass

    def _fetch(is_superadmin: bool) -> Optional[bytes]:
        return load_file_source_blob_bytes(
            file_source_id=file_id,
            expected_group_id=(expected_group_id or "").strip()
            or superadmin_bypass,
            is_superadmin=is_superadmin,
        )

    try:
        return _fetch(is_superadmin=is_superadmin_caller)
    except FileNotFoundError:
        # Owned-path SQL filter excluded the row. Either the file genuinely
        # does not exist OR `_assert_visible` authorized via *_shares.
        if is_superadmin_caller:
            return None
        try:
            bypass_shared_fetch = not is_superadmin_caller
            return _fetch(is_superadmin=bypass_shared_fetch)
        except FileNotFoundError:
            return None
        except MemoryLimitExceededError as e:
            logger.error(
                f"[GESTITO] Blob {file_id} oversize su retry "
                f"con bypass loader: {e}. "
                f"Cap RESOURCE_MAX_BYTES limita la lettura."
            )
            return None
        except Exception as e:
            logger.error(
                f"[GESTITO] Errore lettura blob {file_id} (retry): {e}"
            )
            return None
    except MemoryLimitExceededError as e:
        logger.error(
            f"[GESTITO] Blob {file_id} oversize: {e}. "
            f"Cap RESOURCE_MAX_BYTES limita la lettura."
        )
        return None
    except Exception as e:
        logger.error(
            f"[GESTITO] Errore lettura blob {file_id}: {e}"
        )
        return None


def temp_file_for(
    file_id: str,
    *,
    expected_group_id: Optional[str] = None,
    db_get_file_source: DbGetFileSource,
    get_blob_from_db: GetBlobFromDb,
    temp_dir: str,
    mkdtemp_writable: Callable[..., Path],
    safe_extractall: Callable[[zipfile.ZipFile, Path], None],
    rmtree_safe: Callable[..., None],
    logger: Any,
) -> Optional[Path]:
    """Materialize a file_source blob to a tempfile.

    For SHP/GDB blobs this extracts the ZIP into a temp directory and returns
    the .shp/.gdb path inside it. The caller owns successful-path cleanup;
    this function cleans only malformed/error paths.
    """
    try:
        row_meta = db_get_file_source(file_id, include_data=False)
    except Exception as e:
        logger.warning(
            f"[GESTITO] _temp_file_for {file_id}: fetch metadata fallito: {e}"
        )
        return None
    if not row_meta:
        return None

    blob = get_blob_from_db(file_id, expected_group_id)
    if not blob:
        return None

    fmt = row_meta.get("format", "")
    original_filename = row_meta.get("original_filename", f"{file_id}.bin")

    if fmt in ("shp", "gdb"):
        tmp_dir = mkdtemp_writable(dir=temp_dir)
        try:
            with zipfile.ZipFile(io.BytesIO(blob), "r") as zip_archive:
                safe_extractall(zip_archive, tmp_dir)
            if fmt == "shp":
                shp_files = (
                    list(tmp_dir.rglob("*.shp"))
                    + list(tmp_dir.rglob("*.SHP"))
                )
                if shp_files:
                    return shp_files[0]
            else:
                gdb_dirs = [
                    f for f in tmp_dir.rglob("*")
                    if f.is_dir() and f.suffix.lower() == ".gdb"
                ]
                if gdb_dirs:
                    return gdb_dirs[0]
        except Exception as e:
            logger.error(
                f"[GESTITO] Errore estrazione {fmt.upper()} zip {file_id}: {e}"
            )
        rmtree_safe(tmp_dir)
        return None

    suffix = Path(original_filename).suffix or ".bin"
    temporary_file = tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, dir=temp_dir,
    )
    temporary_file.write(blob)
    temporary_file.close()
    return Path(temporary_file.name)


__all__ = [
    "AssertVisible",
    "DbGetFileSource",
    "GetBlobFromDb",
    "get_blob_from_db",
    "temp_file_for",
]
