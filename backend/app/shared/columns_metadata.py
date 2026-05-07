"""
columns_metadata.py — Helper condiviso per salvare `columns_metadata`

sul canvas di un progetto, con retry su errori transient e raise su
"""

from __future__ import annotations

import time as _time
from typing import Any, List, Optional

from app.shared.exceptions import ColumnsMetadataSaveError


# Costanti retry/backoff

# 4 tentativi totali, 3 sleep tra i retry. Sequenza copre transient
# DB fino a ~1.7s di recovery — oltre, non e' transient
_BACKOFF_SEC = (0.2, 0.5, 1.0)
_MAX_ATTEMPTS = 4  # = len(_BACKOFF_SEC) + 1


# Lazy-init: import psycopg2 solo all'uso (evita penalty in dev/test
# senza driver installato
_TRANSIENT_EXCEPTIONS: Optional[tuple] = None


def _get_transient_exceptions() -> tuple:
    global _TRANSIENT_EXCEPTIONS
    if _TRANSIENT_EXCEPTIONS is None:
        try:
            import psycopg2
            import psycopg2.errors as _pg_err
            _TRANSIENT_EXCEPTIONS = (
                psycopg2.OperationalError,
                _pg_err.DeadlockDetected,
                _pg_err.LockNotAvailable,
                _pg_err.IdleInTransactionSessionTimeout,
                TimeoutError,
            )
        except ImportError:
            # Dev env senza psycopg2: solo TimeoutError
            _TRANSIENT_EXCEPTIONS = (TimeoutError,)
    return _TRANSIENT_EXCEPTIONS


# API pubblica


def save_columns_metadata_with_retry(
    *,
    project_service: Any,
    project_id: str,
    columns_metadata: List[dict],
    logger: Any,
    op: str = "save_columns_metadata",
) -> None:
    """Salva `columns_metadata` sul canvas del progetto.

    Args:
        project_service: istanza di ProjectService (o duck-typing
            equivalente che esponga `update_project_columns_metadata`).
        project_id: id del progetto target.
        columns_metadata: lista `[{"name": str, "type": str}, ...]`.
        logger: logger del modulo chiamante (per coerenza nei log).
        op: tag operativo per il logger structured (default
            "save_columns_metadata").

    Raises:
        ColumnsMetadataSaveError: su fallimento permanente (transient
            esauriti, errore non-transient, progetto inesistente).
            L'attributo `result_already_persisted=True` segnala che il
            blob risultato e' gia' stato scritto downstream — il caller
            puo' usarlo per messaggi user-facing piu' precisi.

    Comportamento:
      - Errori transient: retry con backoff 0.2s → 0.5s → 1.0s. Se
        tutti 3 tentativi falliscono, raise.
      - Errori non-transient: raise SUBITO senza retry (retryare un bug
        amplifica il problema).
      - `update_project_columns_metadata` ritorna False: progetto
        inesistente (raro, possibile se cancellato durante la run) →
        raise senza retry.
    """
    transient_exc = _get_transient_exceptions()
    last_error: Optional[Exception] = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        call_returned_false = False
        try:
            ok = project_service.update_project_columns_metadata(
                project_id, columns_metadata,
            )
            call_returned_false = not ok
        except transient_exc as e:
            last_error = e
            if attempt < _MAX_ATTEMPTS:
                backoff = _BACKOFF_SEC[attempt - 1]
                logger.warning(
                    f"Save columns_metadata transient fail (tentativo "
                    f"{attempt}/{_MAX_ATTEMPTS}) per {project_id}: "
                    f"{type(e).__name__}: {e}. Retry tra {backoff}s.",
                    op=op,
                )
                _time.sleep(backoff)
            continue
        except Exception as e:
            logger.error(
                f"Save columns_metadata fallito con errore non-transient "
                f"per {project_id}: {type(e).__name__}: {e}",
                op=op,
                exc_info=True,
            )
            raise ColumnsMetadataSaveError(
                project_id=project_id,
                reason=f"{type(e).__name__}: {e}",
                attempts=attempt,
                result_already_persisted=True,
            ) from e

        if call_returned_false:
            logger.error(
                f"Save columns_metadata: progetto {project_id} non "
                f"trovato al momento del salvataggio (eliminato "
                f"durante l'esecuzione?)",
                op=op,
            )
            raise ColumnsMetadataSaveError(
                project_id=project_id,
                reason="Progetto non trovato al salvataggio metadata "
                       "(possibile cancellazione durante run)",
                attempts=attempt,
                result_already_persisted=True,
            )

        # Successo
        if attempt > 1:
            logger.info(
                f"Save columns_metadata riuscito al tentativo "
                f"{attempt}/{_MAX_ATTEMPTS} per progetto {project_id}",
                op=op,
            )
        return

    # Tutti i retry transient sono falliti
    logger.error(
        f"Save columns_metadata fallito dopo {_MAX_ATTEMPTS} tentativi "
        f"per {project_id}: {type(last_error).__name__}: {last_error}",
        op=op,
        exc_info=True,
    )
    raise ColumnsMetadataSaveError(
        project_id=project_id,
        reason=f"{type(last_error).__name__}: {last_error}",
        attempts=_MAX_ATTEMPTS,
        result_already_persisted=True,
    ) from last_error
