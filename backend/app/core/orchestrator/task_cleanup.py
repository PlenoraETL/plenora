"""Pulizia completa di un task: prima l'annullamento, poi la pulizia.

UI conferma finale F8/G3 (06/11/2026). Prima l'esecutore, chiudendo la
scheda, mandava in parallelo DELETE /tasks/{id} (annulla) e DELETE
/tasks/{id}/full-cleanup: nessun ordine, quindi quello che il worker scriveva
DOPO la pulizia (righe di log, record del task, output degli Eseguibili)
restava sul server.

Regola unica, per costruzione (``cleanup_task``, chiamata dall'endpoint
full-cleanup e dallo sweep del beat):

- la pulizia tocca SOLO un task terminale (success / failure / revoked): lo
  stato terminale lo scrive il worker dopo l'ultima scrittura del task
  (``workers/dramatiq_app`` after_process_message), quindi dopo non arriva
  piu' niente da togliere;
- un task non terminale si annulla (stessa revoca cooperativa di
  DELETE /tasks/{id}) e si aspetta al piu' TASK_CLEANUP_CANCEL_WAIT_SECONDS;
- se non si e' ancora fermato la pulizia resta IN CODA
  (``task_cleanup_requests``): lo sweep del beat (``run_deferred_cleanups``,
  ogni minuto) la fa appena il task e' terminale, o comunque dopo
  TASK_CLEANUP_DEFER_MAX_SECONDS (task mai finito: worker morto);
- una pulizia con passi non riusciti resta in coda e si riprova.

Il client fa UNA sola chiamata (full-cleanup): l'ordine non dipende piu' da
lui.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, List, Optional, TypedDict

from app.config import settings
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import output_store as _eseg_output_store
from app.core.orchestrator import log_cleanup as orchestrator_log_cleanup
from app.core.orchestrator.access_control import TASK_LOOKUP_ERRORS
from app.core.orchestrator import task_service as task_service_module
from app.core.orchestrator.router_mappers import task_result_to_dict as _task_result_to_dict
from app.core.results import cleanup as results_cleanup
from app.core.results import output_file_cleanup
from app.db import pg_connection
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = frozenset({"success", "failure", "revoked"})
_POLL_SECONDS = 0.25


def _status_value(task_info: Any) -> str:
    status = getattr(task_info, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def is_terminal(task_info: Any) -> bool:
    """Il task e' finito: nessun worker scrivera' piu' per lui."""
    return _status_value(task_info) in TERMINAL_STATUSES


def _setting_seconds(name: str) -> float:
    raw = settings.get(name, settings.DEFAULTS[name])
    return max(0.0, float(raw if raw is not None else settings.DEFAULTS[name]))


@dataclass(frozen=True)
class CleanupOutcome:
    """Esito di ``cleanup_task``: fatta (con i passi non riusciti) o in coda."""

    deferred: bool
    failed_steps: tuple[str, ...] = ()


# ── Coda delle pulizie differite (PostgreSQL) ──────────────────────────────

def defer_cleanup(task_id: str, *, max_seconds: Optional[float] = None) -> None:
    """Mette in coda la pulizia di un task non ancora terminale (idempotente)."""
    wait = _setting_seconds("TASK_CLEANUP_DEFER_MAX_SECONDS") if max_seconds is None else max_seconds
    pg_connection.execute(
        """
        INSERT INTO task_cleanup_requests (task_id, requested_at, deadline_at)
        VALUES (%s, now(), now() + (%s * interval '1 second'))
        ON CONFLICT (task_id) DO NOTHING
        """,
        (task_id, int(wait)),
    )


def deferred_cleanups() -> list[JsonObject]:
    """Le pulizie in coda, con ``overdue`` = scaduta l'attesa massima."""
    return list(pg_connection.fetchall(
        "SELECT task_id, (deadline_at <= now()) AS overdue "
        "FROM task_cleanup_requests ORDER BY requested_at",
    ))


def _record_attempt(task_id: str, failed_steps: List[str]) -> None:
    pg_connection.execute(
        "UPDATE task_cleanup_requests SET attempts = attempts + 1, last_error = %s "
        "WHERE task_id = %s",
        (",".join(failed_steps)[:500], task_id),
    )


def forget_deferred_cleanup(task_id: str) -> None:
    pg_connection.execute(
        "DELETE FROM task_cleanup_requests WHERE task_id = %s", (task_id,)
    )


# ── Pulizia di un task terminale ───────────────────────────────────────────

def _task_eseguibile_record(
    project_id: object,
    task_result_payload: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Identita' dell'Eseguibile dai metadati espliciti del risultato."""
    candidate = str(project_id or "")
    if not candidate or not bool(task_result_payload.get("is_eseguibile")):
        return None
    return eseguibili_db.get_eseguibile(candidate)


def task_project_id(task_info: Any) -> Optional[str]:
    """Progetto del task (dal task o dal suo risultato)."""
    project_id = getattr(task_info, "project_id", None)
    if not project_id and getattr(task_info, "result", None):
        project_id = _task_result_to_dict(task_info.result).get("project_id")
    return str(project_id) if project_id else None


def purge_terminal_task(task_id: str, task_info: Any) -> List[str]:
    """Toglie ogni traccia di UN task terminale; restituisce i passi falliti.

    Ogni passo e' best-effort (uno che fallisce non blocca gli altri) e
    riguarda solo questo task_id: mai i risultati di altri task del progetto.
    """
    if not is_terminal(task_info):
        raise RuntimeError(f"pulizia di un task non terminale rifiutata: {task_id}")
    project_id = task_project_id(task_info)
    task_result_payload: JsonObject = (
        _task_result_to_dict(task_info.result) if getattr(task_info, "result", None) else {}
    )
    failed_steps: List[str] = []

    # Output degli Eseguibili e owner sidecar (object storage): richiesto dal
    # contratto «nessuna traccia», ripetibile se lo storage e' giu'.
    eseguibile_record = _task_eseguibile_record(project_id, task_result_payload)
    if eseguibile_record is not None:
        try:
            _eseg_output_store.delete_task_outputs(
                task_id,
                group_id=str(eseguibile_record.get("group_id") or ""),
            )
        except Exception as e:
            logger.warning(f"cleanup: errore delete output eseguibile: {e}", exc_info=True)
            failed_steps.append("delete_eseguibile_outputs")

    # Record `result` di questo task (mai gli altri del progetto).
    try:
        result_id = task_result_payload.get("result_id")
        if result_id:
            if results_cleanup.delete_result(result_id):
                logger.info(f"cleanup: result_id '{result_id}' eliminato (task {task_id})")
        else:
            logger.debug(f"cleanup: task {task_id} senza result_id nel payload, skip results")
    except Exception as e:
        logger.warning(f"cleanup: errore delete_result: {e}", exc_info=True)
        failed_steps.append("delete_result")

    # File di output di questo task (non la cartella, condivisa tra task).
    try:
        output_file = task_result_payload.get("output_file")
        if output_file and project_id:
            try:
                if output_file_cleanup.delete_project_output_file(
                    project_id, output_file, root=settings.RESULTS_DIR,
                ):
                    logger.info(f"cleanup: file output rimosso: {output_file}")
            except ValueError:
                # Path traversal guard: fuori da RESULTS_DIR, salto sicuro.
                logger.warning("cleanup: output_file fuori da RESULTS_DIR, skip")
    except Exception as e:
        logger.warning(f"cleanup: errore rimozione output_file: {e}", exc_info=True)
        failed_steps.append("unlink_output_file")

    try:
        orchestrator_log_cleanup.delete_task_log_runs(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore delete log_runs: {e}", exc_info=True)
        failed_steps.append("delete_log_runs")

    try:
        task_service_module.task_service.forget_task_result(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore forget Celery: {e}", exc_info=True)
        failed_steps.append("celery_forget")

    if failed_steps:
        logger.warning(f"cleanup: task {task_id} partial — failed_steps={failed_steps}")
    return failed_steps


def cancel_and_wait(
    task_id: str,
    *,
    wait_seconds: Optional[float] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Annulla il task e ne aspetta lo stato terminale al piu' ``wait_seconds``.

    Restituisce l'ultimo stato letto (terminale o no).
    """
    service = task_service_module.task_service
    try:
        service.cancel_task(task_id)
    except task_service_module.TaskAlreadyTerminalError:
        pass
    wait = _setting_seconds("TASK_CLEANUP_CANCEL_WAIT_SECONDS") if wait_seconds is None else wait_seconds
    deadline = clock() + wait
    task_info = service.get_task_status(task_id)
    while not is_terminal(task_info) and clock() < deadline:
        sleep(_POLL_SECONDS)
        task_info = service.get_task_status(task_id)
    return task_info


def cleanup_task(task_id: str, task_info: Any, **wait_opts: Any) -> CleanupOutcome:
    """Regola unica: annulla e aspetta se serve, pulisci solo se terminale."""
    if not is_terminal(task_info):
        task_info = cancel_and_wait(task_id, **wait_opts)
    if not is_terminal(task_info):
        defer_cleanup(task_id)
        logger.info(f"cleanup: task {task_id} ancora in corso, pulizia in coda")
        return CleanupOutcome(deferred=True)
    failed = purge_terminal_task(task_id, task_info)
    if not failed:
        # Una richiesta in coda per lo stesso task (pagina chiusa prima) e' fatta.
        try:
            forget_deferred_cleanup(task_id)
        except TASK_LOOKUP_ERRORS:
            logger.warning(f"cleanup: coda non aggiornata per {task_id}", exc_info=True)
    else:
        # Passi non riusciti: la riga in coda fa riprovare lo sweep. Se anche
        # la coda non si scrive, l'esito parziale (207) resta comunque vero.
        try:
            defer_cleanup(task_id)
        except TASK_LOOKUP_ERRORS:
            logger.warning(f"cleanup: pulizia parziale di {task_id} non messa in coda", exc_info=True)
    return CleanupOutcome(deferred=False, failed_steps=tuple(failed))


class DeferredCleanupStats(TypedDict):
    """Esito di uno sweep: fatte, ancora in attesa del task, non riuscite."""

    done: int
    waiting: int
    failed: int


def run_deferred_cleanups() -> DeferredCleanupStats:
    """Sweep del beat: fa le pulizie in coda dei task ormai terminali.

    Un task ancora in corso resta in coda finche' non finisce o finche' non
    scade l'attesa massima (allora si pulisce comunque: worker morto, il task
    non scrivera' piu').
    """
    done = waiting = failed = 0
    for row in deferred_cleanups():
        task_id = str(row["task_id"])
        try:
            task_info = task_service_module.task_service.get_task_status(task_id)
        except TASK_LOOKUP_ERRORS:
            logger.warning(f"cleanup differita: stato non leggibile per {task_id}", exc_info=True)
            failed += 1
            continue
        if not is_terminal(task_info) and not row.get("overdue"):
            waiting += 1
            continue
        if not is_terminal(task_info):
            # Oltre l'attesa massima: si tratta come finito (revocato).
            task_info = _as_revoked(task_info)
        steps = purge_terminal_task(task_id, task_info)
        if steps:
            _record_attempt(task_id, steps)
            failed += 1
        else:
            forget_deferred_cleanup(task_id)
            done += 1
    return DeferredCleanupStats(done=done, waiting=waiting, failed=failed)


class _RevokedView:
    """Lo stesso task, letto come revocato (attesa massima scaduta)."""

    def __init__(self, task_info: Any) -> None:
        self._task_info = task_info
        self.status = "revoked"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._task_info, name)


def _as_revoked(task_info: Any) -> Any:
    return _RevokedView(task_info)


__all__ = [
    "CleanupOutcome",
    "TERMINAL_STATUSES",
    "cancel_and_wait",
    "cleanup_task",
    "defer_cleanup",
    "deferred_cleanups",
    "forget_deferred_cleanup",
    "is_terminal",
    "purge_terminal_task",
    "run_deferred_cleanups",
    "task_project_id",
]
