"""Contract test per ExecutionManager — orphan handling + release retry.

Gap reale vs i 5 file test esistenti su ``execution_manager``
(``contract``, ``heartbeat``, ``lock_lost``, ``lock_lost_propagation``,
``scheduled_for_persist`` — 23 test totali): nessuno copre i due rami
mappati sulle priorità Batch 3:

- **orphan cleanup post-task-kill**: il progetto viene cancellato mentre
  lo schedule è attivo. ``execute()`` incontra ``project_meta is None``
  e deve: (1) marcare lo schedule come orphan, (2) scrivere log
  ``PIPELINE_FAILED``, (3) ritornare ``status="orphan"`` SENZA acquisire
  il lock né aprire il run. Regression guard: se qualcuno rimuove
  ``mark_as_orphan``, lo schedule resta attivo ma fallisce ogni tick
  successivo → job polling infinito su progetto inesistente.

- **fail-closed DB/lock backend** sul release finale: il finally di
  ``execute()`` tenta il release; se il primo solleva, dorme 1s e
  ritenta. Se il secondo solleva, logga CRITICO e procede comunque
  (il lock "scadrà per TTL"). Regression guard: rimuovere il retry
  lascerebbe lock orfani in ogni blip DB transient; rimuovere il
  ``try/except`` esterno farebbe propagare l'eccezione, corrompendo
  il contract che ``execute()`` sempre ritorna un result dict.

Invariants blindati:

- §1.6 scheduler fail-closed: esecuzione su progetto inesistente
  NON può silentemente continuare né lasciare lo schedule in stato
  "running".
- §1.3 idempotenza release: il contract è "sempre rilascia o segnala
  che TTL scadrà". Nessun ramo del finally deve propagare exception.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# ─── Helpers ─────────────────────────────────────────────────────────────


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
    - ``_get_orchestrator`` solleva subito → salta il body di try e va
      nel ``except Exception``.
    - ``_handle_failure`` mockato per ritornare un result benign — i
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


# ─── 1) Orphan project: mark_as_orphan + log + early return ──────────────


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
      4. **NON** acquisire il lock né aprire il run (short-circuit PRIMA
         di ``open_run`` e ``try_acquire``).

    Regressione: se ``mark_as_orphan`` viene rimosso o spostato sotto
    un altro ramo, lo schedule resta attivo → ogni tick successivo
    riapre il run, fallisce, chiude il run. Polling infinito con log
    spam.

    Contract negativo fondamentale: **NO lock acquired** — se qualcuno
    spostasse il check project_meta dopo ``try_acquire``, un crash
    lascerebbe lock orfani su schedule destinati a essere disabilitati.
    """
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.mark_as_orphan = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")

    with patch(
        "app.db.db_service.get_schedule",
        return_value={"id": "s-orphan", "group_id": "grp-A"},
    ), patch(
        "app.services.project_service.ProjectService"
    ) as project_service_cls:
        # Progetto cancellato → metadata None
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
        f"error_message DEVE indicare che il progetto è stato "
        f"eliminato/orphan, ottenuto {err_msg!r}"
    )
    manager.log_service.write_log.assert_called_once()

    # 3) Return shape corretto
    assert result["success"] is False
    assert result["status"] == "orphan"

    # 4) NO lock acquired, NO run aperto
    manager.concurrency.try_acquire.assert_not_called()


# ─── 2) Release retry nel finally dopo transient failure ─────────────────


def test_finally_release_lock_retries_once_after_transient_db_failure(
    schedule_config, monkeypatch
):
    """Blinda il fail-closed sul release nel finally di ``execute()``:
    se il primo ``concurrency.release`` solleva (DB blip transient),
    il codice DEVE:

      1. Loggare l'errore come GESTITO.
      2. ``time.sleep(1)`` (retry backoff).
      3. Ritentare ``concurrency.release`` una seconda volta.

    Questo test verifica il retry chiamando release con
    ``side_effect=[RuntimeError, None]`` → primo fallisce, secondo
    riesce. Il counter di chiamate DEVE essere 2.

    Regressione: rimuovere il retry significherebbe che ogni blip DB
    transient lascia un lock orfano (che scadrà per TTL ma intanto
    blocca il prossimo tick per 10 minuti). Mobilitare il team oncall
    per ogni blip è troppo.

    ``time.sleep`` è monkeypatched a no-op per non bloccare il test 1s.
    """
    manager = _make_manager_for_cleanup_test(schedule_config)
    # Primo release solleva, secondo riesce
    manager.concurrency.release = MagicMock(side_effect=[RuntimeError("db blip"), None])
    # Evita 1s di attesa reale
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _seconds: None,
    )

    with patch(
        "app.db.db_service.get_schedule",
        return_value={"id": "s-release", "group_id": "grp-A"},
    ), patch(
        "app.services.project_service.ProjectService"
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

    # release deve essere chiamata DUE volte: prima fail, poi success
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


# ─── 3) Release entrambi i tentativi falliti: log + no propagation ──────


def test_finally_does_not_propagate_when_both_release_attempts_fail(
    schedule_config, monkeypatch
):
    """Blinda l'outer ``try/except`` del finally block: se ENTRAMBI i
    tentativi di release sollevano (DB giù per più di 1s), ``execute()``
    DEVE:

      1. Chiamare release esattamente 2 volte (primo + retry).
      2. Loggare CRITICO (il source cita "scadrà per TTL").
      3. **NON propagare l'eccezione** — il finally completa
         pulitamente e ``execute()`` ritorna il result dal
         ``_handle_failure`` (non un uncaught RuntimeError).

    Contract critico: ``execute()`` è chiamato da un task Celery che
    si aspetta sempre un return value. Una RuntimeError non catturata
    marcherebbe il task come failed-with-exception invece di
    failed-with-result → retry automatico di Celery → tick duplicato
    sullo stesso schedule.

    Regressione: rimuovere l'outer ``try/except`` dell'inner retry
    romperebbe il contract "sempre ritorna un result", anche in DB
    disaster scenario.
    """
    manager = _make_manager_for_cleanup_test(schedule_config)
    # Entrambi i release sollevano
    manager.concurrency.release = MagicMock(
        side_effect=[RuntimeError("blip 1"), RuntimeError("blip 2")]
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _seconds: None,
    )

    with patch(
        "app.db.db_service.get_schedule",
        return_value={"id": "s-disaster", "group_id": "grp-A"},
    ), patch(
        "app.services.project_service.ProjectService"
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

    # release chiamata esattamente 2 volte (primo + retry)
    assert manager.concurrency.release.call_count == 2

    # execute() ritorna un result dict (no uncaught exception)
    assert isinstance(result, dict), (
        f"execute() DEVE ritornare dict anche se entrambi release "
        f"falliscono, ottenuto {type(result).__name__}"
    )
    # Il result viene dal _handle_failure mockato, status "failed"
    assert result["status"] == "failed"
