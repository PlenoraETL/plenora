"""
[phase-2-err][batch7] 7.b — retry wrapper classified su release lock.

Audit Batch 7 §3.3 [ALTA] pseudo-retry cieco su `concurrency.release` in
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# ─── Helpers (pattern identico a test_execution_manager_orphan_and_release_contract.py) ─


def _make_manager() -> ExecutionManager:
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


def _make_manager_for_retry_test(schedule_config) -> ExecutionManager:
    """Manager configurato per arrivare al finally dopo errore deterministico
    downstream. `_get_orchestrator` solleva → il body di try fallisce → va
    al finally dove vogliamo osservare il retry wrapper."""
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.set_runtime_status = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")
    manager._start_lock_heartbeat = MagicMock(
        return_value={"stop_event": None, "thread": None, "lock_lost": False}
    )
    manager._stop_lock_heartbeat = MagicMock()
    manager._get_orchestrator = MagicMock(
        side_effect=RuntimeError("forced downstream failure for retry test")
    )
    manager._handle_failure = MagicMock(
        return_value={"success": False, "status": "failed", "message": "forced"}
    )
    return manager


def _run_execute_with_patches(manager, schedule_id, monkeypatch):
    """Wrapper comune per invocare manager.execute con patch standard
    (sleep no-op, project metadata, open_run/close_run mocks)."""
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _seconds: None,
    )
    with patch(
        "app.db.config.schedules.get_schedule",
        return_value={"id": schedule_id, "group_id": "grp-A"},
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
        return manager.execute(schedule_id)


# TEST 1 — Budget N=3 esaurito su transient PG continui


def test_release_retry_budget_n3_on_all_transient(schedule_config, monkeypatch):
    """Tutti i 3 tentativi sollevano OperationalError → release chiamato
    N=3 volte, log [CRITICO] emesso con exc_info preservato (last_transient_err)."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=[
            psycopg2.OperationalError("pool exhausted 1"),
            psycopg2.OperationalError("pool exhausted 2"),
            psycopg2.OperationalError("pool exhausted 3"),
        ]
    )

    with patch(
        "app.core.scheduler.execution_manager.logger"
    ) as mock_logger:
        result = _run_execute_with_patches(manager, "s-budget", monkeypatch)

    # N=3 tentativi tutti transient
    assert manager.concurrency.release.call_count == 3
    # Log [CRITICO] dopo esaurimento budget
    critico_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRITICO]" in str(c)
        and "non rilasciato dopo 3 tentativi transient" in str(c)
    ]
    assert len(critico_errors) == 1
    # exc_info preservato (last_transient_err)
    assert critico_errors[0].kwargs.get("exc_info") is not None
    # Contratto Celery: execute() ritorna dict
    assert isinstance(result, dict)


# TEST 2 — Exit early sul primo success (no retry inutili)


def test_release_retry_exits_early_on_success(schedule_config, monkeypatch):
    """Side effect `[OperationalError, None, ...]` → 2 chiamate, il loop
    esce al primo success. Nessun warning [CRITICO] finale."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=[psycopg2.OperationalError("blip"), None]
    )

    with patch(
        "app.core.scheduler.execution_manager.logger"
    ) as mock_logger:
        _run_execute_with_patches(manager, "s-early", monkeypatch)

    # 2 chiamate: 1 fail + 1 success (N=3 max ma si esce prima)
    assert manager.concurrency.release.call_count == 2
    # Nessun log [CRITICO] (release e' riuscita)
    critico_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRITICO]" in str(c)
    ]
    assert critico_errors == []


# TEST 3 — Non-transient errors NOT retried + log non-transient CRITICO


def test_release_retry_non_transient_does_not_retry(schedule_config, monkeypatch):
    """RuntimeError (non-transient, non-PG) → solo 1 chiamata, log [CRITICO]
    non-transient emesso, NO propagazione (contratto Celery)."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=RuntimeError("bug logico non-transient")
    )

    with patch(
        "app.core.scheduler.execution_manager.logger"
    ) as mock_logger:
        result = _run_execute_with_patches(manager, "s-nontransient", monkeypatch)

    # Solo 1 chiamata: non-transient non entra nel retry loop
    assert manager.concurrency.release.call_count == 1
    # Log [CRITICO] non-transient
    critico_non_transient = [
        c for c in mock_logger.error.call_args_list
        if "[CRITICO]" in str(c) and "non-transient" in str(c)
    ]
    assert len(critico_non_transient) == 1
    assert critico_non_transient[0].kwargs.get("exc_info") is True
    # Contratto Celery: execute() ritorna dict anche con errore non-transient
    assert isinstance(result, dict)


# TEST 4 — Backoff esponenziale con jitter ±20%


def test_release_retry_backoff_exponential_with_jitter(schedule_config, monkeypatch):
    """Tra i 3 tentativi (2 intervalli), time.sleep e' chiamato con valori
    che rispettano backoff esponenziale (0.5s, 1.0s) con jitter ±20%."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=[
            psycopg2.OperationalError("1"),
            psycopg2.OperationalError("2"),
            psycopg2.OperationalError("3"),
        ]
    )
    # Cattura le durate sleep invece di bloccare
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    with patch("app.db.config.schedules.get_schedule",
               return_value={"id": "s-backoff", "group_id": "grp-A"}), \
        patch("app.services.project_service.ProjectService") as psc, \
        patch("app.core.scheduler.execution_manager.open_run"), \
        patch("app.core.scheduler.execution_manager.set_run_context"), \
        patch("app.core.scheduler.execution_manager.close_run"):
        psc.return_value.get_project_metadata.return_value = _project_meta("grp-A")
        manager.execute("s-backoff")

    # 3 tentativi = 2 intervalli sleep (tra tentativo 1-2 e 2-3; dopo il 3°
    # si esce dal budget senza sleep)
    assert len(sleep_calls) == 2
    # 1° interval: base=0.5 * 2^0 = 0.5, jitter ±20% → [0.4, 0.6]
    assert 0.4 <= sleep_calls[0] <= 0.6, (
        f"1° backoff deve essere 0.5s ±20%, ottenuto {sleep_calls[0]}"
    )
    # 2° interval: base=0.5 * 2^1 = 1.0, jitter ±20% → [0.8, 1.2]
    assert 0.8 <= sleep_calls[1] <= 1.2, (
        f"2° backoff deve essere 1.0s ±20%, ottenuto {sleep_calls[1]}"
    )


# TEST 5 — Budget T=10s interrompe il retry anche se N non raggiunto


def test_release_retry_budget_total_10s_interrupts(schedule_config, monkeypatch):
    """time.monotonic simulato tale che `elapsed >= 10s` dopo il 1° retry →
    il loop interrompe prima di raggiungere N=3. 2 release call (1° attempt
    + 1 retry) prima del budget esaurito."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=[
            psycopg2.OperationalError("1"),
            psycopg2.OperationalError("2"),
            psycopg2.OperationalError("3"),
        ]
    )
    # Sequenza time.monotonic: primo call = retry_start = 0.0; successivi
    # simulano elapsed crescente. Elapsed > 10s al 2° check per far break.
    monotonic_seq = iter([0.0, 1.0, 15.0, 15.0, 15.0, 15.0])
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.monotonic",
        lambda: next(monotonic_seq),
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.time.sleep",
        lambda _: None,
    )

    with patch("app.db.config.schedules.get_schedule",
               return_value={"id": "s-timebudget", "group_id": "grp-A"}), \
        patch("app.services.project_service.ProjectService") as psc, \
        patch("app.core.scheduler.execution_manager.open_run"), \
        patch("app.core.scheduler.execution_manager.set_run_context"), \
        patch("app.core.scheduler.execution_manager.close_run"):
        psc.return_value.get_project_metadata.return_value = _project_meta("grp-A")
        manager.execute("s-timebudget")

    # retry_start + 1° check: elapsed=1s OK, prosegue.
    # 2° check: elapsed=15s > 10s → break budget totale.
    # Tentativi effettuati: 2 (1° + 2°). 3° non viene mai tentato.
    assert manager.concurrency.release.call_count == 2


# TEST 6 — Warning [GESTITO] per ogni tentativo transient che fallisce


def test_release_retry_log_warning_per_attempt(schedule_config, monkeypatch):
    """Ogni retry transient (non finale) emette `logger.warning('[GESTITO]
    release lock ... transient (tentativo N/M): ...')`. Su 3 failure → 2
    warning (i primi 2 retry; il 3° e' terminal e logga [CRITICO])."""
    manager = _make_manager_for_retry_test(schedule_config)
    manager.concurrency.release = MagicMock(
        side_effect=[
            psycopg2.OperationalError("fail-1"),
            psycopg2.OperationalError("fail-2"),
            psycopg2.OperationalError("fail-3"),
        ]
    )

    with patch(
        "app.core.scheduler.execution_manager.logger"
    ) as mock_logger:
        _run_execute_with_patches(manager, "s-attempts", monkeypatch)

    # NOTE: emessi sui tentativi 1 e 2 (prima del sleep). Il 3° non
    # emette warning perche' il budget e' esaurito e si va al log CRITICO.
    transient_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "[GESTITO]" in str(c)
        and "release lock s-attempts transient" in str(c)
    ]
    assert len(transient_warnings) == 2, (
        f"attesi 2 warning transient (tentativi 1-2), ricevuto "
        f"{len(transient_warnings)}: {mock_logger.warning.call_args_list}"
    )
    # Verifica pattern (tentativo X/3) in almeno uno dei warning
    assert any("1/3" in str(c) for c in transient_warnings)
    assert any("2/3" in str(c) for c in transient_warnings)
