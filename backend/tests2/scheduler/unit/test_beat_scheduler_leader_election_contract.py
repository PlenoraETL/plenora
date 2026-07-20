"""
Leader election del beat scheduler via lock PostgreSQL (Plenora 2.0, Fase 3).

Prima usava Redis SETNX; ora un upsert atomico su beat_leader_locks
(acquire-or-refresh con TTL). Il contratto e' invariato — un solo leader,
refresh del TTL, standby per i non-leader, degraded fail-closed/open — qui
verificato iniettando un fake store PG con scadenza real-time (lane no-DB).
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _neutralize_db_bound_maintenance():
    """Il tick da leader esegue gli step di manutenzione; questo file blinda
    la leader election, non la manutenzione: la si azzera in blocco."""
    maintenance = (
        "_sync_from_db",
        "_emit_catchup_runs",
        "_write_heartbeat",
        "_check_stuck_tasks",
        "_cleanup_eseguibili_outputs",
        "_archive_old_logs",
        "_cleanup_old_logs",
        "_cleanup_old_schedule_logs",
        "_archive_old_audit_actions",
        "_cleanup_orphan_temp_files",
        "_reconcile_storage",
        "_cleanup_task_results",
        "_drain_task_dispatch_outbox",
        "_recover_runtime_leases",
    )
    with patch.multiple(
        PlenoraBeatScheduler,
        **{name: MagicMock() for name in maintenance},
    ):
        yield


class _FakePgLeaderStore:
    """Emula beat_leader_store su PostgreSQL con scadenza real-time.

    Condiviso tra piu' _FakeScheduler = stessa riga beat_leader_locks
    (due processi beat che contendono lo stesso lock)."""

    def __init__(self):
        self._owner = None
        self._expires_at = None  # epoch

    def acquire_or_refresh_beat_leader(self, *, owner_token, ttl_seconds):
        now = time.time()
        expired = self._expires_at is None or now > self._expires_at
        if self._owner is None or expired or self._owner == owner_token:
            self._owner = owner_token
            self._expires_at = now + ttl_seconds
            return True
        return False

    def release_beat_leader(self, *, owner_token):
        if self._owner == owner_token:
            self._owner = None
            self._expires_at = None
            return True
        return False

    def current_beat_leader(self):
        if self._owner and self._expires_at and time.time() <= self._expires_at:
            return self._owner
        return None


class _RaisingLeaderStore:
    """Store che simula PostgreSQL irraggiungibile (degraded mode)."""

    def acquire_or_refresh_beat_leader(self, *, owner_token, ttl_seconds):
        raise RuntimeError("PostgreSQL unavailable")

    def release_beat_leader(self, *, owner_token):
        raise RuntimeError("PostgreSQL unavailable")


class _FakeScheduler(PlenoraBeatScheduler):
    """PlenoraBeatScheduler senza bootstrap Celery, con store iniettato."""

    def __init__(self, leader_id="pid-1@host-a", leader_store=None):
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.now = MagicMock(return_value=MagicMock(name="now"))
        self._last_stuck_check = 0.0
        self._last_eseguibili_cleanup = 0.0
        self._last_logs_retention = 0.0
        self._leader_id = leader_id
        self._is_leader = False
        self._leader_store = leader_store or _FakePgLeaderStore()


def _run_tick(scheduler):
    # Plenora 2.0 Fase 5: il beat e' celery-free; tick() non chiama piu' un
    # super().tick() di celery.beat.Scheduler. Si invoca direttamente.
    scheduler.tick()


# ACQUISIZIONE LEADERSHIP


def test_first_tick_acquires_leader_lock_when_free():
    store = _FakePgLeaderStore()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)

    with patch.object(scheduler, "_sync_from_db") as sync:
        _run_tick(scheduler)

    sync.assert_called_once()
    assert scheduler._is_leader is True
    assert store.current_beat_leader() == "pid-1@host-a"


def test_second_beat_sees_existing_leader_and_goes_standby():
    store = _FakePgLeaderStore()
    # Primo beat ha gia' il lock.
    store.acquire_or_refresh_beat_leader(owner_token="pid-99@host-x", ttl_seconds=120)

    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)

    with patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat") as heartbeat:
        scheduler.tick()

    sync.assert_not_called()
    heartbeat.assert_not_called()
    assert scheduler._is_leader is False


def test_leader_refreshes_ttl_on_subsequent_ticks():
    store = _FakePgLeaderStore()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)

    with patch.object(scheduler, "_sync_from_db"):
        _run_tick(scheduler)
    original_expire = store._expires_at

    time.sleep(0.01)

    with patch.object(scheduler, "_sync_from_db"):
        _run_tick(scheduler)
    assert store._expires_at > original_expire
    assert scheduler._is_leader is True


# LEADERSHIP LOST


def test_leader_loses_lock_to_another_process_and_logs_warning(caplog):
    import logging

    store = _FakePgLeaderStore()
    # Un altro processo detiene il lock (non scaduto).
    store.acquire_or_refresh_beat_leader(owner_token="pid-2@host-b", ttl_seconds=120)

    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)
    scheduler._is_leader = True  # credeva di esserlo

    with patch.object(scheduler, "_sync_from_db") as sync, \
         caplog.at_level(logging.WARNING, logger="workers.beat_scheduler"):
        scheduler.tick()

    sync.assert_not_called()
    assert scheduler._is_leader is False
    assert any("LEADER" in rec.getMessage() for rec in caplog.records)


# DEGRADED MODE: PostgreSQL non raggiungibile


def test_pg_unavailable_fail_open_stays_leader(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True,
        raising=False,
    )
    scheduler = _FakeScheduler(
        leader_id="pid-1@host-a", leader_store=_RaisingLeaderStore()
    )

    with patch.object(scheduler, "_sync_from_db") as sync:
        _run_tick(scheduler)

    sync.assert_called_once()
    assert scheduler._is_leader is True


def test_pg_unavailable_fail_closed_goes_standby(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False,
        raising=False,
    )
    scheduler = _FakeScheduler(
        leader_id="pid-1@host-a", leader_store=_RaisingLeaderStore()
    )

    with patch.object(scheduler, "_sync_from_db") as sync:
        scheduler.tick()

    sync.assert_not_called()
    assert scheduler._is_leader is False


# SHUTDOWN: release del lock


def test_close_releases_leader_lock_when_still_owned():
    store = _FakePgLeaderStore()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)
    store.acquire_or_refresh_beat_leader(owner_token="pid-1@host-a", ttl_seconds=120)
    scheduler._is_leader = True

    scheduler.close()

    assert store.current_beat_leader() is None


def test_close_does_not_release_lock_owned_by_different_process():
    store = _FakePgLeaderStore()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", leader_store=store)
    # Un altro processo ha il lock.
    store.acquire_or_refresh_beat_leader(owner_token="pid-2@host-b", ttl_seconds=120)
    scheduler._is_leader = True  # stale flag

    scheduler.close()

    assert store.current_beat_leader() == "pid-2@host-b"
