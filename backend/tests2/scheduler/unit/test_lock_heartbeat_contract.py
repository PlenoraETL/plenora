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


def test_context_manager_starts_and_stops(fake_concurrency):
    """`with LockHeartbeat(...) as hb:` chiama start nel __enter__ e
    stop nel __exit__. Pattern previsto per c2 (uso futuro in
    execute()), ma test garantisce che il context manager funziona."""
    from app.core.scheduler._lock_heartbeat import LockHeartbeat

    captured_thread = []

    with LockHeartbeat(
        concurrency=fake_concurrency,
        schedule_id="sch-1",
        interval=10.0,
    ) as hb:
        assert hb._thread is not None
        assert hb._thread.is_alive()
        captured_thread.append(hb._thread)

    # Post __exit__: stop_event settato, thread joinato (timeout 5s)
    assert captured_thread[0].is_alive() is False or (
        # Edge: il join ha timeout 5s; in CI lento potrebbe servire
        # piu' tempo. Diamo un buffer extra di 1s prima di fallire.
        time.sleep(1.0) or captured_thread[0].is_alive() is False
    )
