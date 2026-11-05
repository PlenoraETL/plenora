"""
Orari mandati al client: SEMPRE in UTC esplicito (Fase 5 T7, correzione).

Regola: un istante che esce dal backend (risposta HTTP, token di una run)
o porta il fuso (``isoformat()`` di un datetime aware: ``+00:00``/``+01:00``)
oppure, se il formato non ha il fuso (``YYYYMMDD_HHMMSS``), è in UTC. Il
frontend (``utils/format.parseServerDate``) legge in UTC gli orari senza
fuso. I ``TIMESTAMPTZ`` letti da PostgreSQL arrivano nel fuso della SESSIONE
(su Windows nativo il fuso del sistema, es. Europe/Rome): formattarli senza
fuso e senza conversione sposterebbe l'ora di una o due ore.

Un datetime naive è UTC per convenzione del backend (``datetime.now(timezone.utc)``
ovunque; il test di architettura vieta i naive locali).

Il test ``tests2/shared/test_utc_time_responses_contract.py`` vieta i
``strftime`` senza fuso su datetime non già UTC fuori da qui.
"""

from __future__ import annotations

from datetime import datetime, timezone

RUN_STAMP_FORMAT = "%Y%m%d_%H%M%S"


def as_utc(value: datetime) -> datetime:
    """Lo stesso istante in UTC (naive = già UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_stamp(value: datetime) -> str:
    """``YYYYMMDD_HHMMSS`` in UTC (token delle run degli schedule)."""
    return as_utc(value).strftime(RUN_STAMP_FORMAT)


__all__ = ["RUN_STAMP_FORMAT", "as_utc", "utc_stamp"]
