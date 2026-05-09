"""
Integration test dei 5 scenari operativi della leader election (C3).

Copre end-to-end i 5 test suggeriti manualmente via `fakeredis` (in-memory
"""

import time
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from celery.beat import Scheduler

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.integration, pytest.mark.scheduler, pytest.mark.contract]


def _make_scheduler(leader_id: str, redis_client) -> PlenoraBeatScheduler:
    """Crea un PlenoraBeatScheduler senza Celery bootstrap + Redis iniettato."""
    scheduler = object.__new__(PlenoraBeatScheduler)  # skip __init__
    scheduler._db_loaded = False
    scheduler.data = {}
    scheduler.app = MagicMock()
    scheduler.app.now = MagicMock(return_value=MagicMock(name="now"))
    scheduler._last_stuck_check = 0.0
    scheduler._last_eseguibili_cleanup = 0.0
    scheduler._last_logs_retention = 0.0
    scheduler._last_imdf_bundles_cleanup = 0.0
    scheduler._leader_id = leader_id
    scheduler._is_leader = False
    scheduler._redis_client = redis_client
    return scheduler


# SCENARIO 1 — Doppio beat stesso Redis


def test_scenario_1_two_beats_only_one_acquires_leadership():
    """Due scheduler contro lo stesso Redis: solo uno diventa leader."""
    shared_redis = fakeredis.FakeRedis()

    beat_a = _make_scheduler("pid-1@host-a", shared_redis)
    beat_b = _make_scheduler("pid-2@host-b", shared_redis)

    result_a = beat_a._try_become_leader()
    result_b = beat_b._try_become_leader()

    assert (result_a, result_b) == (True, False)
    assert (beat_a._is_leader, beat_b._is_leader) == (True, False)

    # Solo leader_id di A e' nella chiave Redis
    stored = shared_redis.get("scheduler:beat:leader")
    assert stored == b"pid-1@host-a"


# SCENARIO 2 — SIGKILL del leader (nessun close, TTL scade)


def test_scenario_2_standby_promoted_after_leader_ttl_expiration(monkeypatch):
    """Leader acquisisce con TTL corto, NON refresha (simulazione SIGKILL
    dove il processo muore senza eseguire il close), TTL scade → un altro
    beat al tick successivo diventa leader."""
    # TTL 1s per accelerare il test
    monkeypatch.setattr(
        "workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1
    )
    shared_redis = fakeredis.FakeRedis()

    beat_a = _make_scheduler("pid-killed@host-a", shared_redis)
    beat_b = _make_scheduler("pid-survivor@host-b", shared_redis)

    assert beat_a._try_become_leader() is True
    assert beat_b._try_become_leader() is False  # A e' leader

    # Simula SIGKILL: A non chiama piu' _try_become_leader (no refresh).
    # Aspettiamo che il TTL scada.
    time.sleep(1.2)

    # B al suo tick successivo → lock scaduto → B diventa leader
    assert beat_b._try_become_leader() is True
    assert beat_b._is_leader is True
    # Redis ha ora leader_id di B
    assert shared_redis.get("scheduler:beat:leader") == b"pid-survivor@host-b"


# SCENARIO 3 — Redis down temporaneo + recovery


def test_scenario_3_redis_down_fail_closed_then_recovers(monkeypatch):
    """Redis down durante un tick: default fail-closed (beat standby).
    Recovery: Redis torna su → il beat riacquisisce pulitamente."""
    # `SCHEDULE_BEAT_LEADER_FAIL_OPEN` lives in DEFAULTS dict only;
    # consumer uses `getattr(settings, key, False)`. raising=False lets
    # monkeypatch inject the attribute and remove it at teardown.
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False,
        raising=False,
    )
    shared_redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host-a", shared_redis)

    # Prima: Redis raggiungibile, acquisisco
    assert beat._try_become_leader() is True

    # Simula Redis down: _get_redis ritorna None
    with patch.object(beat, "_get_redis", return_value=None):
        result_down = beat._try_become_leader()

    assert result_down is False
    assert beat._is_leader is False

    # Recovery: Redis torna su (ripristino client). Serve pulire la chiave
    # stale (fakeredis non scade i TTL in tempo reale qui) — simuliamo
    # clean state dopo downtime.
    shared_redis.delete("scheduler:beat:leader")

    result_recovered = beat._try_become_leader()
    assert result_recovered is True
    assert beat._is_leader is True


def test_scenario_3b_redis_down_fail_open_stays_leader(monkeypatch):
    """Con flag SCHEDULE_BEAT_LEADER_FAIL_OPEN=True (behavior legacy),
    Redis down → il beat continua come leader single-instance."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True,
        raising=False,
    )
    beat = _make_scheduler("pid-1@host-a", None)

    with patch.object(beat, "_get_redis", return_value=None):
        result = beat._try_become_leader()

    assert result is True
    assert beat._is_leader is True


# SCENARIO 4 — SIGTERM graceful → close rilascia lock → successore immediato


def test_scenario_4_close_releases_lock_so_next_beat_acquires_immediately():
    """Leader A fa close() (simulato SIGTERM Celery → scheduler.close()):
    la chiave Redis e' cancellata, quindi B che si avvia subito dopo
    vince SETNX senza aspettare TTL."""
    shared_redis = fakeredis.FakeRedis()

    beat_a = _make_scheduler("pid-a@host-a", shared_redis)
    beat_b = _make_scheduler("pid-b@host-b", shared_redis)

    assert beat_a._try_become_leader() is True
    # B ora non puo' prendere (A e' leader)
    assert beat_b._try_become_leader() is False

    # A riceve SIGTERM → close() → DELETE della chiave
    with patch.object(Scheduler, "close"):
        beat_a.close()

    # Chiave liberata immediatamente
    assert shared_redis.get("scheduler:beat:leader") is None

    # B al prossimo tick vince SETNX
    assert beat_b._try_become_leader() is True
    assert beat_b._is_leader is True


def test_scenario_4b_close_does_not_release_lock_taken_by_another_after_ttl(monkeypatch):
    """Edge case del close: se tra il suo is_leader=True e il close
    vero il TTL scade e qualcun altro ha gia' acquisito, close()
    non deve cancellare il lock dell'altro."""
    monkeypatch.setattr(
        "workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1
    )
    shared_redis = fakeredis.FakeRedis()

    beat_a = _make_scheduler("pid-a@host-a", shared_redis)
    beat_b = _make_scheduler("pid-b@host-b", shared_redis)

    beat_a._try_become_leader()  # A leader, TTL 1s

    # Aspetta scadenza e lascia che B prenda
    time.sleep(1.2)
    beat_b._try_become_leader()
    assert shared_redis.get("scheduler:beat:leader") == b"pid-b@host-b"

    # Ora A chiama close() tardivamente (era vittima di GC pause, es.)
    # Deve NON cancellare il lock di B
    with patch.object(Scheduler, "close"):
        beat_a.close()

    assert shared_redis.get("scheduler:beat:leader") == b"pid-b@host-b"


# SCENARIO 5 — TTL vs refresh interval


def test_scenario_5_refresh_extends_ttl_preventing_expiration(monkeypatch):
    """Con refresh regolare a ogni tick, il TTL viene esteso e la
    leadership NON scade neanche durante run lunghe."""
    monkeypatch.setattr(
        "workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1
    )
    shared_redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host-a", shared_redis)

    # Tick 1: acquisisce
    beat._try_become_leader()

    # Simula ticks ogni 0.3s per 1.5s (5 tick). Con TTL=1s e refresh=0.3s,
    # il lock non deve mai scadere.
    for _ in range(5):
        time.sleep(0.3)
        result = beat._try_become_leader()
        assert result is True, "leadership persa durante tick regolare"

    assert shared_redis.get("scheduler:beat:leader") == b"pid-1@host-a"


def test_scenario_5b_missed_tick_longer_than_ttl_loses_leadership(monkeypatch):
    """Se il beat salta un tick piu' lungo del TTL (es. GC pause di 2s
    con TTL=1s), un altro beat puo' acquisire — caso che il sweet
    spot TTL vs interval deve prevenire."""
    monkeypatch.setattr(
        "workers.beat_scheduler._LEADER_LOCK_TTL_SECONDS", 1
    )
    shared_redis = fakeredis.FakeRedis()

    beat_a = _make_scheduler("pid-a@host-a", shared_redis)
    beat_b = _make_scheduler("pid-b@host-b", shared_redis)

    beat_a._try_become_leader()

    # "GC pause" di A: nessun refresh per > TTL
    time.sleep(1.2)

    # B in questo momento prova: lock scaduto, B lo prende
    assert beat_b._try_become_leader() is True

    # A, tornato dal GC, ritenta: vede che la chiave ha un altro
    # valore ("pid-b@host-b") e passa in standby
    assert beat_a._try_become_leader() is False
    assert beat_a._is_leader is False
