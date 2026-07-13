"""Reporter unificato di stato/progress/esito task.

Scrive su ``task_result_store`` (PostgreSQL) keyed-by-``task_id``, senza
dipendere dal contesto del worker: l'actor Dramatiq passa il
``current_message_id()``.

Best-effort by design: un fallimento di scrittura su PG non deve alterare
l'esito del task. Gli errori vengono loggati, non propagati.
"""

from __future__ import annotations

from typing import Optional

from app.core.orchestrator.task_result_store import (
    TaskResultStore,
    default_task_result_store,
)
from app.core.orchestrator.task_revocation_store import (
    TaskRevocationStore,
    default_task_revocation_store,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

PROGRESS_STATE = "PROGRESS"
SUCCESS_STATE = "SUCCESS"
FAILURE_STATE = "FAILURE"
REVOKED_STATE = "REVOKED"

# Stati Celery terminali su cui ha senso scrivere l'esito finale. RETRY non
# e' un esito definitivo (il task ripartira'); REVOKED e' gestito a parte.
_FINAL_CELERY_STATES = frozenset({SUCCESS_STATE, FAILURE_STATE})

_default_store: Optional[TaskResultStore] = None
_revocation_store: Optional[TaskRevocationStore] = None


class TaskRevoked(BaseException):
    """Annullamento cooperativo di un task.

    Sottoclasse di ``BaseException`` (non ``Exception``) di proposito: deve
    attraversare i ``except Exception`` dei corpi task (eseguendone i
    ``finally`` per rilasciare lock/connessioni) e arrivare all'actor.
    Gli actor la dichiarano in ``throws=(TaskRevoked,)`` cosi' Dramatiq non
    ritenta e logga l'evento come atteso; ``finalize_from_celery`` la mappa
    sullo stato terminale ``REVOKED``.
    """

    def __init__(self, task_id: Optional[str] = None) -> None:
        super().__init__(task_id or "")
        self.task_id = task_id


def _resolve_store(store: Optional[TaskResultStore]) -> TaskResultStore:
    if store is not None:
        return store
    global _default_store
    if _default_store is None:
        _default_store = default_task_result_store()
    return _default_store


def _resolve_revocation_store(
    store: Optional[TaskRevocationStore],
) -> TaskRevocationStore:
    if store is not None:
        return store
    global _revocation_store
    if _revocation_store is None:
        _revocation_store = default_task_revocation_store()
    return _revocation_store


def raise_if_task_revoked(
    task_id: Optional[str],
    *,
    store: Optional[TaskRevocationStore] = None,
) -> None:
    """Solleva ``TaskRevoked`` se il task e' stato annullato.

    Punto di annullamento cooperativo: i corpi task lo attraversano via i
    checkpoint di progress (``report_task_progress``). Fail-soft: un errore
    di lettura del flag non revoca (vedi ``is_revoked``)."""
    if not task_id:
        return
    if _resolve_revocation_store(store).is_revoked(task_id):
        raise TaskRevoked(task_id)


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
    # Checkpoint di annullamento cooperativo: se il task e' stato revocato,
    # solleva TaskRevoked PRIMA di scrivere il progress. Fuori dal try
    # best-effort sotto: la TaskRevoked deve propagare, non essere inghiottita.
    raise_if_task_revoked(task_id)
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
        # serializzazione) NON deve far fallire il task.
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
        resolved_store = _resolve_store(store)
        put_terminal_with_run = getattr(
            resolved_store,
            "put_terminal_with_run",
            None,
        )
        if (
            callable(put_terminal_with_run)
            and state in {SUCCESS_STATE, FAILURE_STATE, REVOKED_STATE, "RETRY"}
            and isinstance(payload, dict)
            and payload.get("run_id")
        ):
            put_terminal_with_run(
                task_id,
                state=state,
                payload=payload,
                name=name,
            )
        else:
            resolved_store.put(
                task_id, state=state, payload=payload, name=name
            )
    except Exception:
        # Best-effort safety-net (come report_task_progress): l'esito su PG
        # non deve mai propagare e alterare il risultato del task.
        logger.warning(
            "finalize_task_result fallita task_id=%s", task_id, exc_info=True
        )


def finalize_revoked(
    task_id: Optional[str],
    *,
    name: Optional[str] = None,
    store: Optional[TaskResultStore] = None,
) -> None:
    """Scrive l'esito terminale ``REVOKED`` (task annullato cooperativamente)."""
    payload = {
        "success": False,
        "status": "revoked",
        "message": "Task annullato",
    }
    if name == "pipeline.execute" and task_id:
        payload["run_id"] = task_id
    finalize_task_result(
        task_id,
        state=REVOKED_STATE,
        payload=payload,
        name=name,
        store=store,
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
    """Adatta i parametri di ``Task.after_return`` (o del middleware Dramatiq)
    allo store.

    Un'eccezione ``TaskRevoked`` mappa sullo stato terminale ``REVOKED``
    (annullamento cooperativo). Altrimenti solo SUCCESS/FAILURE vengono
    persistiti: su SUCCESS la payload e' il dict di ritorno del task
    (semantica Celery ``result``); su FAILURE una payload minima e
    sanitizzata (niente ``str(exc)`` raw nel canale letto dal FE)."""
    exc = getattr(einfo, "exception", None)
    if isinstance(exc, TaskRevoked):
        finalize_revoked(task_id, name=name, store=store)
        return
    if status not in _FINAL_CELERY_STATES:
        return
    if status == SUCCESS_STATE:
        payload = retval if isinstance(retval, dict) else {"result": retval}
        resolved_state = (
            FAILURE_STATE
            if isinstance(payload, dict) and payload.get("success") is False
            else SUCCESS_STATE
        )
        finalize_task_result(
            task_id, state=resolved_state, payload=payload, name=name, store=store
        )
        return
    payload = {
        "success": False,
        "error": type(exc).__name__ if exc is not None else "TaskFailure",
    }
    if name == "pipeline.execute" and task_id:
        payload["run_id"] = task_id
    finalize_task_result(
        task_id, state=FAILURE_STATE, payload=payload, name=name, store=store
    )


def _set_revocation_store_for_tests(store: Optional[TaskRevocationStore]) -> None:
    global _revocation_store
    _revocation_store = store


def _reset_revocation_store_for_tests() -> None:
    global _revocation_store
    _revocation_store = None


__all__ = [
    "FAILURE_STATE",
    "PROGRESS_STATE",
    "REVOKED_STATE",
    "SUCCESS_STATE",
    "TaskRevoked",
    "finalize_from_celery",
    "finalize_revoked",
    "finalize_task_result",
    "raise_if_task_revoked",
    "report_task_progress",
]
