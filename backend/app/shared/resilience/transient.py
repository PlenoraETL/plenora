"""Classificatore centralizzato degli errori transitori (whitelist retry).

Consolida la logica ad-hoc sparsa in un unico predicato `is_transient(exc)`:
- scheduler_release: `psycopg.OperationalError` / `DeadlockDetected`;
- datasources (sftp/ftp/sharepoint): `socket.timeout`/`gaierror`, `OSError` di rete;
- enrichment HTTP: status 429/5xx (`RetryConfig.retry_on_status`).

Fondazione riusabile dal retry-whitelist a livello pipeline e dai retry
localizzati esistenti.

Proprieta':
- **Conservativo**: default a `False`. Ritenta SOLO cio' che e' riconosciuto
  transitorio; tutto il resto (ValueError, auth-failure, vincolo violato,
  file-not-found, ...) NON va ritentato.
- **Puro**: nessuno stato, nessun side-effect.
- **Dipendenze opzionali best-effort**: psycopg/requests sono importati a
  import-time con guardia; se assenti i relativi rami si disattivano senza
  rompere l'import del modulo.
"""

from __future__ import annotations

import errno as _errno
import socket
from typing import Any, Tuple, Type

# --- HTTP ------------------------------------------------------------------
# Allineato a app/core/arricchisci/models.py RetryConfig.retry_on_status.
TRANSIENT_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def is_transient_http_status(status: Any) -> bool:
    """True se uno status HTTP e' transitorio (429 o 5xx tipici)."""
    if status is None:
        return False
    try:
        return int(status) in TRANSIENT_HTTP_STATUSES
    except (TypeError, ValueError):
        return False


# --- Postgres SQLSTATE -----------------------------------------------------
# 40001 serialization_failure, 40P01 deadlock_detected, 57P01 admin_shutdown,
# 57P02 crash_shutdown, 57P03 cannot_connect_now.
_TRANSIENT_SQLSTATES: frozenset[str] = frozenset(
    {"40001", "40P01", "57P01", "57P02", "57P03"}
)
# Classi SQLSTATE (prefisso 2 char): 08 connection_exception,
# 53 insufficient_resources.
_TRANSIENT_SQLSTATE_CLASSES: Tuple[str, ...] = ("08", "53")

# --- OSError errno di rete -------------------------------------------------
_TRANSIENT_ERRNOS: frozenset[int] = frozenset(
    code
    for code in (
        getattr(_errno, name, None)
        for name in (
            "ECONNRESET",
            "ECONNREFUSED",
            "ECONNABORTED",
            "ETIMEDOUT",
            "EHOSTUNREACH",
            "ENETUNREACH",
            "ENETDOWN",
            "EPIPE",
            "EAGAIN",
        )
    )
    if code is not None
)

# --- Tipi base sempre transitori (no dipendenze esterne) -------------------
# socket.timeout e' alias di TimeoutError (py>=3.10); ConnectionError copre
# ConnectionResetError/RefusedError/AbortedError.
_BASE_TRANSIENT_TYPES: Tuple[Type[BaseException], ...] = (
    socket.timeout,
    socket.gaierror,
    TimeoutError,
    ConnectionError,
)

# --- Tipi da SDK opzionali (best-effort, a livello-modulo) -----------------
_optional: list[Type[BaseException]] = []
try:  # pragma: no cover - dipende dall'ambiente
    import psycopg

    _optional.append(psycopg.OperationalError)
except Exception:  # noqa: BLE001 - assenza SDK non deve rompere l'import
    pass
try:  # pragma: no cover
    import requests

    _optional.extend((requests.exceptions.Timeout, requests.exceptions.ConnectionError))
except Exception:  # noqa: BLE001
    pass

_OPTIONAL_TRANSIENT_TYPES: Tuple[Type[BaseException], ...] = tuple(_optional)


def _sqlstate_is_transient(state: Any) -> bool:
    if not state:
        return False
    text = str(state)
    return text in _TRANSIENT_SQLSTATES or text[:2] in _TRANSIENT_SQLSTATE_CLASSES


def is_transient(exc: BaseException) -> bool:
    """True se `exc` e' un errore transitorio da ritentare (whitelist).

    Conservativo: default a False per gli errori non riconosciuti.
    """
    if isinstance(exc, _BASE_TRANSIENT_TYPES):
        return True
    if _OPTIONAL_TRANSIENT_TYPES and isinstance(exc, _OPTIONAL_TRANSIENT_TYPES):
        return True
    # Postgres: psycopg espone `.sqlstate`; alcuni wrapper usano `.pgcode`.
    if _sqlstate_is_transient(
        getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    ):
        return True
    # OSError di rete per errno (i casi socket/timeout sono gia' coperti sopra).
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNOS:
        return True
    # HTTP error con response status transitorio (es. requests.HTTPError).
    response = getattr(exc, "response", None)
    if response is not None and is_transient_http_status(
        getattr(response, "status_code", None)
    ):
        return True
    return False
