"""
Contract test parametrizzato per l'invariante "run lifecycle order" del
ExecutionManager.

Cluster bug coperti (plenora-bug-orchestration):

  1. P0: `execution_manager.py:233` chiamava `open_run` PRIMA di
     `try_acquire`. I path early-return "skipped" e "queued" non
     chiamavano `close_run`, lasciando record permanenti in stato
     'running' nel log_runs (orphan run lifecycle leak).
     Post-fix: `open_run` viene chiamato SOLO dopo `try_acquire == "acquired"`.

  2. P1: `set_runtime_status("running")` veniva chiamato senza rollback
     in finally. Worker killato a meta' esecuzione lasciava il runtime
     status permanentemente "running". Post-fix: tracker
     `runtime_status_finalized` + rollback a "idle" se nessun path
     canonico (update_runtime / handle_failure) ha finalizzato.

  3. P2 collateral: il return per "queued" ritornava `success=True`,
     facendo si' che Celery Beat considerasse il task completato.
     Post-fix: `success=False`.

Test paradigma Go-friendly: dipendenze iniettate via monkeypatch, no
fixture pesanti. Assertion sull'ORDINE delle invocazioni (spy puro).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.scheduler,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _make_executor_with_spies(monkeypatch, schedule_config):
    """Costruisce un ExecutionManager con tutti i collaboratori mockati.

    Ritorna `(executor, spies)` dove `spies` e' un dict di MagicMock per
    le invocazioni che vogliamo asserire.
    """
    from app.core.scheduler.execution_manager import ExecutionManager
    import app.core.scheduler.execution_manager as em_module

    executor = ExecutionManager()

    # Servizi a livello istanza
    executor.schedule_service = MagicMock()
    executor.log_service = MagicMock()
    executor.concurrency = MagicMock()
    executor.policy = MagicMock()

    # `execute()` carica il config via `schedule_service.get_schedule(id)`.
    executor.schedule_service.get_schedule.return_value = schedule_config

    # Pre-check tenant: scheduler_db.get_schedule_row + project_meta.
    schedule_row = MagicMock()
    schedule_row.group_id = "grp-1"
    monkeypatch.setattr(
        "app.core.scheduler.db.get_schedule_row",
        lambda *_a, **_kw: schedule_row,
    )

    project_meta = MagicMock()
    project_meta.group_id = "grp-1"
    project_service_mock = MagicMock()
    project_service_mock.get_project_metadata.return_value = project_meta
    monkeypatch.setattr(
        "app.core.projects.service.ProjectService",
        lambda *_a, **_kw: project_service_mock,
    )

    # Spy sui boundary del lifecycle.
    spy_open_run = MagicMock()
    spy_close_run = MagicMock()
    spy_set_run_context = MagicMock()
    monkeypatch.setattr(em_module, "open_run", spy_open_run)
    monkeypatch.setattr(em_module, "close_run", spy_close_run)
    monkeypatch.setattr(em_module, "set_run_context", spy_set_run_context)

    # `create_intermediate_run` importato dentro la funzione: stub al
    # modulo. Non bloccante: best-effort try/except.
    monkeypatch.setattr(
        "app.core.intermediate.run_lifecycle.create_run",
        lambda *_a, **_kw: None,
        raising=False,
    )

    # Heartbeat: no-op per non lanciare thread.
    monkeypatch.setattr(
        em_module, "start_lock_heartbeat",
        lambda *_a, **_kw: {"lock_lost": False},
    )
    monkeypatch.setattr(
        em_module, "stop_lock_heartbeat", lambda *_a, **_kw: None,
    )

    # release_lock_with_retry: no-op
    monkeypatch.setattr(
        "app.core.scheduler.scheduler_release.release_lock_with_retry",
        lambda *_a, **_kw: None,
    )

    # close_worker_connection / scheduler_db.close_connection: no-op
    monkeypatch.setattr(
        "app.core.intermediate.cleanup.close_worker_connection",
        lambda: None, raising=False,
    )
    monkeypatch.setattr(
        "app.core.scheduler.db.close_connection",
        lambda: None, raising=False,
    )

    spies = {
        "open_run": spy_open_run,
        "close_run": spy_close_run,
        "set_runtime_status": executor.schedule_service.set_runtime_status,
        "update_runtime": executor.schedule_service.update_runtime,
        "try_acquire": executor.concurrency.try_acquire,
    }
    return executor, spies


def _invoke_execute(executor, schedule_config) -> Any:
    """Helper: invoca executor.execute con argomenti standard."""
    return executor.execute(
        schedule_id=schedule_config.id,
        triggered_by="cron",
        retry_attempt=0,
        scheduled_for="2026-05-21T10:00:00+00:00",
    )


class TestOpenRunHappensAfterAcquire:
    """L'invariante critica: `open_run` non deve essere chiamata se il
    lock non e' stato acquisito. Pre-fix era il bug P0 di orphan run.
    """

    def test_skipped_path_does_not_open_run(self, monkeypatch, schedule_config):
        executor, spies = _make_executor_with_spies(monkeypatch, schedule_config)
        spies["try_acquire"].return_value = "skipped"

        result = _invoke_execute(executor, schedule_config)

        assert result["status"] == "skipped"
        assert result["success"] is False
        spies["open_run"].assert_not_called()
        spies["close_run"].assert_not_called()
        spies["set_runtime_status"].assert_not_called()

    def test_queued_path_does_not_open_run(self, monkeypatch, schedule_config):
        executor, spies = _make_executor_with_spies(monkeypatch, schedule_config)
        spies["try_acquire"].return_value = "queued"

        result = _invoke_execute(executor, schedule_config)

        assert result["status"] == "queued"
        # Bug P2 collaterale: queued non deve segnalare success=True
        # (altrimenti Celery Beat sopprime gli alert di monitoring).
        assert result["success"] is False
        spies["open_run"].assert_not_called()
        spies["close_run"].assert_not_called()
        spies["set_runtime_status"].assert_not_called()


class TestRuntimeStatusRollback:
    """Quando il worker e' interrotto a meta' esecuzione (KeyboardInterrupt,
    SIGKILL, ecc.) senza completare update_runtime ne' handle_failure,
    il finally deve rollbackare il runtime status a 'idle' invece di
    lasciarlo 'running' per sempre.
    """

    def test_keyboard_interrupt_rollbacks_runtime_to_idle(
        self, monkeypatch, schedule_config,
    ):
        executor, spies = _make_executor_with_spies(monkeypatch, schedule_config)
        spies["try_acquire"].return_value = "acquired"

        # Orchestrator solleva KeyboardInterrupt: l'except Exception non
        # lo cattura, propaga oltre il try block ma il finally esegue.
        def _kbd_interrupt(*_a, **_kw):
            raise KeyboardInterrupt("worker interrupted")

        fake_orchestrator = MagicMock()
        fake_orchestrator.execute_pipeline_from_contract.side_effect = _kbd_interrupt
        monkeypatch.setattr(
            executor, "_get_orchestrator", lambda: fake_orchestrator,
        )
        monkeypatch.setattr(
            "app.core.orchestrator.execution_contract.build_pipeline_execution_contract",
            lambda **_kw: {"project_type": "estrai", "canvas": MagicMock()},
        )

        # Disattiva pre-check connection per non richiedere DB.
        schedule_config.pre_check.verify_destination_connection = False

        with pytest.raises(KeyboardInterrupt):
            _invoke_execute(executor, schedule_config)

        # Verifica che set_runtime_status sia stato chiamato per "running"
        # e poi per "idle" nel finally (rollback).
        statuses = [
            call.args[1] for call in spies["set_runtime_status"].call_args_list
        ]
        assert "running" in statuses, "Pre-execute setta running"
        assert "idle" in statuses, (
            "Rollback nel finally deve riportare runtime status a idle "
            "quando nessun path canonico l'ha finalizzato"
        )
        # update_runtime NON deve essere stato chiamato (path canonico
        # non raggiunto).
        spies["update_runtime"].assert_not_called()
