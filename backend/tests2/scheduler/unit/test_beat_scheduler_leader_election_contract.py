"""
Leader election del beat scheduler via Redis SETNX (C3 punto 1).

Contratto atteso
"""

import time
from typing import Dict, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from celery.beat import Scheduler

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeRedis:
    """Emula il subset di redis-py usato dalla leader election."""

    def __init__(self):
        # key -> (value_bytes, expires_at_epoch_or_None)
        self._data: Dict[str, Tuple[bytes, Optional[float]]] = {}

    def set(self, key: str, value, nx: bool = False, ex: Optional[int] = None):
        self._sweep()
        if nx and key in self._data:
            return None
        value_bytes = value.encode("utf-8") if isinstance(value, str) else value
        expires_at = time.time() + ex if ex else None
        self._data[key] = (value_bytes, expires_at)
        return True

    def get(self, key: str):
        self._sweep()
        entry = self._data.get(key)
        return entry[0] if entry else None

    def expire(self, key: str, ex: int) -> bool:
        if key not in self._data:
            return False
        value, _ = self._data[key]
        self._data[key] = (value, time.time() + ex)
        return True

    def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0

    def _sweep(self):
        now = time.time()
        expired = [k for k, (_, exp) in self._data.items() if exp and now > exp]
        for k in expired:
            del self._data[k]


class _FakeScheduler(PlenoraBeatScheduler):
    """PlenoraBeatScheduler senza bootstrap Celery, con Redis fake iniettato."""

    def __init__(self, leader_id: str = "pid-1@host-a", redis_client=None):
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.now = MagicMock(return_value=MagicMock(name="now"))
        self._last_stuck_check = 0.0
        self._last_eseguibili_cleanup = 0.0
        self._last_logs_retention = 0.0
        self._last_imdf_bundles_cleanup = 0.0
        # Attributi per leader election (aggiunti dal fix)
        self._leader_id = leader_id
        self._is_leader = False
        self._redis_client = redis_client


# ACQUISIZIONE LEADERSHIP


def test_first_tick_acquires_leader_lock_when_key_absent():
    """Primo beat che parte: il lock non esiste, il SETNX riesce,
    il beat diventa leader e procede con sync+cleanup."""
    redis = _FakeRedis()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)

    with patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick", return_value=1.0):
        scheduler.tick()

    sync.assert_called_once()
    assert scheduler._is_leader is True
    assert redis.get("scheduler:beat:leader") == b"pid-1@host-a"


def test_second_beat_sees_existing_leader_and_goes_standby():
    """Un secondo beat avviato quando il lock gia' esiste NON deve
    fare sync, cleanup, ne' super().tick(). Deve restare idle."""
    redis = _FakeRedis()
    # Primo beat ha gia' il lock
    redis.set("scheduler:beat:leader", "pid-99@host-x", nx=True, ex=120)

    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)

    with patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat") as heartbeat, \
         patch.object(scheduler, "_check_stuck_tasks") as stuck, \
         patch.object(Scheduler, "tick") as super_tick:
        scheduler.tick()

    sync.assert_not_called()
    heartbeat.assert_not_called()
    stuck.assert_not_called()
    super_tick.assert_not_called()
    assert scheduler._is_leader is False


def test_leader_refreshes_ttl_on_subsequent_ticks():
    """Un beat che e' gia' leader, al tick successivo deve refresh-are
    il TTL (expire) per evitare che scada mentre sta lavorando."""
    redis = _FakeRedis()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)

    # Primo tick: acquisisce
    with patch.object(scheduler, "_sync_from_db"), \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick", return_value=1.0):
        scheduler.tick()

    original_expire = redis._data["scheduler:beat:leader"][1]

    # Forza avanzamento del tempo nel fake (simulazione passaggio secondi)
    time.sleep(0.01)

    # Secondo tick: refresh
    with patch.object(scheduler, "_sync_from_db"), \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick", return_value=1.0):
        scheduler.tick()

    new_expire = redis._data["scheduler:beat:leader"][1]
    assert new_expire > original_expire


# LEADERSHIP LOST / RIPRESA


def test_leader_loses_lock_to_another_process_and_logs_warning(caplog):
    """Il beat che era leader scopre che qualcun altro ha preso il lock
    (scenario: il suo TTL e' scaduto mentre era bloccato, un altro
    processo ha acquisito). Deve loggare WARNING e passare in standby."""
    import logging

    redis = _FakeRedis()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)
    scheduler._is_leader = True
    # Un altro processo ha il lock
    redis.set("scheduler:beat:leader", "pid-2@host-b", ex=120)

    with patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick"), \
         caplog.at_level(logging.WARNING, logger="workers.beat_scheduler"):
        scheduler.tick()

    sync.assert_not_called()
    assert scheduler._is_leader is False
    assert any("LEADER" in rec.getMessage() for rec in caplog.records)


# DEGRADED MODE: Redis non raggiungibile


def test_redis_unavailable_falls_back_to_single_instance_leader(monkeypatch):
    """Con SCHEDULE_BEAT_LEADER_FAIL_OPEN=True (flag esplicito), Redis
    unavailable → beat diventa leader single-instance (behavior legacy).

    Nota: il DEFAULT e' fail-closed. Questo test documenta il
    comportamento opt-in per deployment mono-host."""
    # `SCHEDULE_BEAT_LEADER_FAIL_OPEN` vive dentro DEFAULTS dict, non come
    # property di Settings: il consumer (workers/beat_scheduler.py) fa
    # `getattr(settings, key, False)`. Settiamo l'attributo con
    # `raising=False` cosi' monkeypatch lo aggiunge in setup e lo rimuove
    # al teardown senza esigere che esista a priori.
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True,
        raising=False,
    )
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=None)

    with patch.object(scheduler, "_get_redis", return_value=None), \
         patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_emit_catchup_runs"), \
         patch.object(scheduler, "_write_heartbeat"), \
         patch.object(scheduler, "_check_stuck_tasks"), \
         patch.object(scheduler, "_cleanup_eseguibili_outputs"), \
         patch.object(scheduler, "_cleanup_imdf_bundles"), \
         patch.object(scheduler, "_cleanup_old_logs"), \
         patch.object(Scheduler, "tick", return_value=1.0):
        scheduler.tick()

    sync.assert_called_once()
    assert scheduler._is_leader is True


# SHUTDOWN: release del lock


def test_close_releases_leader_lock_when_still_owned():
    """Su shutdown pulito (close), il leader rilascia il lock cosi' che
    un altro beat possa prenderlo subito invece di aspettare il TTL."""
    redis = _FakeRedis()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)
    redis.set("scheduler:beat:leader", "pid-1@host-a", ex=120)
    scheduler._is_leader = True

    with patch.object(Scheduler, "close"):
        scheduler.close()

    assert redis.get("scheduler:beat:leader") is None


def test_close_does_not_release_lock_owned_by_different_process():
    """Se il lock e' posseduto da un altro processo (es. TTL scaduto,
    qualcun altro l'ha ripreso), close() del non-owner NON deve cancellarlo.
    Protegge contro race durante failover."""
    redis = _FakeRedis()
    scheduler = _FakeScheduler(leader_id="pid-1@host-a", redis_client=redis)
    # Un altro processo ha il lock
    redis.set("scheduler:beat:leader", "pid-2@host-b", ex=120)
    scheduler._is_leader = True  # stale flag — credeva di esserlo

    with patch.object(Scheduler, "close"):
        scheduler.close()

    # Lock dell'altro processo deve rimanere
    assert redis.get("scheduler:beat:leader") == b"pid-2@host-b"
