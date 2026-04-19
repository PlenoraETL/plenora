"""Heartbeat thread dentro ExecutionManager.execute (C3 fix 4).

Contratto:
1. Helper `_start_lock_heartbeat(schedule_id)` lancia un thread daemon
   che chiama `concurrency.refresh_lock(schedule_id)` periodicamente.
   Ritorna uno stato opaco da passare a _stop_lock_heartbeat.
2. Helper `_stop_lock_heartbeat(state)` segnala lo stop e fa join con
   timeout. Idempotente.
3. Thread e' daemon=True: muore col processo Python (se SIGKILL del
   worker Celery, il thread non sopravvive — il lock scade per TTL).
4. Il thread NON rinfresca piu' dopo lo stop_event.
5. execute() integra: dopo try_acquire success chiama _start, nel
   finally chiama _stop.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# =============================================================================
# _start_lock_heartbeat / _stop_lock_heartbeat (helpers isolati)
# =============================================================================


def _build_manager_with_mock_concurrency():
    manager = ExecutionManager()
    fake = MagicMock()
    fake.refresh_lock = MagicMock(return_value=True)
    manager.concurrency = fake
    return manager, fake


def test_start_lock_heartbeat_returns_state_and_starts_thread():
    """_start_lock_heartbeat crea un thread attivo con name 'heartbeat-*'."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-xyz", interval=0.01)
    try:
        assert state is not None
        # Il thread deve esistere ed essere vivo
        thread = state["thread"]
        assert thread.is_alive()
        assert "heartbeat-sched-xyz" in thread.name
    finally:
        manager._stop_lock_heartbeat(state)


def test_heartbeat_thread_is_daemon():
    """Daemon=True: muore col processo. Se SIGKILL del worker il
    thread non sopravvive a rinfrescare un lock post-mortem."""
    manager, _ = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=0.01)
    try:
        assert state["thread"].daemon is True
    finally:
        manager._stop_lock_heartbeat(state)


def test_heartbeat_calls_refresh_lock_repeatedly():
    """Con interval corto, in una finestra di ~150ms il thread chiama
    refresh_lock almeno 2-3 volte."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=0.03)
    try:
        time.sleep(0.15)
    finally:
        manager._stop_lock_heartbeat(state)

    # Almeno 2 refresh (tolleranza su timing: 150ms/30ms = 5, margine)
    assert fake_conc.refresh_lock.call_count >= 2
    fake_conc.refresh_lock.assert_any_call("sched-1")


def test_stop_lock_heartbeat_stops_thread_deterministically():
    """Dopo _stop_lock_heartbeat, il thread non e' piu' vivo."""
    manager, _ = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=0.01)
    manager._stop_lock_heartbeat(state)

    # Piccolo grace per il join
    time.sleep(0.05)

    assert state["thread"].is_alive() is False


def test_stop_lock_heartbeat_is_idempotent():
    """Chiamare _stop due volte non deve sollevare."""
    manager, _ = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=0.01)
    manager._stop_lock_heartbeat(state)
    # Secondo stop: no-op
    manager._stop_lock_heartbeat(state)


def test_heartbeat_does_not_call_refresh_after_stop():
    """Dopo stop, il contatore di refresh non incrementa piu'."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    state = manager._start_lock_heartbeat("sched-1", interval=0.01)
    time.sleep(0.05)
    manager._stop_lock_heartbeat(state)

    count_after_stop = fake_conc.refresh_lock.call_count
    time.sleep(0.1)

    assert fake_conc.refresh_lock.call_count == count_after_stop


def test_heartbeat_swallows_refresh_errors():
    """Se refresh_lock solleva, il thread non muore (prossimo tick
    riproverà). Protezione contro transient DB blip."""
    manager, fake_conc = _build_manager_with_mock_concurrency()
    fake_conc.refresh_lock.side_effect = RuntimeError("db blip")

    state = manager._start_lock_heartbeat("sched-1", interval=0.02)
    try:
        time.sleep(0.1)
        # Il thread deve essere ancora vivo nonostante refresh abbia sollevato
        assert state["thread"].is_alive()
    finally:
        manager._stop_lock_heartbeat(state)
