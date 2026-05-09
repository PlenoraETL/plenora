"""
app/shared/api_utils.py

Logica HTTP/API condivisa tra api_connector.py e enrichment_service.py.
"""

import functools
import threading
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter

from app.shared.connections import oauth as _oauth
from app.shared.connections.oauth import (
    get_arcgis_token,
    get_cached_oauth2_token,
    invalidate_oauth2_token,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "get_shared_session",
    "get_arcgis_token",
    "get_cached_oauth2_token",
    "invalidate_oauth2_token",
    "prepare_headers",
    "validate_external_url",
    "validate_external_url_silent",
]


_shared_session: Optional[requests.Session] = None
_shared_session_lock = threading.Lock()


def get_shared_session() -> requests.Session:
    """Ritorna una Session requests condivisa con connection pooling."""
    global _shared_session
    if _shared_session is None:
        with _shared_session_lock:
            if _shared_session is None:
                sess = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=50,
                    max_retries=0,
                    pool_block=False,
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                _shared_session = sess
    return _shared_session


@functools.lru_cache(maxsize=1)
def _warn_urby_loopback_alias_once() -> None:
    """Emit the deprecated URBY loopback env alias warning once per process."""
    import warnings as _warnings

    msg = (
        "[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND e' un alias retro-compat. "
        "Migrare a PLENORA_ALLOW_LOOPBACK_OUTBOUND. "
        "L'alias verra' rimosso entro 2026-Q4."
    )
    logger.warning(msg)
    _warnings.warn(msg, DeprecationWarning, stacklevel=2)


def _check_outbound_address(
    url: str, allow_loopback_opt_in: bool = True
) -> Optional[str]:
    """Anti-SSRF primitive shared by raising and return-string validators."""
    import ipaddress
    import os
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return "URL non valido: solo schemi http/https sono permessi"
    host = parsed.hostname
    if not host:
        return "URL non valido: hostname mancante"

    if allow_loopback_opt_in:
        env_name = os.environ.get(
            "ENVIRONMENT", os.environ.get("ENV", "development")
        ).lower()
        if env_name in ("production", "prod", "staging", "stage"):
            allow_loopback = False
        else:
            plenora_set = (
                os.environ.get("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "").strip()
                == "1"
            )
            urby_set = (
                os.environ.get("URBY_ALLOW_LOOPBACK_OUTBOUND", "").strip()
                == "1"
            )
            allow_loopback = plenora_set or urby_set
            if urby_set and not plenora_set:
                _warn_urby_loopback_alias_once()
    else:
        allow_loopback = False

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"Host non risolvibile: {host}"

    addr_list = list({info[4][0] for info in infos})
    for addr in addr_list:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if allow_loopback and ip.is_loopback:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"URL bloccato: l'host risolve a un IP privato/interno ({addr})"
    return None


def validate_external_url(url: str) -> None:
    """Anti-SSRF: valida che l'URL sia http(s) verso un host pubblico."""
    motivo = _check_outbound_address(url, allow_loopback_opt_in=True)
    if motivo is not None:
        raise ValueError(motivo)


def validate_external_url_silent(url: str) -> Optional[str]:
    """Return-string variant of validate_external_url."""
    return _check_outbound_address(url, allow_loopback_opt_in=True)


def prepare_headers(auth: Dict, custom_headers: Dict = None) -> Dict:
    """Prepara headers HTTP per chiamata API REST."""
    return _oauth.prepare_headers(
        auth,
        custom_headers,
        oauth2_token_getter=get_cached_oauth2_token,
    )
