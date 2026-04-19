"""Idempotency guard condiviso tra i task asincroni (C4 round 3).

Motivazione
-----------
I task che scrivono su sistemi a valle (CARICA → DB cliente, ARRICCHISCI
con carica encadenato, futuri connettori) hanno tutti lo stesso bisogno:
se un retry manuale o un replay porta a ri-invocare un task gia'
completato, non devono ri-scrivere.

Il pattern originale e' stato sviluppato in `app/core/carica/idempotency.py`
durante C4 round 2; questo modulo condiviso lo promuove a shared/ per
riuso tra moduli (arricchisci, futuri).

Semantica per status (invariata da C4 round 2):
- `completed` → True (idempotency hit, skip)
- `failed`    → False (retry legittimo)
- `running`   → False (no block su race rara)
- missing     → False (primo tentativo)
- DB error    → False (fail-open: meglio ri-eseguire che perdere run legittima)
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


def is_already_completed(run_id: str) -> bool:
    """True se il run_id esiste in intermediate.runs con
    status='completed'. False in tutti gli altri casi (missing,
    running, failed, DB error, empty key)."""
    if not run_id:
        return False
    try:
        from app.core.intermediate import db as idb
        row = idb.get_run(run_id)
        if not row:
            return False
        return row.get("status") == "completed"
    except Exception as e:
        logger.warning(
            f"[IDEMPOTENCY] get_run failed for {run_id}: {e} — "
            f"fail-open, procedo con esecuzione"
        )
        return False
