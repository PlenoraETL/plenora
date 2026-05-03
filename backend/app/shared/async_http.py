"""
app/shared/async_http.py
========================
HTTP client async condiviso basato su httpx.AsyncClient.

Usato dal refactor async dell'arricchimento (Fase 1 del piano P0).
Riutilizza connessioni TCP via connection pool configurabile.

Design:
- Singleton globale con lifecycle gestito da FastAPI / worker Celery
- Pool size configurabile via env var ENRICHMENT_POOL_SIZE (default 50)
- Retry esponenziale su errori transienti (ConnectError, TimeoutException, HTTP 5xx)
- Rispetta Retry-After header su 429
- Anti-SSRF già validato dal caller via validate_external_url()

NON sostituisce requests.Session (che rimane per chiamate sync).
Questo modulo esiste SOLO per codice async.
"""

import asyncio
import os
import random
import time
from typing import Optional, Dict, Any

import httpx

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Configurazione
# ──────────────────────────────────────────────────────────────────────

def _get_pool_size() -> int:
    """Legge pool size da Settings, con fallback e clamping di sicurezza.

    BE-CONFIG-001D: default tipato in
    `Settings.DEFAULTS["ENRICHMENT_POOL_SIZE"]=50`. Override env
    continua a funzionare via Settings (`int` coercion).
    """
    from app.config import settings as _settings
    try:
        val = int(_settings.get("ENRICHMENT_POOL_SIZE"))
        return max(5, min(val, 200))  # clamp [5, 200] per safety
    except (ValueError, TypeError):
        return 50


def _get_default_timeout() -> httpx.Timeout:
    """Timeout di default per chiamate API: connect 10s, read 30s, write 10s."""
    return httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)


# ──────────────────────────────────────────────────────────────────────
# Singleton AsyncClient
# ──────────────────────────────────────────────────────────────────────

_async_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_async_client() -> httpx.AsyncClient:
    """Ritorna l'AsyncClient condiviso, creandolo al primo accesso.

    Thread-safe via asyncio.Lock. Il client viene riutilizzato per tutta
    la durata del worker Celery (o del processo backend).

    Pool size: configurabile via ENRICHMENT_POOL_SIZE env var (default 50).
    """
    global _async_client
    if _async_client is None or _async_client.is_closed:
        async with _client_lock:
            if _async_client is None or _async_client.is_closed:
                pool_size = _get_pool_size()
                limits = httpx.Limits(
                    max_connections=pool_size,
                    max_keepalive_connections=pool_size,
                    keepalive_expiry=30.0,
                )
                _async_client = httpx.AsyncClient(
                    limits=limits,
                    timeout=_get_default_timeout(),
                    follow_redirects=False,  # security: no redirect cieco
                    verify=True,              # TLS verification
                )
                logger.info(
                    f"AsyncClient inizializzato (pool_size={pool_size})"
                )
    return _async_client


async def close_async_client() -> None:
    """Chiude il client async. Da chiamare in shutdown hook."""
    global _async_client
    if _async_client and not _async_client.is_closed:
        await _async_client.aclose()
        logger.info("AsyncClient chiuso")
    _async_client = None


# ──────────────────────────────────────────────────────────────────────
# Retry helper con backoff esponenziale
# ──────────────────────────────────────────────────────────────────────

async def async_request_with_retry(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
    data: Optional[Any] = None,
    timeout: Optional[float] = None,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
) -> httpx.Response:
    """Esegue richiesta HTTP async con retry esponenziale.

    Retry condizioni:
    - Network errors (ConnectError, ReadError, WriteError, PoolTimeout)
    - HTTP 408, 429, 500, 502, 503, 504
    - Rispetta header Retry-After su 429 (cap a 60s)

    Raises:
        httpx.HTTPStatusError: su errori 4xx non retryable (400, 401, 403, 404)
        httpx.RequestError: su fallimento definitivo dopo i retry
    """
    client = await get_async_client()

    # Costruisci i kwargs dinamicamente per evitare di passare None a campi specifici
    req_kwargs: Dict[str, Any] = {}
    if headers is not None:
        req_kwargs["headers"] = headers
    if params is not None:
        req_kwargs["params"] = params
    if json is not None:
        req_kwargs["json"] = json
    if data is not None:
        req_kwargs["data"] = data
    if timeout is not None:
        req_kwargs["timeout"] = timeout

    retryable_status = {408, 429, 500, 502, 503, 504}
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **req_kwargs)

            if response.status_code in retryable_status and attempt < max_retries:
                # Rispetta Retry-After se presente (cap a 60s)
                retry_after_hdr = response.headers.get("Retry-After", "")
                try:
                    wait = min(60.0, float(retry_after_hdr)) if retry_after_hdr else None
                except ValueError:
                    wait = None
                if wait is None:
                    wait = min(60.0, backoff_factor * (2 ** attempt) + random.uniform(0, 0.5))
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} {method} {url} "
                    f"HTTP {response.status_code} wait={wait:.1f}s"
                )
                await asyncio.sleep(wait)
                continue

            # Success o 4xx definitivo
            return response

        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError,
                httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_exc = e
            if attempt < max_retries:
                wait = backoff_factor * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} {method} {url} "
                    f"network error ({type(e).__name__}) wait={wait:.1f}s"
                )
                await asyncio.sleep(wait)
                continue
            raise

    # Non dovremmo mai arrivare qui, ma per completezza
    if last_exc:
        raise last_exc
    # Se arriviamo qui, tutti i retry hanno dato 5xx senza sollevare
    return response  # type: ignore[return-value]
