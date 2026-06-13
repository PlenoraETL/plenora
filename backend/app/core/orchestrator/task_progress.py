"""Reporter unificato di stato/progress/esito task (Plenora 2.0, Fase 2).

Scrive su ``task_result_store`` (PostgreSQL) keyed-by-``task_id``, senza
dipendere dal contesto Celery ``self``: il wrapper Celery passa
``self.request.id``, l'actor Dramatiq (chiusura Fase 1) passa il
``current_message_id()``. E' il rimpiazzo del result backend Redis sul
lato scrittura.

Best-effort by design: durante la transizione (finche' la LETTURA non
migra a PG in F2.3) un fallimento di scrittura su PG non deve alterare
l'esito del task — Celery conserva ancora il proprio backend. Gli errori
vengono loggati, non propagati.
"""

from __future__ import annotations

from typing import Optional

from app.core.orchestrator.task_result_store import (
    TaskResultStore,
    default_task_result_store,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

PROGRESS_STATE = "PROGRESS"
SUCCESS_STATE = "SUCCESS"
FAILURE_STATE = "FAILURE"

# Stati Celery terminali su cui ha senso scrivere l'esito finale. RETRY e
# REVOKED non sono esiti definitivi: il task ripartira' o e' stato annullato.
_FINAL_CELERY_STATES = frozenset({SUCCESS_STATE, FAILURE_STATE})

_default_store: Optional[TaskResultStore] = None


def _resolve_store(store: Optional[TaskResultStore]) -> TaskResultStore:
    if store is not None:
        return store
    global _default_store
    if _default_store is None:
        _default_store = default_task_result_store()
    return _default_store


def report_task_progress(
    task_id: Optional[str],
    percent: int,
    message: str,
    *,
    is_error: bool = False,
    name: Optional[str] = None,
    store: Optional[TaskResultStore] = None,
) -> None:
    """Registra un avanzamento (stato PROGRESS) per il task.

    ``percent`` negativo indica errore (convenzione storica dei task);
    la payload normalizza a >= 0 e setta ``is_error`` (vedi
    ``_update_progress`` in carica/pipeline)."""
    if not task_id:
        return
    payload = {
        "progress": max(0, int(percent)),
        "message": message,
        "is_error": bool(is_error) or percent < 0,
    }
    try:
        _resolve_store(store).put(
            task_id, state=PROGRESS_STATE, payload=payload, name=name
        )
    except Exception:
        # Best-effort safety-net: un errore di scrittura PG (DB giu',
        # serializzazione) NON deve far fallire il task; il polling Celery
        # resta valido finche' la lettura non migra a PG (F2.3).
        logger.warning(
            "report_task_progress fallita task_id=%s", task_id, exc_info=True
        )


def finalize_task_result(
    task_id: Optional[str],
    *,
    state: str,
    payload: Optional[dict],
    name: Optional[str] = None,
    store: Optional[TaskResultStore] = None,
) -> None:
    """Scrive l'esito finale del task (sovrascrive la riga di progress)."""
    if not task_id:
        return
    try:
        _resolve_store(store).put(
            task_id, state=state, payload=payload, name=name
        )
    except Exception:
        # Best-effort safety-net (come report_task_progress): l'esito su PG
        # non deve mai propagare e alterare il risultato del task.
        logger.warning(
            "finalize_task_result fallita task_id=%s", task_id, exc_info=True
        )


def finalize_from_celery(
    task_id: Optional[str],
    status: str,
    retval: object,
    einfo: object,
    *,
    name: Optional[str] = None,
    store: Optional[TaskResultStore] = None,
) -> None:
    """Adatta i parametri di ``Task.after_return`` allo store.

    Solo gli stati terminali (SUCCESS/FAILURE) vengono persistiti. Su
    SUCCESS la payload e' il dict di ritorno del task (semantica Celery
    ``result``); su FAILURE una payload minima e sanitizzata (niente
    ``str(exc)`` raw nel canale letto dal FE — vedi get_task_status)."""
    if status not in _FINAL_CELERY_STATES:
        return
    if status == SUCCESS_STATE:
        payload = retval if isinstance(retval, dict) else {"result": retval}
        finalize_task_result(
            task_id, state=SUCCESS_STATE, payload=payload, name=name, store=store
        )
        return
    exc = getattr(einfo, "exception", None)
    payload = {
        "success": False,
        "error": type(exc).__name__ if exc is not None else "TaskFailure",
    }
    finalize_task_result(
        task_id, state=FAILURE_STATE, payload=payload, name=name, store=store
    )


__all__ = [
    "FAILURE_STATE",
    "PROGRESS_STATE",
    "SUCCESS_STATE",
    "finalize_from_celery",
    "finalize_task_result",
    "report_task_progress",
]
