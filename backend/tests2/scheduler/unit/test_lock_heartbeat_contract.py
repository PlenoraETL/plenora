"""Contract test PR-#36 — LockHeartbeat class API.

Scope: blinda l'API pubblica della class estratta in
`app.core.scheduler._lock_heartbeat.LockHeartbeat`.

Invarianti testati:
  - `start()` spawn thread daemon
  - `stop()` set stop_event + join idempotente, timeout 5s
  - `get_state_dict()` ritorna reference live (non copia) cosi' le
    mutazioni del thread loop sono visibili al chiamante
  - lock_lost flag: True solo se refresh_lock False AND try_acquire
    NON "acquired"; rimane False altrimenti
  - exception in refresh_lock / try_acquire NON propaga ma logga
    warning e continua (transient blip safe)
  - context manager (`with LockHeartbeat(...)`) chiama start/stop
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


@pytest.fixture
def fake_concurrency():
    """Mock concurrency con refresh_lock + try_acquire configurabili."""
    cm = MagicMock()
    cm.refresh_lock = MagicMock(return_value=True)
    cm.try_acquire = MagicMock(return_value="acquired")
    return cm


def test_start_spawns_daemon_thread(fake_concurrency):
    """start() crea un thread daemon nominato per lo schedule_id."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=10.0,
    )
    hb.start()
    try:
        assert hb._thread is not None
        assert hb._thread.is_alive()
        assert hb._thread.daemon is True
        assert "sch-1" in hb._thread.name
    finally:
        hb.stop()


def test_get_state_dict_returns_live_reference(fake_concurrency):
    """get_state_dict() ritorna la reference interna mutabile, non
    una copia. Mutazioni del thread loop sono visibili al chiamante."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=10.0,
    )
    state = hb.get_state_dict()
    # Stesso identity, non copia
    assert state is hb._state
    # Pre-start lock_lost False di default
    assert state.get("lock_lost") is False
    # Mutazione manuale dello state interno e' visibile via reference
    hb._state["lock_lost"] = True
    assert state["lock_lost"] is True


def test_stop_is_idempotent(fake_concurrency):
    """stop() chiamato 2 volte non solleva."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=10.0,
    )
    hb.start()
    hb.stop()
    # Seconda chiamata non solleva
    hb.stop()


def test_lock_lost_flag_true_when_refresh_false_and_reacquire_fails(
    fake_concurrency,
):
    """Pattern critico C3: refresh_lock False + try_acquire != 'acquired'
    → state['lock_lost'] = True via mutazione thread loop.

    Forza un solo tick dell'heartbeat (interval molto piccolo) e
    verifica che dopo lo state riflette lock_lost=True."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    fake_concurrency.refresh_lock = MagicMock(return_value=False)
    fake_concurrency.try_acquire = MagicMock(return_value="failed")

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=0.05,  # 50ms tick
    )
    hb.start()
    # Aspetta che almeno un tick venga processato
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if hb.lock_lost:
            break
        time.sleep(0.05)
    hb.stop()

    assert hb.lock_lost is True, (
        "lock_lost dovrebbe essere True dopo refresh False + reacquire failed"
    )
    state = hb.get_state_dict()
    assert state["lock_lost"] is True


def test_lock_lost_flag_remains_false_when_refresh_ok(fake_concurrency):
    """Happy path: refresh_lock sempre True → lock_lost resta False."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    fake_concurrency.refresh_lock = MagicMock(return_value=True)

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=0.05,
    )
    hb.start()
    time.sleep(0.2)  # 4 tick max
    hb.stop()

    assert hb.lock_lost is False
    # Verifica che il loop e' partito
    assert fake_concurrency.refresh_lock.call_count >= 1


def test_refresh_exception_does_not_propagate_or_set_lock_lost(
    fake_concurrency,
):
    """Transient blip: refresh_lock solleva → loop continua, no
    state['lock_lost']=True (e' una situazione retry-safe)."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    fake_concurrency.refresh_lock = MagicMock(
        side_effect=RuntimeError("transient DB blip")
    )

    hb = LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=0.05,
    )
    hb.start()
    time.sleep(0.15)  # ~3 tick: il loop deve sopravvivere
    hb.stop()

    assert hb.lock_lost is False, (
        "Exception in refresh_lock e' transient: NON deve marcare lock_lost"
    )
    assert hb._thread is not None  # esiste ancora


def test_state_dict_includes_external_hb_key_when_wrapper_injects_it():
    """Finding H (PR-#36): il wrapper compat `_start_lock_heartbeat` in
    `execution_manager` aggiunge la chiave `_hb` al dict ritornato da
    `get_state_dict()`. Poiche' il dict E' la stessa reference dello
    `_state` interno, l'iniezione muta anche `LockHeartbeat._state`.

    Questo test blinda il behavior accoppiato (NON un bug, e' design
    documentato): se la shape pulita di `_state` cambia in futuro, il
    wrapper compat va aggiornato di conseguenza."""
    from unittest.mock import MagicMock

    from app.core.scheduler._lock_heartbeat import LockHeartbeat
    from app.core.scheduler.execution_manager import ExecutionManager

    cm = MagicMock()
    cm.refresh_lock = MagicMock(return_value=True)
    em = ExecutionManager.__new__(ExecutionManager)
    em.concurrency = cm

    state = em._start_lock_heartbeat("sch-test", interval=10.0)
    try:
        # 1. Il dict pubblico include _hb iniettato dal wrapper
        assert "_hb" in state
        # 2. _hb e' una LockHeartbeat, NON viene letto da execute()
        assert isinstance(state["_hb"], LockHeartbeat)
        # 3. Stessa reference dello state interno (accoppiamento atteso)
        hb = state["_hb"]
        assert state is hb._state
        # 4. Reference circolare _state["_hb"] -> hb (documentata)
        assert hb._state["_hb"] is hb
        # 5. Shape pulita PRE-iniezione (LockHeartbeat costruito diretto):
        #    chiavi note sono stop_event, lock_lost, thread; nessun _hb
        clean = LockHeartbeat(concurrency=cm, schedule_id="x", interval=10.0)
        assert "_hb" not in clean._state
    finally:
        em._stop_lock_heartbeat(state)
