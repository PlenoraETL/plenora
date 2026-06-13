"""Contratti del runner APScheduler del beat (Plenora 2.0, Fase 3 F3.3).

Sostituisce 'celery beat -S PlenoraBeatScheduler' con 'python -m workers.beat'.
Il loop e' APScheduler; la logica resta in PlenoraBeatScheduler.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("apscheduler", reason="apscheduler non installato (Fase 3)")

from workers import beat as beat_runner
from workers.beat_scheduler import DEFAULT_MAX_INTERVAL, PlenoraBeatScheduler


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_build_engine_returns_plenora_scheduler_with_pg_leader_store():
    engine = beat_runner.build_engine()
    assert isinstance(engine, PlenoraBeatScheduler)
    # Leader lock su PG (non Redis): lo store deve essere agganciato.
    from app.core.scheduler import beat_leader_store

    assert engine._leader_store is beat_leader_store
    assert engine._is_leader is False


def test_build_scheduler_registers_single_interval_tick_job():
    engine = beat_runner.build_engine()
    scheduler = beat_runner.build_scheduler(engine)

    job = scheduler.get_job(beat_runner._TICK_JOB_ID)
    assert job is not None, "il runner deve registrare il job di tick"
    # Stessa cadenza del beat Celery.
    assert int(job.trigger.interval.total_seconds()) == int(DEFAULT_MAX_INTERVAL)
    # Il job chiama engine.tick (la logica proprietaria).
    assert job.func == engine.tick
    # Niente esecuzioni concorrenti se un tick e' lento.
    assert job.max_instances == 1
    assert job.coalesce is True


def test_run_wires_signal_handlers_and_close_on_shutdown():
    src = inspect.getsource(beat_runner.run)
    # SIGTERM/SIGINT registrati per shutdown pulito.
    assert "signal.SIGTERM" in src
    assert "signal.SIGINT" in src
    # Su shutdown rilascia il lock leader (engine.close) e ferma APScheduler.
    assert "engine.close()" in src
    assert "scheduler.shutdown(" in src
    # Primo tick immediato all'avvio (no attesa del primo intervallo).
    assert "engine.tick()" in src


def test_runner_uses_blocking_scheduler():
    src = inspect.getsource(beat_runner)
    assert "BlockingScheduler" in src
