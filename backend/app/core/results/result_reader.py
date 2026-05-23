"""Legacy result read helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.results import store
from app.core.results.temp_paths import make_cached_result_path
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_result_as_bytes(project_id: str) -> Optional[bytes]:
    """Return the latest result blob bytes for a project."""
    try:
        row = store.get_latest_result_row(project_id)
        if not row or not row.id:
            return None
        return store.get_result_data(row.id)
    except Exception as exc:
        logger.warning(f"Errore lettura blob result ({project_id}): {exc}")
        return None


def get_result_as_tempfile(project_id: str) -> Optional[Path]:
    """Materialize the latest project result into a temp file for legacy callers.

    DEPRECATED: usare `get_result_as_tempfile_cm` (context manager) per
    cleanup deterministico. Questa funzione lascia il file su disco; il
    caller DEVE chiamarlo `.unlink()` quando ha finito. Senza cleanup,
    ogni chiamata accumula un file permanente sotto TEMP_DIR fino al
    disk fill.

    Pre-fix: nessun registry, nessun cleanup, nessuna deprecation
    warning — i caller dimenticavano la cancellazione e il sistema
    accumulava file orfani indefinitamente.
    Post-fix: emit DeprecationWarning + registrazione del path in un
    registry process-local cancellato all'`atexit`. Best-effort: non
    sopravvive a SIGKILL, ma elimina il leak per i graceful shutdown
    (~99% dei casi in prod).
    """
    import warnings
    warnings.warn(
        "get_result_as_tempfile lascia un file orfano: usare "
        "get_result_as_tempfile_cm (context manager) per cleanup "
        "deterministico.",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        row = store.get_latest_result_row(project_id)
        if not row or not row.id:
            return None

        data = store.get_result_data(row.id)
        if not data:
            return None

        tmp_path = make_cached_result_path(f".{row.format or 'csv'}")
        tmp_path.write_bytes(data)
        _register_for_atexit_cleanup(tmp_path)
        return tmp_path
    except Exception as exc:
        logger.warning(f"Errore lettura risultato dal DB ({project_id}): {exc}")
        return None


from contextlib import contextmanager


_ATEXIT_REGISTRY: list[Path] = []
_ATEXIT_REGISTERED = False


def _register_for_atexit_cleanup(path: Path) -> None:
    """Aggiunge `path` al registry process-local di cleanup atexit.

    Registry leggero (non thread-safe per design: list.append e' atomic
    in CPython grazie al GIL). Su shutdown graceful, l'handler cancella
    tutti i path raccolti. Su SIGKILL i file restano — quello e' il
    failure mode noto, accettabile per i caller deprecati.
    """
    global _ATEXIT_REGISTERED
    if not _ATEXIT_REGISTERED:
        import atexit
        atexit.register(_cleanup_atexit_registry)
        _ATEXIT_REGISTERED = True
    _ATEXIT_REGISTRY.append(path)


def _cleanup_atexit_registry() -> None:
    """Cancella i tempfile orfani al graceful shutdown."""
    for path in list(_ATEXIT_REGISTRY):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


@contextmanager
def get_result_as_tempfile_cm(project_id: str):
    """Context manager preferred: cleanup deterministico al `with` exit.

    Esempio:
        with get_result_as_tempfile_cm(project_id) as tmp_path:
            if tmp_path is None:
                return None
            process(tmp_path)
        # tmp_path cancellato automaticamente qui

    Mappa a Go come `defer os.Remove(tmpPath)`.
    """
    tmp_path: Optional[Path] = None
    try:
        row = store.get_latest_result_row(project_id)
        if not row or not row.id:
            yield None
            return

        data = store.get_result_data(row.id)
        if not data:
            yield None
            return

        tmp_path = make_cached_result_path(f".{row.format or 'csv'}")
        tmp_path.write_bytes(data)
        yield tmp_path
    except Exception as exc:
        logger.warning(f"Errore lettura risultato dal DB ({project_id}): {exc}")
        yield None
    finally:
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def get_result_info(project_id: str) -> Optional[store.ResultMetadataPayload]:
    """Return latest result metadata without blob bytes."""
    try:
        return store.get_latest_result(project_id)
    except Exception as exc:
        logger.warning("get_result_info fallito per %s: %s", project_id, exc)
        return None


__all__ = [
    "get_result_as_bytes",
    "get_result_as_tempfile",
    "get_result_as_tempfile_cm",
    "get_result_info",
]
