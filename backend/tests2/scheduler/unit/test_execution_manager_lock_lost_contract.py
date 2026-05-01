"""Heartbeat lock lost detection (C3 follow-up fix A) - R-05 deterministic.

Contratto
---------
Se `concurrency.refresh_lock(schedule_id)` ritorna False (lock
scaduto o rilasciato da un altro processo), il thread heartbeat:

1. Tenta re-acquire atomico con il suo original value "running".
2. Se re-acquire riesce -> WARNING "temporaneamente perso, riacquisito".
   Il lock era scaduto per un blip ma nessun altro ha acquisito.
3. Se re-acquire fallisce -> ERROR "lock perso, esecuzione concorrente
   possibile" + setta flag `lock_lost` sullo state dell'heartbeat.
   Il thread continua il loop (prossimo tick potrebbe riprendere il
   lock se il detentore rilascia).

Il flag e' leggibile via `state["lock_lost"]` ed e' usato da
`execute()` per tracciare nel result che il run ha perso protezione.

R-05: ogni tick e' guidato da FakeClock.advance(); zero time.sleep
reali.
"""

from unittest.mock import MagicMock

import pytest

from app.core.scheduler.execution_manager import ExecutionManager
from app.shared.clock import FakeClock


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _build_manager(refresh_return_values=None, reacquire_succeeds=True):
    """Costruisce un ExecutionManager col mock di concurrency configurato.

    Pattern leggero: `__new__` evita il full `__init__` (~4s di setup
    ScheduleService/LogService/PolicyEngine/ArtifactStore non usati
    da questi test).
    """
    manager = ExecutionManager.__new__(ExecutionManager)
    fake = MagicMock()

    # refresh_lock: sequenza di return value (lista) o valore singolo
    if refresh_return_values is None:
        fake.refresh_lock = MagicMock(return_value=True)
    else:
        fake.refresh_lock = MagicMock(side_effect=refresh_return_values)

    fake.try_acquire = MagicMock(return_value="acquired" if reacquire_succeeds else "queued")
    manager.concurrency = fake
    return manager, fake


def _tick(fc: FakeClock, interval: float) -> None:
    fc.wait_for_pending_waiters(1)
    fc.advance(interval + 0.001)


# =============================================================================
# refresh OK -> nessun problema
# =============================================================================


def test_heartbeat_does_not_try_reacquire_when_refresh_returns_true():
    """Caso normale: refresh ritorna True -> il thread continua senza
    provare re-acquire."""
    manager, fake = _build_manager(refresh_return_values=[True, True, True, True, True])

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        _tick(fc, 10.0)
        fc.wait_for_pending_waiters(1)
    finally:
        manager._stop_lock_heartbeat(state)

    fake.try_acquire.assert_not_called()
    assert state.get("lock_lost") is not True


# =============================================================================
# refresh False -> tenta re-acquire
# =============================================================================


def test_heartbeat_attempts_reacquire_when_refresh_returns_false(caplog):
    """Se refresh_lock ritorna False, il thread deve tentare re-acquire."""
    import logging

    manager, fake = _build_manager(
        refresh_return_values=[False, True, True, True, True, True, True],
        reacquire_succeeds=True,
    )

    fc = FakeClock()
    with caplog.at_level(logging.WARNING, logger="app.core.scheduler.execution_manager"):
        state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
        try:
            _tick(fc, 10.0)
            fc.wait_for_pending_waiters(1)
        finally:
            manager._stop_lock_heartbeat(state)

    fake.try_acquire.assert_called()
    args, _kwargs = fake.try_acquire.call_args
    assert args[0] == "sched-1"


def test_heartbeat_successful_reacquire_does_not_set_lock_lost_flag():
    """refresh=False + re-acquire=OK -> lock temporaneamente perso ma
    riacquisito, NON e' un caso di lock_lost definitivo."""
    manager, fake = _build_manager(
        refresh_return_values=[False, True, True, True],
        reacquire_succeeds=True,
    )

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        _tick(fc, 10.0)
        fc.wait_for_pending_waiters(1)
    finally:
        manager._stop_lock_heartbeat(state)

    assert state.get("lock_lost") is not True


def test_heartbeat_failed_reacquire_sets_lock_lost_flag():
    """refresh=False + re-acquire=FAIL -> qualcun altro ha preso il lock.
    Il thread setta lock_lost=True nello state, cosi' execute() puo'
    riportarlo nel risultato."""
    manager, fake = _build_manager(
        refresh_return_values=[False, False, False, False, False],
        reacquire_succeeds=False,
    )

    fc = FakeClock()
    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=fc)
    try:
        _tick(fc, 10.0)
        fc.wait_for_pending_waiters(1)
    finally:
        manager._stop_lock_heartbeat(state)

    assert state.get("lock_lost") is True
    assert fake.try_acquire.called


def test_heartbeat_state_exposes_lock_lost_as_false_by_default():
    """Lo state ritornato da _start_lock_heartbeat deve esporre una
    chiave `lock_lost` leggibile (False inizialmente)."""
    manager, _ = _build_manager()

    state = manager._start_lock_heartbeat("sched-1", interval=10.0, clock=FakeClock())
    try:
        assert "lock_lost" in state
        assert state["lock_lost"] is False
    finally:
        manager._stop_lock_heartbeat(state)
