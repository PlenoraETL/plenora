"""Helper di bootstrap per worker/beat Celery nei test funzionali."""

from __future__ import annotations

from urllib.parse import urlparse
import sys
from pathlib import Path
from typing import Optional

from tests2.functional.harness.processes import ManagedProcess, spawn_process


def spawn_celery_worker(
    *,
    workdir: Path,
    env: Optional[dict] = None,
    loglevel: str = "warning",
) -> ManagedProcess:
    """Avvia un worker Celery locale con pool solo.

    Non fa readiness check attivo: i test funzionali async dovranno
    definire un handshake piu' specifico (per esempio submit + poll).
    """
    return spawn_process(
        name="celery-worker",
        command=[
            sys.executable,
            "-m",
            "celery",
            "-A",
            "workers.celery_app",
            "worker",
            "--pool=solo",
            "--loglevel",
            loglevel,
        ],
        workdir=workdir,
        env=env,
    )


def spawn_celery_beat(
    *,
    workdir: Path,
    env: Optional[dict] = None,
    loglevel: str = "warning",
) -> ManagedProcess:
    """Avvia Celery beat con lo scheduler custom del progetto."""
    return spawn_process(
        name="celery-beat",
        command=[
            sys.executable,
            "-m",
            "celery",
            "-A",
            "workers.celery_app",
            "beat",
            "-S",
            "workers.beat_scheduler.PlenoraBeatScheduler",
            "--loglevel",
            loglevel,
        ],
        workdir=workdir,
        env=env,
    )


def flush_redis_db(redis_url: str) -> None:
    """Svuota il DB Redis indicato dall'URL.

    Usato dai test integration/functional per evitare task/result orfani
    tra run successive. Se la dependency `redis` non è disponibile o il
    server non è raggiungibile, solleva esplicitamente: è infrastruttura
    di test che deve funzionare, non best-effort silenzioso.
    """
    parsed = urlparse(redis_url)
    db = int((parsed.path or "/0").lstrip("/") or "0")
    import redis
    from redis.exceptions import RedisError

    client = redis.Redis(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=db,
        password=parsed.password,
        socket_connect_timeout=5,
        socket_timeout=5,
        decode_responses=False,
    )
    try:
        client.flushdb()
    except RedisError as exc:
        raise RuntimeError(
            "Redis test dependency is not reachable at "
            f"{redis_url!r}. For host-run tests, publish Redis and set "
            "CELERY_BROKER_URL/REDIS_URL to redis://127.0.0.1:6379/0; "
            "inside containers, set it to the service hostname, e.g. "
            "redis://redis:6379/0."
        ) from exc
