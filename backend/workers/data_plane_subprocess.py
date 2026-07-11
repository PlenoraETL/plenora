"""Isolamento cross-platform dei task data-plane in processi usa-e-getta.

Il processo Dramatiq resta un agent leggero. Per ogni task abilitato crea un
interprete Python con start method ``spawn``: niente pool DB ereditati e stesso
comportamento su Windows, Linux e container. Il child restituisce al parent
solo un envelope piccolo; DataFrame e allocator nativi muoiono col processo.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
import traceback
from types import SimpleNamespace
from typing import Any, Callable


PIPELINE_EXECUTE_TASK = "pipeline.execute"
PIPELINE_PREVIEW_TASK = "pipeline.preview"
SUPPORTED_TASKS = frozenset({PIPELINE_EXECUTE_TASK, PIPELINE_PREVIEW_TASK})
_POLL_INTERVAL_SECONDS = 0.2
_EXIT_GRACE_SECONDS = 5.0


class DataPlaneChildError(RuntimeError):
    """Il processo data-plane e' terminato senza un risultato valido."""


class DataPlaneChildTimeout(DataPlaneChildError):
    """Il processo data-plane ha superato il timeout configurato."""


def _resolve_task_body(task_name: str) -> Callable[..., object]:
    """Allowlist esplicita dei corpi eseguibili nel child."""
    if task_name == PIPELINE_EXECUTE_TASK:
        from workers.tasks.pipeline_task import execute_pipeline_task

        return execute_pipeline_task
    if task_name == PIPELINE_PREVIEW_TASK:
        from workers.tasks.pipeline_task import preview_pipeline_task

        return preview_pipeline_task
    raise ValueError(f"Task data-plane subprocess non supportato: {task_name}")


def _child_entry(
    send_connection: Any,
    task_name: str,
    task_id: str,
    kwargs: dict[str, object],
    request_id: str,
) -> None:
    """Entry point spawn-safe: esegue il task e invia un solo envelope."""
    try:
        from app.config_env_loader import load_backend_dotenv
        from app.core.orchestrator.task_progress import TaskRevoked
        from app.utils.logger import set_request_id

        load_backend_dotenv()
        set_request_id(request_id or task_id)
        task_body = _resolve_task_body(task_name)
        shim = SimpleNamespace(
            request=SimpleNamespace(id=task_id),
            name=task_name,
        )
        try:
            result = task_body(shim, **kwargs)
            envelope = {"kind": "result", "result": result}
        except TaskRevoked as exc:
            envelope = {
                "kind": "revoked",
                "task_id": exc.task_id or task_id,
            }
        except BaseException as exc:
            envelope = {
                "kind": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        send_connection.send(envelope)
    except BaseException as exc:
        # Copre errori di import/bootstrap precedenti al task body.
        try:
            send_connection.send(
                {
                    "kind": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass
    finally:
        try:
            send_connection.close()
        except Exception:
            pass


def _stop_process(process: Any) -> None:
    """Termina un child bloccato, con fallback kill cross-platform."""
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=_EXIT_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(timeout=_EXIT_GRACE_SECONDS)


def _remote_error_message(envelope: dict[str, object]) -> str:
    error_type = str(envelope.get("error_type") or "ChildError")
    message = str(envelope.get("message") or "errore senza dettaglio")
    return f"Data-plane child {error_type}: {message}"


def _receive_if_available(connection: Any, timeout: float) -> object | None:
    try:
        if connection.poll(timeout):
            return connection.recv()
    except (EOFError, OSError):
        return None
    return None


def execute_task_in_subprocess(
    *,
    task_name: str,
    task_id: str,
    kwargs: dict[str, object],
    timeout_seconds: int,
    request_id: str = "",
    mp_context: Any = None,
) -> object:
    """Esegue un task allowlisted in un nuovo processo ``spawn``.

    Il parent riceve l'envelope prima del join, evitando deadlock se il
    risultato supera il buffer della pipe. L'ACK RabbitMQ avverra' soltanto
    dopo il ritorno di questa funzione e la finalizzazione middleware.
    """
    if task_name not in SUPPORTED_TASKS:
        raise ValueError(f"Task data-plane subprocess non supportato: {task_name}")
    if not task_id:
        raise ValueError("task_id obbligatorio per executor subprocess")

    timeout = max(1, int(timeout_seconds))
    context = mp_context or mp.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_child_entry,
        args=(send_connection, task_name, task_id, dict(kwargs), request_id),
        name=f"plenora-data-{task_id[:12]}",
    )
    try:
        process.start()
    except BaseException as exc:
        receive_connection.close()
        send_connection.close()
        raise DataPlaneChildError(
            f"Impossibile avviare il child {task_name} {task_id}: {exc}"
        ) from exc
    send_connection.close()

    deadline = time.monotonic() + timeout
    envelope: dict[str, object] | None = None
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            received = _receive_if_available(
                receive_connection,
                min(_POLL_INTERVAL_SECONDS, max(0.0, remaining)),
            )
            if received is not None:
                envelope = received
                break
            if not process.is_alive():
                # Ultima poll dopo l'exit: il feeder della pipe puo' arrivare
                # poco dopo l'aggiornamento dell'exitcode.
                received = _receive_if_available(
                    receive_connection,
                    _POLL_INTERVAL_SECONDS,
                )
                if received is not None:
                    envelope = received
                break

        if envelope is None and process.is_alive():
            _stop_process(process)
            raise DataPlaneChildTimeout(
                f"Task {task_name} {task_id} oltre timeout {timeout}s"
            )

        process.join(timeout=_EXIT_GRACE_SECONDS)
        if process.is_alive():
            _stop_process(process)
            raise DataPlaneChildError(
                f"Task {task_name} {task_id}: child non termina dopo il risultato"
            )
        if envelope is None:
            raise DataPlaneChildError(
                f"Task {task_name} {task_id}: child exit={process.exitcode} "
                "senza envelope"
            )
    finally:
        receive_connection.close()
        if process.is_alive():
            _stop_process(process)

    if not isinstance(envelope, dict):
        raise DataPlaneChildError(
            f"Task {task_name} {task_id}: envelope child non valido"
        )

    kind = envelope.get("kind")
    if kind == "result":
        if process.exitcode not in (0, None):
            raise DataPlaneChildError(
                f"Task {task_name} {task_id}: result ricevuto ma "
                f"child exit={process.exitcode}"
            )
        return envelope.get("result")
    if kind == "revoked":
        from app.core.orchestrator.task_progress import TaskRevoked

        raise TaskRevoked(str(envelope.get("task_id") or task_id))
    if kind == "error":
        logging.getLogger(__name__).error(
            "Data-plane child %s %s remote traceback:\n%s",
            task_name,
            task_id,
            envelope.get("traceback") or "<assente>",
        )
        raise DataPlaneChildError(_remote_error_message(envelope))
    raise DataPlaneChildError(
        f"Task {task_name} {task_id}: kind envelope sconosciuto {kind!r}"
    )


__all__ = [
    "DataPlaneChildError",
    "DataPlaneChildTimeout",
    "PIPELINE_EXECUTE_TASK",
    "SUPPORTED_TASKS",
    "execute_task_in_subprocess",
]
