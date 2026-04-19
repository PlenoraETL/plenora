"""Heartbeat lock lost detection (C3 follow-up fix A).

Contratto
---------
Se `concurrency.refresh_lock(schedule_id)` ritorna False (lock
scaduto o rilasciato da un altro processo), il thread heartbeat:

1. Tenta re-acquire atomico con il suo original value "running".
2. Se re-acquire riesce → WARNING "temporaneamente perso, riacquisito".
   Il lock era scaduto per un blip ma nessun altro ha acquisito.
3. Se re-acquire fallisce → ERROR "lock perso, esecuzione concorrente
   possibile" + setta flag `lock_lost` sullo state dell'heartbeat.
   Il thread continua il loop (prossimo tick potrebbe riprendere il
   lock se il detentore rilascia).

Il flag e' leggibile via `state["lock_lost"]` ed e' usato da
`execute()` per tracciare nel result che il run ha perso protezione.
"""

import threading
import time
from unittest.mock import MagicMock

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _build_manager(refresh_return_values=None, reacquire_succeeds=True):
    """Costruisce un ExecutionManager col mock di concurrency configurato."""
    manager = ExecutionManager()
    fake = MagicMock()

    # refresh_lock: sequenza di return value (lista) o valore singolo
    if refresh_return_values is None:
        fake.refresh_lock = MagicMock(return_value=True)
    else:
        fake.refresh_lock = MagicMock(side_effect=refresh_return_values)

    fake.try_acquire = MagicMock(return_value="acquired" if reacquire_succeeds else "queued")
    manager.concurrency = fake
    return manager, fake


# =============================================================================
# refresh OK → nessun problema
# =============================================================================


def test_heartbeat_does_not_try_reacquire_when_refresh_returns_true():
    """Caso normale: refresh ritorna True → il thread continua senza
    provare re-acquire."""
    manager, fake = _build_manager(refresh_return_values=[True, True, True])

    state = manager._start_lock_heartbeat("sched-1", interval=0.02)
    try:
        time.sleep(0.1)
    finally:
        manager._stop_lock_heartbeat(state)

    fake.try_acquire.assert_not_called()
    assert state.get("lock_lost") is not True


# =============================================================================
# refresh False → tenta re-acquire
# =============================================================================


def test_heartbeat_attempts_reacquire_when_refresh_returns_false(caplog):
    """Se refresh_lock ritorna False, il thread deve tentare re-acquire."""
    import logging

    manager, fake = _build_manager(
        refresh_return_values=[False, True, True, True, True, True, True],
        reacquire_succeeds=True,
    )

    with caplog.at_level(logging.WARNING, logger="app.core.scheduler.execution_manager"):
        state = manager._start_lock_heartbeat("sched-1", interval=0.02)
        try:
            time.sleep(0.1)
        finally:
            manager._stop_lock_heartbeat(state)

    fake.try_acquire.assert_called()
    args, _kwargs = fake.try_acquire.call_args
    assert args[0] == "sched-1"


def test_heartbeat_successful_reacquire_does_not_set_lock_lost_flag():
    """refresh=False + re-acquire=OK → lock temporaneamente perso ma
    riacquisito, NON e' un caso di lock_lost definitivo."""
    manager, fake = _build_manager(
        refresh_return_values=[False, True, True, True],
        reacquire_succeeds=True,
    )

    state = manager._start_lock_heartbeat("sched-1", interval=0.02)
    try:
        time.sleep(0.1)
    finally:
        manager._stop_lock_heartbeat(state)

    assert state.get("lock_lost") is not True


def test_heartbeat_failed_reacquire_sets_lock_lost_flag():
    """refresh=False + re-acquire=FAIL → qualcun altro ha preso il lock.
    Il thread setta lock_lost=True nello state, cosi' execute() puo'
    riportarlo nel risultato.

    Il log ERROR e' verificato a mano nel source: usiamo il logger
    custom di app.utils che non propaga a standard logging / caplog.
    Qui testiamo solo il flag, che e' l'interfaccia contrattuale."""
    manager, fake = _build_manager(
        refresh_return_values=[False, False, False, False, False],
        reacquire_succeeds=False,
    )

    state = manager._start_lock_heartbeat("sched-1", interval=0.02)
    try:
        time.sleep(0.1)
    finally:
        manager._stop_lock_heartbeat(state)

    assert state.get("lock_lost") is True
    # Re-acquire e' stato effettivamente tentato
    assert fake.try_acquire.called


def test_heartbeat_state_exposes_lock_lost_as_false_by_default():
    """Lo state ritornato da _start_lock_heartbeat deve esporre una
    chiave `lock_lost` leggibile (False inizialmente)."""
    manager, _ = _build_manager()

    state = manager._start_lock_heartbeat("sched-1", interval=0.1)
    try:
        assert "lock_lost" in state
        assert state["lock_lost"] is False
    finally:
        manager._stop_lock_heartbeat(state)
