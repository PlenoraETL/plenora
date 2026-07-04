"""Helper di bootstrap per worker/beat nei test funzionali.

Rinominato da ``celery.py`` (2026-07-04): il worker e' Dramatiq su
RabbitMQ e ``flush_redis_db`` e' stato rimosso (Redis non esiste piu';
l'isolamento fra run e' via code suffissate, vedi ``env.isolate_task_queues``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from tests2.functional.harness.processes import ManagedProcess, spawn_process


def spawn_worker(
    *,
    workdir: Path,
    env: Optional[dict] = None,
    loglevel: str = "warning",
) -> ManagedProcess:
    """Avvia un worker dei task locale (Dramatiq su RabbitMQ).

    ``python -m dramatiq workers.dramatiq_app`` (1 processo / 4 thread).
    Non fa readiness check attivo: i test async definiscono un handshake
    piu' specifico (submit + poll su PG).
    """
    _ = loglevel  # firma storica; il CLI dramatiq non ha un flag loglevel
    return spawn_process(
        name="dramatiq-worker",
        command=[
            sys.executable,
            "-m",
            "dramatiq",
            "workers.dramatiq_app",
            "--processes",
            "1",
            "--threads",
            "4",
        ],
        workdir=workdir,
        env=env,
    )


def spawn_beat(
    *,
    workdir: Path,
    env: Optional[dict] = None,
    loglevel: str = "warning",
) -> ManagedProcess:
    """Avvia il beat del progetto (APScheduler, ``python -m workers.beat``).

    La logica (leader su PG, sync, catchup, emit via dispatch) e' quella
    di produzione."""
    _ = loglevel
    return spawn_process(
        name="plenora-beat",
        command=[
            sys.executable,
            "-m",
            "workers.beat",
        ],
        workdir=workdir,
        env=env,
    )
