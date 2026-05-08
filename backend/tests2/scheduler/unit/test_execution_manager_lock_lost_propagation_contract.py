"""
Propagazione del flag lock_lost al result dict di execute (C3 round 3) - R-05.

Contract: se durante l'esecuzione il thread heartbeat rileva che il
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.shared.clock import FakeClock


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# Pattern B2.0 (PR #34): handler stdlib agganciato al logger reale.
# `StructuredLogger` ha propagate=False quindi caplog non funziona.
class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _build_em_with_fake_concurrency():
    """Helper: istanzia un ExecutionManager con concurrency mockata.

    Niente schedule_service / log_service / policy: chiamiamo solo
    _start_lock_heartbeat / _stop_lock_heartbeat, non execute().
    """
    from app.core.scheduler.execution_manager import ExecutionManager

    cm = MagicMock()
    cm.refresh_lock = MagicMock(return_value=False)
    cm.try_acquire = MagicMock(return_value="failed")

    em = ExecutionManager.__new__(ExecutionManager)
    em.concurrency = cm
    return em, cm


def _tick(fc: FakeClock, interval: float) -> None:
    fc.wait_for_pending_waiters(1)
    fc.advance(interval + 0.001)


def test_start_lock_heartbeat_returns_dict_with_lock_lost_key():
    """Il dict ritornato espone `lock_lost` come chiave: la proprieta'
    pubblica che `execute()` consuma in `heartbeat_state.get("lock_lost", False)`.
    """
    em, _ = _build_em_with_fake_concurrency()
    state = em._start_lock_heartbeat("sch-1", interval=10.0, clock=FakeClock())
    try:
        assert "lock_lost" in state, (
            "Il dict ritornato da _start_lock_heartbeat DEVE contenere "
            "'lock_lost'. execute() lo legge via state.get('lock_lost')."
        )
        assert state["lock_lost"] is False
    finally:
        em._stop_lock_heartbeat(state)


def test_start_lock_heartbeat_state_lock_lost_is_live_reference():
    """`state['lock_lost']` riflette LIVE le mutazioni del thread loop:
    quando il thread rileva refresh False + reacquire failed, il dict
    visibile dal chiamante mostra lock_lost=True (non e' un snapshot
    al momento del return).

    Senza questa proprieta', il refactor PR-#36 con LockHeartbeat
    non potrebbe mantenere la signature pubblica invariata e
    l'invariante "execute() include lock_lost nel result"."""
    em, _ = _build_em_with_fake_concurrency()
    fc = FakeClock()
    state = em._start_lock_heartbeat("sch-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        # Aspetta la re-park per garantire che l'iterazione (incluso
        # il set di lock_lost) sia completa.
        fc.wait_for_pending_waiters(1)
        assert state.get("lock_lost") is True, (
            "state['lock_lost'] deve riflettere live la mutazione del "
            "thread loop. Senza reference live, execute() non vedrebbe "
            "il flag mutato dopo l'avvio dell'heartbeat."
        )
    finally:
        em._stop_lock_heartbeat(state)


def test_lock_heartbeat_emits_error_log_when_lock_definitively_lost():
    """Il thread loop emette log ERROR quando lock perso e
    re-acquire fallito. Senza questo log, il flag `lock_lost` nel
    result resta senza audit trail server-side.

    Cattura tramite `_ListHandler` agganciato al logger reale di
    `app.core.scheduler.scheduler_lock`."""
    target_logger = logging.getLogger("app.core.scheduler.scheduler_lock")
    handler = _ListHandler()
    target_logger.addHandler(handler)

    em, _ = _build_em_with_fake_concurrency()
    fc = FakeClock()
    state = em._start_lock_heartbeat("sch-test", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        fc.wait_for_pending_waiters(1)
    finally:
        em._stop_lock_heartbeat(state)
        target_logger.removeHandler(handler)

    error_records = [r for r in handler.records if r.levelno == logging.ERROR]
    assert error_records, (
        "Nessun log ERROR emesso quando il lock e' perso e re-acquire "
        "fallisce. La detection del thread heartbeat resta silente."
    )
    assert any("sch-test" in r.getMessage() for r in error_records), (
        "Il log ERROR deve includere lo schedule_id per audit trail"
    )
