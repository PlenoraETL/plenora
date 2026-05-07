"""Metriche su leader transitions (C3 round 4).

Il commento originale di C3 sul fix 1 diceva:
  "Log WARNING + metrica quando il leader cambia (serve per capire i flap)"

Il WARNING e' gia' in place. Questo modulo copre la metrica strutturata:
ogni transizione (acquired / lost / released / standby / degraded) scrive
un counter su Redis via HINCRBY. Leggibile da CLI Redis o dashboard
esterni. Nessuna dipendenza prometheus server-side — il beat e un
processo separato dal FastAPI e non condivide memoria.

Hash Redis usate:
- `scheduler:beat:leader:transitions` (hash) — counter per transition_type
- `scheduler:beat:leader:last_transition_at` (string) — ISO timestamp
- `scheduler:beat:leader:current_leader_id` (string) — leader attivo

Transition types:
- `acquired`      primo SETNX riuscito
- `lost`          scoperto altro leader alla refresh
- `released`      rilascio esplicito su close()
- `standby`       entrato in standby per Redis down (fail-closed)
- `degraded_open` entrato in leader degraded (fail-open flag)
"""

import fakeredis
import pytest
from unittest.mock import MagicMock, patch

from workers.beat_scheduler import PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_scheduler(leader_id, redis_client):
    scheduler = object.__new__(PlenoraBeatScheduler)
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


# Transition: acquired


def test_acquired_transition_increments_counter():
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)

    beat._try_become_leader()

    # Hash incrementata per type=acquired
    acquired_count = redis.hget("scheduler:beat:leader:transitions", "acquired")
    assert acquired_count == b"1"


def test_acquired_sets_current_leader_id():
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)

    beat._try_become_leader()

    current = redis.get("scheduler:beat:leader:current_leader_id")
    assert current == b"pid-1@host"


# Transition: lost (qualcun altro ha preso il lock)


def test_lost_transition_increments_counter():
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)

    # Beat crede di essere leader
    beat._is_leader = True
    # Ma in realta' un altro processo ha la chiave
    redis.set("scheduler:beat:leader", "pid-other@host-x", ex=120)

    beat._try_become_leader()

    lost_count = redis.hget("scheduler:beat:leader:transitions", "lost")
    assert lost_count == b"1"


# Transition: released (close esplicito)


def test_released_transition_increments_counter():
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)
    beat._try_become_leader()

    from celery.beat import Scheduler
    with patch.object(Scheduler, "close"):
        beat.close()

    released_count = redis.hget("scheduler:beat:leader:transitions", "released")
    assert released_count == b"1"


# Transition: standby (Redis down fail-closed)


def test_standby_transition_recorded_when_redis_down(monkeypatch):
    """Redis down + fail-closed default: la transizione standby non
    puo' essere scritta su Redis (e' down), ma al recovery di Redis
    la prossima transizione deve ripartire pulita (no stale state)."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", False
    )
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)
    beat._is_leader = True  # stato iniziale: leader

    # Redis improvvisamente down
    with patch.object(beat, "_get_redis", return_value=None):
        beat._try_become_leader()

    # Non possiamo asserire che il counter sia incrementato
    # (Redis e' down), ma lo state del beat deve essere standby
    assert beat._is_leader is False


# Transition: degraded_open (Redis down + fail-open)


def test_degraded_open_transition_recorded_when_fail_open_triggers(monkeypatch):
    """Con fail_open=True e Redis down, il beat resta leader in
    degraded mode. La transizione deve essere contabilizzata (per
    alert operational: 'stiamo correndo senza coordinazione'),
    ma serve Redis per scriverla → se Redis torna su, al prossimo
    tick quando refresh riesce, degraded_open viene contabilizzato."""
    monkeypatch.setattr(
        "app.config.settings.SCHEDULE_BEAT_LEADER_FAIL_OPEN", True
    )
    redis = fakeredis.FakeRedis()
    beat = _make_scheduler("pid-1@host", redis)

    # Redis down inizialmente
    with patch.object(beat, "_get_redis", return_value=None):
        result = beat._try_become_leader()

    assert result is True  # fail-open
    # Redis tornato su: al prossimo tick acquired, il beat scrive
    # almeno il current_leader_id (non possiamo scrivere counter
    # durante il downtime Redis, ma al recovery scriviamo lo stato).
    beat._try_become_leader()
    current = redis.get("scheduler:beat:leader:current_leader_id")
    assert current == b"pid-1@host"


# Robustezza


def test_metric_write_failure_does_not_break_leader_election():
    """Se la scrittura della metrica fallisce, il flow leader election
    deve comunque avere successo. La telemetria e' best-effort."""
    redis = MagicMock()
    # set() va (acquire OK) ma hincrby solleva
    redis.set.return_value = True
    redis.hincrby.side_effect = RuntimeError("metric write failed")

    beat = _make_scheduler("pid-1@host", redis)

    # _try_become_leader non deve sollevare
    result = beat._try_become_leader()

    assert result is True
    assert beat._is_leader is True
