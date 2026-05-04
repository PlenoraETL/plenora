"""
Helper puri per RSS/memory sampling pre-stage.

BE-GO-READY-MEMORY-OOM-002-WORKER-RSS-SAMPLING.

Funzioni pure: leggono RSS via psutil (opzionale), confrontano con cap,
sollevano MemoryLimitExceededError se superato. Nessun import FastAPI/web.
Il mapping HTTP vive nel boundary handler in app/main.py.

psutil e' gia' dipendenza (requirements.txt), ma il fallback e' safe:
se psutil non e' importabile, il guard e' silenziosamente disabilitato
(log debug, no raise).
"""

from __future__ import annotations

import logging
from typing import Optional

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
        from app.shared.exceptions import MemoryLimitExceededError
        raise MemoryLimitExceededError(
            observed_bytes=rss,
            limit_bytes=cap_bytes,
            path_basename="worker_process",
            stage=stage,
        )
