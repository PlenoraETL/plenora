"""
Caratterizzazione di ConcurrencyManager.

Scopo: cristallizzare il comportamento ATTUALE del lock+queue manager
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from unittest.mock import patch

import pytest

from app.core.scheduler.concurrency_manager import ConcurrencyManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _InMemoryLockStore:
    """Simula la tabella scheduler_locks in-memory."""

    def __init__(self):
        # key -> (value_str, expires_at or None)
        self._data: Dict[str, Tuple[str, Optional[datetime]]] = {}

    def set_nx(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> bool:
        self._sweep_expired()
        if key in self._data:
            return False
        expires_at = None
        if ttl_seconds:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._data[key] = (value, expires_at)
        return True

    def get(self, key: str) -> Optional[str]:
        self._sweep_expired()
        entry = self._data.get(key)
        return entry[0] if entry else None

    def incr(self, key: str) -> int:
        self._sweep_expired()
        if key not in self._data:
            self._data[key] = ("0", None)
        value, exp = self._data[key]
        new_value = int(value) + 1
        self._data[key] = (str(new_value), exp)
        return new_value

    def decr(self, key: str) -> int:
        self._sweep_expired()
        if key not in self._data:
            return 0
        value, exp = self._data[key]
        new_value = max(0, int(value) - 1)
        if new_value == 0:
            del self._data[key]
            return 0
        self._data[key] = (str(new_value), exp)
        return new_value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def _sweep_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [k for k, (_, exp) in self._data.items() if exp and now > exp]
        for k in expired:
            del self._data[k]


@pytest.fixture
def manager(monkeypatch):
    """ConcurrencyManager con i 5 helper privati sostituiti da un
    in-memory store, così try_acquire/release/ecc. operano in isolamento."""
    monkeypatch.setattr(
        "app.core.scheduler.concurrency_manager.db.execute",
        lambda *a, **kw: None,
    )
    mgr = ConcurrencyManager()
    store = _InMemoryLockStore()
    monkeypatch.setattr(mgr, "_set_nx", store.set_nx)
    monkeypatch.setattr(mgr, "_get", store.get)
    monkeypatch.setattr(mgr, "_incr", store.incr)
    monkeypatch.setattr(mgr, "_decr", store.decr)
    monkeypatch.setattr(mgr, "_delete", store.delete)
    mgr._test_store = store
    return mgr


# Test struttura base: available / bootstrap failure


def test_available_stays_false_when_table_bootstrap_fails():
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute",
        side_effect=RuntimeError("db down"),
    ):
        manager = ConcurrencyManager()

    assert manager.available is False


def test_available_is_true_on_normal_bootstrap(manager):
    assert manager.available is True


# try_acquire: acquired / queued / skipped


def test_try_acquire_on_free_schedule_returns_acquired(manager):
    assert manager.try_acquire("sched-1") == "acquired"
    assert manager.is_running("sched-1") is True


def test_try_acquire_on_busy_schedule_queues(manager):
    manager.try_acquire("sched-1")
    result = manager.try_acquire("sched-1")

    assert result == "queued"
    assert manager.get_status("sched-1")["queue_size"] == 1


def test_try_acquire_respects_max_queue_size(manager):
    manager.try_acquire("sched-1")  # acquired
    manager.try_acquire("sched-1")  # queued (1/3)
    manager.try_acquire("sched-1")  # queued (2/3)
    manager.try_acquire("sched-1")  # queued (3/3)

    # Il quarto deve essere skipped — supera max_queue_size=3
    assert manager.try_acquire("sched-1") == "skipped"
    # E la coda resta a 3 (incr+decr si cancellano per lo skipped)
    assert manager.get_status("sched-1")["queue_size"] == 3


def test_try_acquire_max_queue_size_parameter_overrides_default(manager):
    manager.try_acquire("sched-1", max_queue_size=1)  # acquired
    manager.try_acquire("sched-1", max_queue_size=1)  # queued (1/1)

    # Il terzo con max_queue_size=1 e' skipped
    assert manager.try_acquire("sched-1", max_queue_size=1) == "skipped"


def test_try_acquire_isolates_different_schedules(manager):
    # Lock su uno schedule non blocca un altro schedule
    assert manager.try_acquire("sched-a") == "acquired"
    assert manager.try_acquire("sched-b") == "acquired"
    assert manager.is_running("sched-a") is True
    assert manager.is_running("sched-b") is True


# release: rilascio + decremento coda


def test_release_removes_lock(manager):
    manager.try_acquire("sched-1")
    assert manager.is_running("sched-1") is True

    manager.release("sched-1")

    assert manager.is_running("sched-1") is False


def test_release_does_not_consume_another_runs_queue_reservation(manager):
    manager.try_acquire("sched-1")  # acquired
    manager.try_acquire("sched-1")  # queued (1)
    manager.try_acquire("sched-1")  # queued (2)
    assert manager.get_status("sched-1")["queue_size"] == 2

    manager.release("sched-1")

    # Il lock attivo non possiede gli slot delle run accodate: sara' ogni
    # retry ad eliminare la propria prenotazione quando acquisisce.
    assert manager.is_running("sched-1") is False
    assert manager.get_status("sched-1")["queue_size"] == 2


def test_release_idempotent_when_lock_already_free(manager):
    # Rilasciare un lock gia' libero non deve sollevare
    manager.release("sched-1")
    manager.release("sched-1")

    assert manager.is_running("sched-1") is False


# get_status / is_running


def test_get_status_reports_locked_and_queue_size(manager):
    status = manager.get_status("sched-1")
    assert status == {"locked": False, "queue_size": 0}

    manager.try_acquire("sched-1")
    status = manager.get_status("sched-1")
    assert status["locked"] is True
    assert status["queue_size"] == 0

    manager.try_acquire("sched-1")  # queued
    status = manager.get_status("sched-1")
    assert status["queue_size"] == 1


def test_is_running_reflects_lock_state(manager):
    assert manager.is_running("sched-1") is False

    manager.try_acquire("sched-1")
    assert manager.is_running("sched-1") is True

    manager.release("sched-1")
    assert manager.is_running("sched-1") is False


# force_release / cleanup


def test_force_release_clears_both_lock_and_queue(manager):
    manager.try_acquire("sched-1")
    manager.try_acquire("sched-1")  # queued (1)
    manager.try_acquire("sched-1")  # queued (2)

    manager.force_release("sched-1")

    status = manager.get_status("sched-1")
    assert status["locked"] is False
    assert status["queue_size"] == 0


# refresh_lock (C3 fix 4) — heartbeat per lock orfano su SIGKILL


def test_refresh_lock_returns_true_when_lock_held(manager, monkeypatch):
    """refresh_lock su un lock vivo ritorna True (refresh avvenuto)."""
    manager.try_acquire("sched-1")

    # Patch db.execute per simulare UPDATE successo
    mock_cursor = type("_Cur", (), {"rowcount": 1})()
    monkeypatch.setattr(
        "app.core.scheduler.concurrency_manager.db.execute",
        lambda *a, **kw: mock_cursor,
    )

    assert manager.refresh_lock("sched-1") is True


def test_refresh_lock_returns_false_when_lock_absent(manager):
    """refresh_lock su un lock mai acquisito ritorna False."""
    assert manager.refresh_lock("never-locked") is False


def test_refresh_lock_returns_false_when_lock_released_meanwhile(manager):
    """Race safety: se il lock viene rilasciato tra check e UPDATE,
    refresh non deve dichiarare vittoria."""
    manager.try_acquire("sched-1")
    manager.release("sched-1")

    assert manager.refresh_lock("sched-1") is False
