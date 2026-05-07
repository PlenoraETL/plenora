"""
Heartbeat thread dentro ExecutionManager.execute (C3 fix 4) - R-05 deterministic.

Contratto:
"""

from unittest.mock import MagicMock

import pytest

from app.core.scheduler.execution_manager import ExecutionManager
from app.shared.clock import FakeClock


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# _start_lock_heartbeat / _stop_lock_heartbeat (helpers isolati)


def _build_manager_with_mock_concurrency():
    """Pattern leggero (vedi test_lock_heartbeat_contract.py): bypassa
    `ExecutionManager.__init__` (che istanzia ScheduleService /
    LogService / PolicyEngine / ArtifactStore: ~4s di setup) e setta
    solo `concurrency`, l'unico attributo letto da
    `_start_lock_heartbeat`/`_stop_lock_heartbeat`."""
    manager = ExecutionManager.__new__(ExecutionManager)
    fake = MagicMock()
    fake.refresh_lock = MagicMock(return_value=True)
    manager.concurrency = fake
    return manager, fake


def _tick(fc: FakeClock, interval: float) -> None:
    """Forza un tick deterministico del loop heartbeat."""
    fc.wait_for_pending_waiters(1)
    fc.advance(interval + 0.001)


def test_start_lock_heartbeat_returns_state_and_starts_thread():
    """_start_lock_heartbeat crea un thread attivo con name 'heartbeat-*'."""
    manager, _fake_conc = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-xyz", interval=10.0, clock=FakeClock())
    try:
        assert state is not None
        thread = state["thread"]
        assert thread.is_alive()
        assert "heartbeat-sched-xyz" in thread.name
    finally:
        manager._stop_lock_heartbeat(state)


def test_heartbeat_thread_is_daemon():
    """Daemon=True: muore col processo. Se SIGKILL del worker il
    thread non sopravvive a rinfrescare un lock post-mortem."""
    manager, _ = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=FakeClock())
    try:
        assert state["thread"].daemon is True
    finally:
        manager._stop_lock_heartbeat(state)


def test_heartbeat_calls_refresh_lock_repeatedly():
    """Tre tick virtuali -> tre invocazioni di refresh_lock."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        _tick(fc, 10.0)
        _tick(fc, 10.0)
        # Aspetta che il thread sia di nuovo parked dopo l'ultimo tick
        fc.wait_for_pending_waiters(1)
    finally:
        manager._stop_lock_heartbeat(state)

    assert fake_conc.refresh_lock.call_count >= 3
    fake_conc.refresh_lock.assert_any_call("sched-1")


def test_stop_lock_heartbeat_stops_thread_deterministically():
    """Dopo _stop_lock_heartbeat, il thread non e' piu' vivo."""
    manager, _ = _build_manager_with_mock_concurrency()

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    fc.wait_for_pending_waiters(1)
    manager._stop_lock_heartbeat(state)

    assert state["thread"].is_alive() is False


def test_stop_lock_heartbeat_is_idempotent():
    """Chiamare _stop due volte non deve sollevare."""
    manager, _ = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=FakeClock())
    manager._stop_lock_heartbeat(state)
    manager._stop_lock_heartbeat(state)


def test_heartbeat_does_not_call_refresh_after_stop():
    """Dopo stop, il contatore di refresh non incrementa piu'."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    _tick(fc, 10.0)
    fc.wait_for_pending_waiters(1)
    manager._stop_lock_heartbeat(state)

    count_after_stop = fake_conc.refresh_lock.call_count
    # Avanza ancora il tempo virtuale: il thread e' fermato, niente piu'
    # refresh anche su un advance() arbitrariamente grande.
    fc.advance(100.0)
    assert fake_conc.refresh_lock.call_count == count_after_stop


def test_heartbeat_swallows_refresh_errors():
    """Se refresh_lock solleva, il thread non muore (prossimo tick
    riprovera'). Protezione contro transient DB blip."""
    manager, fake_conc = _build_manager_with_mock_concurrency()
    fake_conc.refresh_lock.side_effect = RuntimeError("db blip")

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        _tick(fc, 10.0)
        # Thread ancora vivo dopo 2 exception, parked sul prossimo wait
        fc.wait_for_pending_waiters(1)
        assert state["thread"].is_alive()
    finally:
        manager._stop_lock_heartbeat(state)
