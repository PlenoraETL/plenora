"""Leader election fail-closed di default quando Redis e' unavailable (C3 follow-up).

Contratto
---------
Il default cambia da fail-open (io-sono-leader su Redis down) a
fail-closed (standby su Redis down), per evitare che due beat durante
un flap Redis diventino entrambi leader e riaprano il doppio enqueue.

Flag `SCHEDULE_BEAT_LEADER_FAIL_OPEN` (default False) permette il
vecchio behavior per deployment single-instance dove nessun altro
beat gira mai (single host, systemd unit singolo).

Il flag viene letto a ogni chiamata di _try_become_leader (non
cache-ato a init) cosi' puo' essere cambiato runtime via env var
senza restart — utile per incident response.
"""

from unittest.mock import MagicMock, patch

import pytest
from celery.beat import Scheduler

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeScheduler(PlenoraBeatScheduler):
    def __init__(self, leader_id="pid-1@host-a"):
        self._db_loaded = False
        self.data = {}
        self.app = MagicMock()
        self.app.now = MagicMock(return_value=MagicMock(name="now"))
        self._last_stuck_check = 0.0
        self._last_eseguibili_cleanup = 0.0
        self._last_logs_retention = 0.0
        self._last_imdf_bundles_cleanup = 0.0
        self._leader_id = leader_id
        self._is_leader = False
        self._redis_client = None


# =============================================================================
# Default: fail-closed
# =============================================================================


def test_default_fail_closed_when_redis_unavailable(monkeypatch):
    """Default: Redis down → _try_become_leader ritorna False → beat standby."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False
    )
    scheduler = _FakeScheduler()

    with patch.object(scheduler, "_get_redis", return_value=None):
        result = scheduler._try_become_leader()

    assert result is False
    assert scheduler._is_leader is False


def test_fail_closed_logs_warning(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False
    )
    scheduler = _FakeScheduler()

    with patch.object(scheduler, "_get_redis", return_value=None), \
         caplog.at_level(logging.WARNING, logger="workers.beat_scheduler"):
        scheduler._try_become_leader()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("LEADER" in m and "standby" in m.lower() for m in warnings), (
        f"Nessun WARNING standby nei log: {warnings}"
    )


def test_fail_closed_tick_skips_all_work_when_redis_down(monkeypatch):
    """Il beat in fail-closed standby NON fa sync, heartbeat, cleanup."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False
    )
    scheduler = _FakeScheduler()

    with patch.object(scheduler, "_get_redis", return_value=None), \
         patch.object(scheduler, "_sync_from_db") as sync, \
         patch.object(scheduler, "_emit_catchup_runs") as catchup, \
         patch.object(scheduler, "_write_heartbeat") as heartbeat, \
         patch.object(Scheduler, "tick") as super_tick:
        scheduler.tick()

    sync.assert_not_called()
    catchup.assert_not_called()
    heartbeat.assert_not_called()
    super_tick.assert_not_called()


# =============================================================================
# Fail-open esplicito (flag True)
# =============================================================================


def test_fail_open_flag_restores_single_instance_leader(monkeypatch):
    """Con flag True: Redis down → comportamento legacy (io-sono-leader)."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True
    )
    scheduler = _FakeScheduler()

    with patch.object(scheduler, "_get_redis", return_value=None):
        result = scheduler._try_become_leader()

    assert result is True
    assert scheduler._is_leader is True


# =============================================================================
# Errore durante acquire/refresh: stessa semantica del ping down
# =============================================================================


def test_exception_during_acquire_is_fail_closed_by_default(monkeypatch):
    """Se Redis raggiungibile ma il SET/GET solleva exception, default
    e' fail-closed: meglio no beat che doppi beat durante un blip."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False
    )
    scheduler = _FakeScheduler()

    fake_client = MagicMock()
    fake_client.set.side_effect = RuntimeError("connection reset")

    with patch.object(scheduler, "_get_redis", return_value=fake_client):
        result = scheduler._try_become_leader()

    assert result is False
    assert scheduler._is_leader is False


def test_exception_during_acquire_with_fail_open_true(monkeypatch):
    """Flag True + exception → fail-open (behavior legacy)."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True
    )
    scheduler = _FakeScheduler()

    fake_client = MagicMock()
    fake_client.set.side_effect = RuntimeError("connection reset")

    with patch.object(scheduler, "_get_redis", return_value=fake_client):
        result = scheduler._try_become_leader()

    assert result is True
