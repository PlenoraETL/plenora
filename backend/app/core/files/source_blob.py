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

from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.file_source_blob_access import load_file_source_blob_bytes


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

    CONTRACT CALLER (P0 data-layer audit):
        Il path ritornato DEVE essere cancellato dal caller in
        `try/finally`. Pre-fix il caller `FileSourceService.load_dataframe`
        non garantiva l'unlink su eccezione, accumulando file orfani
        sotto TEMP_DIR. Cleanup di emergenza atexit registrato qui
        per i casi di graceful shutdown — non sopravvive a SIGKILL.

    Per cleanup deterministico usare `temp_file_for_cm` (context
    manager) introdotto in sessione 4.
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
    out_path = Path(temporary_file.name)
    # Safety net atexit: il caller dovrebbe cancellare manualmente al
    # termine dell'uso, ma su graceful shutdown garantiamo il cleanup
    # dei tempfile orfani — leak prevention per il path non-SHP/GDB.
    _register_tempfile_atexit_cleanup(out_path)
    return out_path


from contextlib import contextmanager


_ATEXIT_TEMPFILES: list[Path] = []
_ATEXIT_HANDLER_REGISTERED = False


def _register_tempfile_atexit_cleanup(path: Path) -> None:
    """Registry process-local: cancella i tempfile orfani al graceful
    shutdown. Best-effort, no SIGKILL coverage. Pattern uguale al
    `result_reader._register_for_atexit_cleanup`."""
    global _ATEXIT_HANDLER_REGISTERED
    if not _ATEXIT_HANDLER_REGISTERED:
        import atexit
        atexit.register(_cleanup_tempfiles_atexit)
        _ATEXIT_HANDLER_REGISTERED = True
    _ATEXIT_TEMPFILES.append(path)


def _cleanup_tempfiles_atexit() -> None:
    """Cancella i tempfile orfani al graceful shutdown."""
    for path in list(_ATEXIT_TEMPFILES):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


@contextmanager
def temp_file_for_cm(
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
):
    """Context manager preferito: cleanup deterministico al with-exit.

    Wrapper attorno a `temp_file_for` che yield il path e ne garantisce
    la cancellazione (file o dir SHP/GDB) al ritorno dal blocco `with`.
    Mappa a Go come `defer os.RemoveAll(path)`.

    Esempio:
        with temp_file_for_cm(file_id, ...) as path:
            if path is None:
                return None
            process(path)
        # path cancellato qui automaticamente
    """
    path = temp_file_for(
        file_id,
        expected_group_id=expected_group_id,
        db_get_file_source=db_get_file_source,
        get_blob_from_db=get_blob_from_db,
        temp_dir=temp_dir,
        mkdtemp_writable=mkdtemp_writable,
        safe_extractall=safe_extractall,
        rmtree_safe=rmtree_safe,
        logger=logger,
    )
    try:
        yield path
    finally:
        if path is None:
            return
        try:
            # SHP/GDB: path e' un file dentro tmp_dir; il rmtree_safe del
            # caller potrebbe non sapere come trovarlo. Per safety,
            # cancelliamo sia il file singolo (caso non-SHP) sia la
            # directory parent se siamo dentro un tmp_dir mkdtemp.
            if path.is_dir():
                rmtree_safe(path)
            elif path.exists():
                path.unlink()
        except OSError as e:
            logger.warning(
                f"[GESTITO] temp_file_for_cm cleanup fallito per {path}: {e}"
            )


__all__ = [
    "AssertVisible",
    "DbGetFileSource",
    "GetBlobFromDb",
    "get_blob_from_db",
    "temp_file_for",
    "temp_file_for_cm",
]
