"""Runner del beat su APScheduler (Plenora 2.0, Fase 3 F3.3).

Rimpiazza ``celery beat -S workers.beat_scheduler.PlenoraBeatScheduler``:
il loop e' APScheduler (nativo Windows/Linux/container, niente fork), la
logica resta in ``PlenoraBeatScheduler`` (leader election su PostgreSQL,
sync schedule dal DB, catchup, manutenzione, emit via layer dispatch).

Plenora 2.0 Fase 5: l'engine e' celery-free (la date-math cron/interval e'
su croniter, vedi app.core.scheduler.cron_schedule); l'emit dei task va via
``dispatch_task_by_name`` (Dramatiq).

Avvio: ``python -m workers.beat``.
"""

from __future__ import annotations

import signal

from apscheduler.schedulers.blocking import BlockingScheduler

from app.utils.logger import get_logger
from workers.beat_scheduler import DEFAULT_MAX_INTERVAL, PlenoraBeatScheduler

logger = get_logger(__name__)

_TICK_JOB_ID = "plenora-beat-tick"


def build_engine() -> PlenoraBeatScheduler:
    """Istanzia l'engine di scheduling (celery-free).

    Gli schedule vengono caricati dal DB a ogni tick via ``_sync_from_db``;
    la date-math cron/interval e' su croniter."""
    return PlenoraBeatScheduler()


def build_scheduler(engine: PlenoraBeatScheduler) -> BlockingScheduler:
    """Costruisce il BlockingScheduler con un solo job a intervallo.

    ``coalesce=True`` + ``max_instances=1``: un tick lento non accumula
    esecuzioni concorrenti. Il valore di ritorno di ``tick`` (intervallo di
    sleep adattivo di Celery) e' ignorato: APScheduler usa l'intervallo
    fisso ``DEFAULT_MAX_INTERVAL``, stessa cadenza del beat Celery."""
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        engine.tick,
        trigger="interval",
        seconds=DEFAULT_MAX_INTERVAL,
        id=_TICK_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=int(DEFAULT_MAX_INTERVAL),
    )
    return scheduler


def _bootstrap_beat_process() -> None:
    """Esegue il bootstrap 4-step nel processo beat (rimpiazzo del signal
    Celery ``beat_init``, perso col passaggio ad APScheduler in F3.3).

    Fail-closed: un'eccezione da ``bootstrap`` propaga e impedisce l'avvio
    del beat contro un DB / storage / broker mal configurato. Idempotente
    via ``is_bootstrapped`` (processo singolo, niente fork)."""
    from app import bootstrap as _bootstrap_mod

    if _bootstrap_mod.is_bootstrapped():
        return
    _bootstrap_mod.bootstrap()


def run() -> None:
    _bootstrap_beat_process()
    engine = build_engine()
    scheduler = build_scheduler(engine)

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("[BEAT] Segnale %s ricevuto, arresto runner APScheduler", signum)
        try:
            scheduler.shutdown(wait=False)
        finally:
            # Rilascia il lock leader su PG cosi' un altro beat puo'
            # prenderlo subito invece di aspettare il TTL.
            engine.close()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info(
        "[BEAT] Runner APScheduler avviato (tick ogni %ss, leader lock su PG)",
        DEFAULT_MAX_INTERVAL,
    )
    # Esegui un primo tick subito invece di aspettare il primo intervallo,
    # cosi' gli schedule dovuti partono senza ritardo all'avvio.
    try:
        engine.tick()
    except Exception:
        logger.warning("[BEAT] Primo tick fallito (riprovo al prossimo)", exc_info=True)
    scheduler.start()


if __name__ == "__main__":
    run()
