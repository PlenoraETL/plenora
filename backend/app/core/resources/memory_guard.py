"""
Helper puri per RSS/memory sampling e cleanup post-task.

"""

from __future__ import annotations

import ctypes
import gc
import logging
import sys
from typing import Optional

from app.core.resources.exceptions import MemoryLimitExceededError

logger = logging.getLogger(__name__)


def get_current_rss_bytes() -> Optional[int]:
    """Ritorna il RSS corrente del processo in byte, o None se
    psutil non e' disponibile."""
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
        return None


def check_worker_rss(
    cap_bytes: int,
    stage: str,
) -> None:
    """Controlla il RSS del worker e solleva se supera il cap.

    Args:
        cap_bytes: limite RSS in byte. Se <= 0 il guard e' disabilitato.
        stage: nome dello stage per diagnostica (es. "pipeline_execute").

    Raises:
        MemoryLimitExceededError: se RSS > cap_bytes.
    """
    if cap_bytes <= 0:
        return

    rss = get_current_rss_bytes()
    if rss is None:
        logger.debug("RSS guard skipped: psutil non disponibile")
        return

    if rss > cap_bytes:
        raise MemoryLimitExceededError(
            observed_bytes=rss,
            limit_bytes=cap_bytes,
            path_basename="worker_process",
            stage=stage,
        )


def cleanup_worker_memory(
    *,
    run_gc: bool = True,
    run_malloc_trim: bool = True,
) -> dict[str, int | bool | None]:
    """Best-effort cleanup della memoria del processo worker.

    ``gc.collect`` libera oggetti Python ciclici. Su Linux/glibc,
    ``malloc_trim(0)`` prova a restituire all'OS arena libc rimaste libere
    dopo task pandas/pyarrow pesanti. Il cleanup non deve mai propagare:
    serve a rendere riutilizzabile il worker, non a cambiare l'esito del task.
    """
    before_rss = get_current_rss_bytes()
    collected = 0
    malloc_trim_ok = False

    if run_gc:
        try:
            collected = int(gc.collect())
        except Exception:
            logger.warning("Worker memory cleanup: gc.collect fallito", exc_info=True)

    if run_malloc_trim and sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL("libc.so.6")
            malloc_trim_ok = bool(libc.malloc_trim(0))
        except Exception:
            logger.debug(
                "Worker memory cleanup: malloc_trim non disponibile",
                exc_info=True,
            )

    after_rss = get_current_rss_bytes()
    return {
        "before_rss": before_rss,
        "after_rss": after_rss,
        "collected": collected,
        "malloc_trim": malloc_trim_ok,
    }
