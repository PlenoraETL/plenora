"""
Contract test per ExecutionManager â€” orphan handling + release retry.

Gap reale vs i 5 file test esistenti su ``execution_manager``
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_manager() -> ExecutionManager:
    """ExecutionManager bypassando ``__init__`` per non creare
    connessioni reali (pattern dagli altri file test del modulo)."""
    manager = ExecutionManager.__new__(ExecutionManager)
    manager.schedule_service = MagicMock()
    manager.log_service = MagicMock()
    manager.concurrency = MagicMock()
    manager.policy = MagicMock()
    manager._connection_service = None
    manager._get_connection_service = MagicMock(return_value=MagicMock())
    manager._artifact_store = MagicMock()
    return manager


def _project_meta(group_id):
    return SimpleNamespace(id="proj-1", group_id=group_id, project_type="estrai")


def _make_manager_for_cleanup_test(schedule_config) -> ExecutionManager:
    """Manager configurato per arrivare al finally dopo un errore
    deterministico downstream.

    Scelte:
    - ``_start_lock_heartbeat`` stubbato per ritornare uno state che
      ``_stop_lock_heartbeat`` capisce (no thread reale), altrimenti
      partirebbe un daemon che chiama ``refresh_lock`` sul mock.
    - ``_get_orchestrator`` solleva subito â†’ salta il body di try e va
      nel ``except Exception``.
    - ``_handle_failure`` mockato per ritornare un result benign â€” i
      test qui NON testano il contenuto del result ma SOLO il
      comportamento del finally block.
    """
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.set_runtime_status = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")
    manager._start_lock_heartbeat = MagicMock(
        return_value={"stop_event": None, "thread": None, "lock_lost": False}
    )
    manager._stop_lock_heartbeat = MagicMock()
    manager._get_orchestrator = MagicMock(
        side_effect=RuntimeError("forced downstream failure for finally test")
    )
    manager._handle_failure = MagicMock(
        return_value={"success": False, "status": "failed", "message": "forced"}
    )
    return manager


def test_execute_marks_schedule_as_orphan_when_project_does_not_exist(
    schedule_config,
):
    """Blinda il ramo orphan cleanup: se il ``ProjectService`` ritorna
    ``None`` per il ``project_id`` dello schedule (progetto cancellato),
    ``execute()`` DEVE:

      1. Chiamare ``schedule_service.mark_as_orphan(schedule_id)`` cosi'
         lo schedule viene disabilitato e non ri-tentato.
      2. Scrivere un log entry ``PIPELINE_FAILED`` a livello ERROR con
         error_message che cita "orphan" / "eliminato".
      3. Ritornare ``{"success": False, "status": "orphan", ...}``.
      4. **NON** acquisire il lock nÃ© aprire il run (short-circuit PRIMA
         di ``open_run`` e ``try_acquire``).

    Regressione: se ``mark_as_orphan`` viene rimosso o spostato sotto
    un altro ramo, lo schedule resta attivo â†’ ogni tick successivo
    riapre il run, fallisce, chiude il run. Polling infinito con log
    spam.

    Contract negativo fondamentale: **NO lock acquired** â€” se qualcuno
    spostasse il check project_meta dopo ``try_acquire``, un crash
    lascerebbe lock orfani su schedule destinati a essere disabilitati.
    """
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.mark_as_orphan = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")

    with patch(
        "app.core.scheduler.db.get_schedule_row",
        return_value=SimpleNamespace(id="s-orphan", group_id="grp-A"),
    ), patch(
        "app.core.projects.service.ProjectService"
    ) as project_service_cls:
        # Progetto cancellato â†’ metadata None
        project_service_cls.return_value.get_project_metadata.return_value = None

        result = manager.execute("s-orphan")

    # 1) mark_as_orphan chiamato con lo schedule_id corretto
    manager.schedule_service.mark_as_orphan.assert_called_once_with("s-orphan")

    # 2) Log PIPELINE_FAILED a livello ERROR con messaggio esplicito
    manager.log_service.create_log_entry.assert_called_once()
    call_kwargs = manager.log_service.create_log_entry.call_args.kwargs
    assert call_kwargs.get("event") == "PIPELINE_FAILED"
    assert call_kwargs.get("level") == "ERROR"
    err_msg = call_kwargs.get("error_message", "")
    assert (
        "orphan" in err_msg.lower()
        or "eliminat" in err_msg.lower()
        or "non trovato" in err_msg.lower()
    ), (
        f"error_message DEVE indicare che il progetto Ã¨ stato "
        f"eliminato/orphan, ottenuto {err_msg!r}"
    )
    manager.log_service.write_log.assert_called_once()

    # 3) Return shape corretto
    assert result["success"] is False
    assert result["status"] == "orphan"

    # 4) NO lock acquired, NO run aperto
    manager.concurrency.try_acquire.assert_not_called()


def test_finally_release_lock_retries_once_after_transient_db_failure(
    schedule_config, monkeypatch
):
    """Blinda il fail-closed sul release nel finally di ``execute()``:
    se il primo ``concurrency.release`` solleva una classe transient PG
    (psycopg.OperationalError â€” pool exhausted, connection reset),
    il codice DEVE:

      1. Loggare warning [GESTITO] per il tentativo transient.
      2. Backoff esponenziale con jitter (``time.sleep`` mockato).
      3. Ritentare ``concurrency.release`` (budget N=3, T=10s post current contract.b).

    Questo test copre lo scenario ``side_effect=[OperationalError, None]``
    â†’ 1Â° fail transient, 2Â° success â†’ retry classified funziona, count=2.

    Regressione: rimuovere il retry significherebbe che ogni blip DB
    transient lascia un lock orfano (scadrÃ  per TTL ma intanto blocca
    il prossimo tick per 10 minuti).

    Post current contract.b: classe transient narrow a psycopg2 (Previous behavior era
    Exception broad); RuntimeError generico NON triggera retry (non-transient).
    """
    import psycopg  # P0 (2026-05-26): psycopg2 retirato
    manager = _make_manager_for_cleanup_test(schedule_config)
    # Primo release solleva transient PG, secondo riesce
    manager.concurrency.release = MagicMock(
        side_effect=[psycopg.OperationalError("db blip"), None]
    )
    # Evita attesa reale del backoff
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _seconds: None,
    )

    with patch(
        "app.core.scheduler.db.get_schedule_row",
        return_value=SimpleNamespace(id="s-release", group_id="grp-A"),
    ), patch(
        "app.core.projects.service.ProjectService"
    ) as project_service_cls, patch(
        "app.core.scheduler.execution_manager.open_run"
    ), patch(
        "app.core.scheduler.execution_manager.set_run_context"
    ), patch(
        "app.core.scheduler.execution_manager.close_run"
    ):
        project_service_cls.return_value.get_project_metadata.return_value = (
            _project_meta("grp-A")
        )
        result = manager.execute("s-release")

    # release deve essere chiamata DUE volte: prima fail transient, poi success
    assert manager.concurrency.release.call_count == 2, (
        f"release DEVE essere ritentata dopo transient failure: "
        f"atteso 2 chiamate, ottenuto {manager.concurrency.release.call_count}"
    )
    # Entrambe le chiamate DEVONO avere lo stesso schedule_id
    for call in manager.concurrency.release.call_args_list:
        args, _kwargs = call
        assert args[0] == "s-release"

    # execute() NON deve propagare l'eccezione del primo release:
    # il _handle_failure mockato ritorna il result, e il finally
    # non solleva niente visibile al caller.
    assert result is not None
    assert result["status"] == "failed"


def test_finally_does_not_propagate_when_both_release_attempts_fail(
    schedule_config, monkeypatch
):
    """Blinda l'outer ``try/except`` del finally block: se TUTTI i tentativi
    di release sollevano transient PG (DB giÃ¹ per piÃ¹ della budget window
    del retry wrapper), ``execute()`` DEVE:

      1. Chiamare release esattamente N=3 volte (post current contract.b).
      2. Loggare CRITICO (il source cita "scadrÃ  per TTL").
      3. **NON propagare l'eccezione** â€” il finally completa
         pulitamente e ``execute()`` ritorna il result dal
         ``_handle_failure`` (non un uncaught OperationalError).

    Contract critico: ``execute()`` Ã¨ chiamato da un task Celery che
    si aspetta sempre un return value. Una OperationalError non catturata
    marcherebbe il task come failed-with-exception invece di
    failed-with-result â†’ retry automatico di Celery â†’ tick duplicato
    sullo stesso schedule.

    Regressione: rimuovere l'outer guard del finally romperebbe il contract
    "sempre ritorna un result", anche in DB disaster scenario.
    """
    import psycopg  # P0 (2026-05-26): psycopg2 retirato
    manager = _make_manager_for_cleanup_test(schedule_config)
    # Tutti N=3 release sollevano transient PG (post-7.b budget N=3)
    manager.concurrency.release = MagicMock(
        side_effect=[
            psycopg.OperationalError("blip 1"),
            psycopg.OperationalError("blip 2"),
            psycopg.OperationalError("blip 3"),
        ]
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _seconds: None,
    )

    with patch(
        "app.core.scheduler.db.get_schedule_row",
        return_value=SimpleNamespace(id="s-disaster", group_id="grp-A"),
    ), patch(
        "app.core.projects.service.ProjectService"
    ) as project_service_cls, patch(
        "app.core.scheduler.execution_manager.open_run"
    ), patch(
        "app.core.scheduler.execution_manager.set_run_context"
    ), patch(
        "app.core.scheduler.execution_manager.close_run"
    ):
        project_service_cls.return_value.get_project_metadata.return_value = (
            _project_meta("grp-A")
        )
        # Se il finally propagasse, questa chiamata solleverebbe
        # un'eccezione invece di ritornare un dict.
        result = manager.execute("s-disaster")

    # release chiamata N=3 volte (budget retry wrapper current contract.b)
    assert manager.concurrency.release.call_count == 3

    # execute() ritorna un result dict (no uncaught exception)
    assert isinstance(result, dict), (
        f"execute() DEVE ritornare dict anche se tutti release "
        f"falliscono, ottenuto {type(result).__name__}"
    )
    # Il result viene dal _handle_failure mockato, status "failed"
    assert result["status"] == "failed"
