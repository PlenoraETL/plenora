"""Lock release helpers for scheduler execution."""

from __future__ import annotations

import random
import time
from typing import Callable, Protocol

from app.shared.resilience import is_transient
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_RELEASE_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 5.0
BUDGET_TOTAL_SECONDS = 10.0


class ReleaseConcurrencyProtocol(Protocol):
    def release(self, schedule_id: str) -> None:
        ...


def release_lock_with_retry(
    concurrency: ReleaseConcurrencyProtocol,
    schedule_id: str,
    *,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[float, float], float] | None = None,
) -> None:
    """Release scheduler lock with bounded retries for transient PG errors."""
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    jitter = jitter or random.uniform
    try:
        release_success = False
        last_transient_err: BaseException | None = None
        retry_start = monotonic()
        for attempt in range(MAX_RELEASE_RETRIES):
            try:
                concurrency.release(schedule_id)
                release_success = True
                break
            except Exception as transient_err:
                # Whitelist transitori centralizzata (#3): PG OperationalError/
                # DeadlockDetected + connection (08xxx) + 57Pxx. I non-transitori
                # (bug logici) propagano all'except esterno (log [CRITICO]).
                if not is_transient(transient_err):
                    raise
                last_transient_err = transient_err
                elapsed = monotonic() - retry_start
                if (
                    attempt >= MAX_RELEASE_RETRIES - 1
                    or elapsed >= BUDGET_TOTAL_SECONDS
                ):
                    break
                backoff = min(
                    BACKOFF_BASE_SECONDS * (2 ** attempt),
                    BACKOFF_MAX_SECONDS,
                )
                logger.warning(
                    f"[GESTITO] release lock {schedule_id} transient "
                    f"(tentativo {attempt + 1}/{MAX_RELEASE_RETRIES}): "
                    f"{transient_err}"
                )
                sleep(backoff * jitter(0.8, 1.2))
        if not release_success:
            logger.error(
                f"[CRITICO] Lock {schedule_id} non rilasciato dopo "
                f"{MAX_RELEASE_RETRIES} tentativi transient. Scadra' per TTL.",
                exc_info=last_transient_err,
            )
    except Exception as release_err:
        logger.error(
            f"[CRITICO] Lock {schedule_id} non rilasciato - errore "
            f"non-transient: {release_err}. Scadra' per TTL.",
            exc_info=True,
        )
