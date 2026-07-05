"""Retry helper per transient errors su integrazioni esterne.

Pattern condiviso per FTP/SFTP/SharePoint/HTTP transient con retry e
backoff esponenziale, per assorbire glitch di rete temporanei.

Funzione pura (no stato globale, no decoratori magici): mappa
direttamente a un Go-equivalente come
`retryTransient(fn func() error, opts RetryOpts) error`.

Uso tipico:
```python
from app.shared.connections.retry import call_with_retry

result = call_with_retry(
    lambda: sftp_client.connect(**kwargs),
    op_name="sftp.connect",
    max_attempts=3,
    backoff_base=0.5,
    retriable_excs=(ConnectionError, TimeoutError, OSError),
)
```
"""

from __future__ import annotations

import time
from typing import Any, Callable, Tuple, Type

from app.utils.logger import get_logger

logger = get_logger(__name__)


_DEFAULT_TRANSIENT: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def call_with_retry(
    fn: Callable[[], Any],
    *,
    op_name: str,
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    backoff_factor: float = 2.0,
    retriable_excs: Tuple[Type[BaseException], ...] = _DEFAULT_TRANSIENT,
) -> Any:
    """Invoca `fn()` con retry+exponential backoff su transient errors.

    Args:
        fn: callable zero-arg da invocare. Tipicamente un lambda che
            cattura via closure i parametri della chiamata reale.
        op_name: nome operazione per logging (es. "sftp.connect").
        max_attempts: tentativi totali (incluso il primo). Default 3.
        backoff_base: secondi tra il primo retry. Default 1.0s.
        backoff_factor: moltiplicatore per ogni retry. Default 2.0
            (=> 1s, 2s, 4s con max_attempts=4).
        retriable_excs: tuple di exception class considerate transient.
            Eccezioni FUORI da questa lista propagano immediatamente
            senza retry — utile per distinguere `AuthenticationException`
            (non-retriable) da `socket.timeout` (retriable).

    Returns:
        Il valore di ritorno di `fn()`.

    Raises:
        L'ultima eccezione transient se `max_attempts` esauriti, oppure
        la prima eccezione NON-retriable.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except retriable_excs as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                logger.warning(
                    f"[retry_transient] {op_name} fallito dopo "
                    f"{max_attempts} attempt: {type(exc).__name__}",
                )
                raise
            wait_s = backoff_base * (backoff_factor ** attempt)
            logger.info(
                f"[retry_transient] {op_name} attempt {attempt + 1} "
                f"fallito ({type(exc).__name__}), retry in {wait_s:.1f}s",
            )
            time.sleep(wait_s)
    # Unreachable: la return o il raise sopra coprono tutti i casi.
    raise last_exc  # type: ignore[misc]
