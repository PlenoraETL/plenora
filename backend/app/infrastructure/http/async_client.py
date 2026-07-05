"""
app/infrastructure/http/async_client.py

HTTP client async condiviso basato su httpx.AsyncClient.
"""

import asyncio
from typing import Optional

import httpx

from app.utils.logger import get_logger

logger = get_logger(__name__)


# Configurazione

def _get_pool_size() -> int:
    """Legge pool size da Settings, con fallback e clamping di sicurezza.

    Override env continua a funzionare via Settings (`int` coercion).
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


# Singleton AsyncClient

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


