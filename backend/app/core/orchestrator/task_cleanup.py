"""Pulizia completa di un task (DELETE /tasks/{id}/full-cleanup, esecutore).

Semplificazione H1–H7 (06/11/2026): niente coda, niente sweep, niente
attese. Tre casi, decisi da cosa c'e' sul server:

- NON RESTA NIENTE (nessun record di stato e nessun metadato del task):
  ``nothing_left`` -> 204 subito, senza revoca ne' attesa (H2). E' il
  marcatore «gia' pulito», affidabile per costruzione: una pulizia riuscita
  toglie per ultimi i metadati e poi il record di stato, quindi «niente» vuol
  dire pulito (o mai esistito, o scaduto), mai «in coda»: un task in coda ha
  i metadati dal momento in cui e' accettato;
- TASK TERMINALE (success / failure / revoked): si pulisce adesso
  (``purge_terminal_task``). Il record di stato e i metadati si tolgono SOLO
  se tutti gli altri passi sono riusciti: con un 207 il record resta, e
  ripetere la pulizia rifa' i passi mancanti con il payload vero (H1: prima
  il record si toglieva comunque e la ripetizione vedeva un task PENDING
  senza payload);
- TASK IN CORSO: si annota la richiesta SUL TASK (``request_cleanup``, chiave
  con scadenza nel KV condiviso), lo si annulla e si risponde 202 subito. La
  pulizia la fa chi porta il task allo stato terminale, dopo averlo scritto
  (``finish_requested_cleanup``): il worker a fine task
  (workers/dramatiq_app, _TaskResultMiddleware) o il beat che chiude i task
  di un worker morto (workers/beat_scheduler._recover_runtime_leases). La
  richiesta si annota PRIMA di rileggere lo stato: se il task e' finito nel
  frattempo, la pulizia la fa subito questa chiamata (nessuna finestra in
  cui nessuno dei due la vede).

Ogni passo riguarda solo questo task_id, mai i risultati di altri task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, List, Optional

from app.config import settings
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import output_store as _eseg_output_store
from app.core.orchestrator import log_cleanup as orchestrator_log_cleanup
from app.core.orchestrator import task_service as task_service_module
from app.core.orchestrator.access_control import TASK_LOOKUP_ERRORS
from app.core.orchestrator.router_mappers import task_result_to_dict as _task_result_to_dict
from app.core.results import cleanup as results_cleanup
from app.core.results import output_file_cleanup
from app.shared.contracts.payloads import JsonObject
from app.shared.kv import KeyValueStore, KeyValueStoreUnavailable, PgKeyValueStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = frozenset({"success", "failure", "revoked"})
_REQUEST_PREFIX = "plenora:orchestrator:task_cleanup:"
_requests: KeyValueStore = PgKeyValueStore()


def _status_value(task_info: Any) -> str:
    status = getattr(task_info, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def is_terminal(task_info: Any) -> bool:
    """Il task e' finito: nessun worker scrivera' piu' per lui."""
    return _status_value(task_info) in TERMINAL_STATUSES


@dataclass(frozen=True)
class CleanupOutcome:
    """Esito di ``cleanup_task``: fatta (con i passi non riusciti) o richiesta."""

    deferred: bool
    failed_steps: tuple[str, ...] = ()


# ── «Gia' pulito» ──────────────────────────────────────────────────────────

def nothing_left(task_id: str) -> bool:
    """Nessun record di stato e nessun metadato: non c'e' niente da pulire.

    Il record si legge per primo e in modo fail-loud (DB giu' -> eccezione,
    mai «gia' pulito» per un errore).
    """
    service = task_service_module.task_service
    stored = service.runtime.get_result(task_id)
    if stored.info is not None or str(stored.state or "PENDING").upper() != "PENDING":
        return False
    return service.metadata_store.get(task_id) is None


# ── Richiesta di pulizia annotata sul task in corso ────────────────────────

def request_cleanup(task_id: str) -> None:
    """Annota sul task che va pulito appena e' terminale (scade col task)."""
    _requests.set(_REQUEST_PREFIX + task_id, "1", ttl_seconds=int(settings.TASK_RESULT_EXPIRES))


def cleanup_requested(task_id: str) -> bool:
    return _requests.get(_REQUEST_PREFIX + task_id) is not None


def _forget_request(task_id: str) -> None:
    _requests.delete(_REQUEST_PREFIX + task_id)


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

    I passi sui dati sono best-effort (uno che fallisce non blocca gli
    altri). Metadati e record di stato si tolgono per ultimi e solo se tutti
    i passi sui dati sono riusciti (H1): finche' qualcosa resta, il record
    porta il payload che serve a ripetere la pulizia.
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

    if failed_steps:
        logger.warning(f"cleanup: task {task_id} partial — failed_steps={failed_steps}")
        return failed_steps

    # Per ultimi: metadati, poi record di stato (dopo, il task e' «niente»).
    service = task_service_module.task_service
    try:
        service.metadata_store.delete(task_id)
        service.forget_task_result(task_id)
    except Exception as e:
        logger.warning(f"cleanup: errore forget Celery: {e}", exc_info=True)
        return ["celery_forget"]
    return []


def _purge_and_close(task_id: str, task_info: Any) -> List[str]:
    failed = purge_terminal_task(task_id, task_info)
    if not failed:
        _forget_request(task_id)
    return failed


def cleanup_task(task_id: str, task_info: Any) -> CleanupOutcome:
    """La regola dell'endpoint: pulisci se terminale, altrimenti annota e annulla."""
    if not is_terminal(task_info):
        service = task_service_module.task_service
        request_cleanup(task_id)  # prima di rileggere lo stato (vedi modulo)
        try:
            service.cancel_task(task_id)
        except task_service_module.TaskAlreadyTerminalError:
            pass
        task_info = service.get_task_status(task_id)
        if not is_terminal(task_info):
            logger.info(f"cleanup: task {task_id} in corso, annullato; lo pulisce chi lo chiude")
            return CleanupOutcome(deferred=True)
    return CleanupOutcome(deferred=False, failed_steps=tuple(_purge_and_close(task_id, task_info)))


def finish_requested_cleanup(task_id: str) -> Optional[List[str]]:
    """Dopo lo stato terminale: fa la pulizia se era stata chiesta.

    None = nessuna richiesta (o task non terminale); altrimenti i passi non
    riusciti (lista vuota = fatta). Una pulizia non riuscita resta richiesta:
    la ripete la prossima chiamata dell'esecutore (la sua annotazione resta).
    """
    try:
        if not cleanup_requested(task_id):
            return None
        task_info = task_service_module.task_service.get_task_status(task_id)
        if not is_terminal(task_info):
            return None
        return _purge_and_close(task_id, task_info)
    except (KeyValueStoreUnavailable, *TASK_LOOKUP_ERRORS):
        logger.warning(f"cleanup: pulizia richiesta non fatta per {task_id}", exc_info=True)
        return None


__all__ = [
    "CleanupOutcome",
    "TERMINAL_STATUSES",
    "cleanup_requested",
    "cleanup_task",
    "finish_requested_cleanup",
    "is_terminal",
    "nothing_left",
    "purge_terminal_task",
    "request_cleanup",
    "task_project_id",
]
