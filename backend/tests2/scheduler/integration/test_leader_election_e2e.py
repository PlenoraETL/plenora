"""
Integration test dei 5 scenari operativi della leader election (Fase 3).

Migrata da Redis (fakeredis) a lock PostgreSQL: un fake store in-memory con
scadenza real-time (TTL) emula beat_leader_locks condiviso tra piu' beat.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from celery.beat import Scheduler

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.integration, pytest.mark.scheduler, pytest.mark.contract]


class _SharedPgLeaderStore:
    """Emula beat_leader_locks (una riga) condivisa tra processi beat.

    `fail=True` simula PostgreSQL irraggiungibile (degraded mode)."""

    def __init__(self):
        self._owner = None
        self._expires_at = None  # epoch
        self.fail = False

    def acquire_or_refresh_beat_leader(self, *, owner_token, ttl_seconds):
        if self.fail:
            raise RuntimeError("PostgreSQL unavailable")
        now = time.time()
        expired = self._expires_at is None or now > self._expires_at
        if self._owner is None or expired or self._owner == owner_token:
            self._owner = owner_token
            self._expires_at = now + ttl_seconds
            return True
        return False

    def release_beat_leader(self, *, owner_token):
        if self.fail:
            raise RuntimeError("PostgreSQL unavailable")
        if self._owner == owner_token:
            self._owner = None
            self._expires_at = None
            return True
        return False

    def current_beat_leader(self):
        if self._owner and self._expires_at and time.time() <= self._expires_at:
            return self._owner
        return None


def _make_scheduler(leader_id, leader_store) -> PlenoraBeatScheduler:
    """Crea un PlenoraBeatScheduler senza Celery bootstrap + store iniettato."""
    scheduler = object.__new__(PlenoraBeatScheduler)  # skip __init__
    scheduler._db_loaded = False
    scheduler.data = {}
    scheduler.app = MagicMock()
    scheduler.app.now = MagicMock(return_value=MagicMock(name="now"))
    scheduler._leader_id = leader_id
    scheduler._is_leader = False
    scheduler._leader_store = leader_store
    return scheduler


def test_scenario_1_two_beats_only_one_acquires_leadership():
    store = _SharedPgLeaderStore()
    beat_a = _make_scheduler("pid-1@host-a", store)
    beat_b = _make_scheduler("pid-2@host-b", store)

    result_a = beat_a._try_become_leader()
    result_b = beat_b._try_become_leader()

    assert (result_a, result_b) == (True, False)
    assert (beat_a._is_leader, beat_b._is_leader) == (True, False)
    assert store.current_beat_leader() == "pid-1@host-a"


def test_scenario_2_standby_promoted_after_leader_ttl_expiration(monkeypatch):
    monkeypatch.setattr("workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1)
    store = _SharedPgLeaderStore()
    beat_a = _make_scheduler("pid-killed@host-a", store)
    beat_b = _make_scheduler("pid-survivor@host-b", store)

    assert beat_a._try_become_leader() is True
    assert beat_b._try_become_leader() is False

    # SIGKILL di A: nessun refresh; il TTL scade.
    time.sleep(1.2)

    assert beat_b._try_become_leader() is True
    assert beat_b._is_leader is True
    assert store.current_beat_leader() == "pid-survivor@host-b"


def test_scenario_3_pg_down_fail_closed_then_recovers(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False,
        raising=False,
    )
    store = _SharedPgLeaderStore()
    beat = _make_scheduler("pid-1@host-a", store)

    assert beat._try_become_leader() is True

    # PostgreSQL down → fail-closed → standby.
    store.fail = True
    assert beat._try_become_leader() is False
    assert beat._is_leader is False

    # Recovery: PG torna su; beat e' ancora owner del lock (TTL non scaduto)
    # → refresh → torna leader.
    store.fail = False
    assert beat._try_become_leader() is True
    assert beat._is_leader is True


def test_scenario_3b_pg_down_fail_open_stays_leader(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True,
        raising=False,
    )
    store = _SharedPgLeaderStore()
    store.fail = True
    beat = _make_scheduler("pid-1@host-a", store)

    assert beat._try_become_leader() is True
    assert beat._is_leader is True


def test_scenario_4_close_releases_lock_so_next_beat_acquires_immediately():
    store = _SharedPgLeaderStore()
    beat_a = _make_scheduler("pid-a@host-a", store)
    beat_b = _make_scheduler("pid-b@host-b", store)

    assert beat_a._try_become_leader() is True
    assert beat_b._try_become_leader() is False

    with patch.object(Scheduler, "close"):
        beat_a.close()

    assert store.current_beat_leader() is None
    assert beat_b._try_become_leader() is True
    assert beat_b._is_leader is True


def test_scenario_4b_close_does_not_release_lock_taken_by_another_after_ttl(monkeypatch):
    monkeypatch.setattr("workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1)
    store = _SharedPgLeaderStore()
    beat_a = _make_scheduler("pid-a@host-a", store)
    beat_b = _make_scheduler("pid-b@host-b", store)

    beat_a._try_become_leader()  # A leader, TTL 1s
    time.sleep(1.2)
    beat_b._try_become_leader()
    assert store.current_beat_leader() == "pid-b@host-b"

    # A chiama close() tardivamente: NON deve cancellare il lock di B.
    with patch.object(Scheduler, "close"):
        beat_a.close()

    assert store.current_beat_leader() == "pid-b@host-b"


def test_scenario_5_refresh_extends_ttl_preventing_expiration(monkeypatch):
    monkeypatch.setattr("workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1)
    store = _SharedPgLeaderStore()
    beat = _make_scheduler("pid-1@host-a", store)

    beat._try_become_leader()
    for _ in range(5):
        time.sleep(0.3)
        assert beat._try_become_leader() is True, "leadership persa durante tick regolare"

    assert store.current_beat_leader() == "pid-1@host-a"


def test_scenario_5b_missed_tick_longer_than_ttl_loses_leadership(monkeypatch):
    monkeypatch.setattr("workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1)
    store = _SharedPgLeaderStore()
    beat_a = _make_scheduler("pid-a@host-a", store)
    beat_b = _make_scheduler("pid-b@host-b", store)

    beat_a._try_become_leader()
    time.sleep(1.2)  # "GC pause" di A: nessun refresh per > TTL

    assert beat_b._try_become_leader() is True
    assert beat_a._try_become_leader() is False
    assert beat_a._is_leader is False
