"""
Heartbeat thread dentro ExecutionManager.execute (C3 fix 4) - R-05 deterministic.

Contratto:
"""

from unittest.mock import MagicMock
import ast
from pathlib import Path

import pytest

from app.core.scheduler.execution_manager import ExecutionManager
from app.shared.clock import FakeClock


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]

BACKEND_ROOT = Path(__file__).resolve().parents[3]
EXECUTION_MANAGER = BACKEND_ROOT / "app" / "core" / "scheduler" / "execution_manager.py"
RUNTIME = BACKEND_ROOT / "app" / "core" / "scheduler" / "scheduler_execution_runtime.py"


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


def test_heartbeat_calls_refresh_lock_repeatedly(tick):
    """Tre tick virtuali -> tre invocazioni di refresh_lock."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        tick(fc, 10.0)
        tick(fc, 10.0)
        tick(fc, 10.0)
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


def test_heartbeat_does_not_call_refresh_after_stop(tick):
    """Dopo stop, il contatore di refresh non incrementa piu'."""
    manager, fake_conc = _build_manager_with_mock_concurrency()

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    tick(fc, 10.0)
    fc.wait_for_pending_waiters(1)
    manager._stop_lock_heartbeat(state)

    count_after_stop = fake_conc.refresh_lock.call_count
    # Avanza ancora il tempo virtuale: il thread e' fermato, niente piu'
    # refresh anche su un advance() arbitrariamente grande.
    fc.advance(100.0)
    assert fake_conc.refresh_lock.call_count == count_after_stop


def test_heartbeat_swallows_refresh_errors(tick):
    """Se refresh_lock solleva, il thread non muore (prossimo tick
    riprovera'). Protezione contro transient DB blip."""
    manager, fake_conc = _build_manager_with_mock_concurrency()
    fake_conc.refresh_lock.side_effect = RuntimeError("db blip")

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        tick(fc, 10.0)
        tick(fc, 10.0)
        # Thread ancora vivo dopo 2 exception, parked sul prossimo wait
        fc.wait_for_pending_waiters(1)
        assert state["thread"].is_alive()
    finally:
        manager._stop_lock_heartbeat(state)


def test_heartbeat_wrapper_logic_stays_outside_execution_manager():
    """ExecutionManager keeps only compat delegation for heartbeat helpers."""
    execution_src = EXECUTION_MANAGER.read_text(encoding="utf-8")
    runtime_src = RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(execution_src)

    method_names = {"_start_lock_heartbeat", "_stop_lock_heartbeat"}
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in method_names
    }

    assert method_names == set(methods)
    assert "LockHeartbeat" not in execution_src
    assert "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS" not in execution_src
    assert "thread.join" not in execution_src
    assert "stop_event.set" not in execution_src
    assert "LockHeartbeat" in runtime_src
    assert "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS" in runtime_src
    assert "thread.join" in runtime_src
    assert "stop_event.set" in runtime_src

    start_calls = [
        node
        for node in ast.walk(methods["_start_lock_heartbeat"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_lock_heartbeat"
    ]
    stop_calls = [
        node
        for node in ast.walk(methods["_stop_lock_heartbeat"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stop_lock_heartbeat"
    ]
    assert len(start_calls) == 1
    assert len(stop_calls) == 1
