"""Outbound URL validation primitives.

This module owns the anti-SSRF checks used before server-side HTTP calls.
"""

from __future__ import annotations

import warnings as _warnings

import functools
import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urlparse

from app.utils.logger import get_logger

logger = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _warn_urby_loopback_alias_once() -> None:
    """Emit the deprecated URBY loopback env alias warning once per process."""

    msg = (
        "[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND e' un alias retro-compat. "
        "Migrare a PLENORA_ALLOW_LOOPBACK_OUTBOUND. "
        "L'alias verra' rimosso entro 2026-Q4."
    )
    logger.warning(msg)
    _warnings.warn(msg, DeprecationWarning, stacklevel=2)


def check_outbound_address(
    url: str, allow_loopback_opt_in: bool = True
) -> Optional[str]:
    """Anti-SSRF primitive shared by raising and return-string validators."""
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
    """Validate that an outbound URL is HTTP(S) and resolves publicly."""
    reason = check_outbound_address(url, allow_loopback_opt_in=True)
    if reason is not None:
        raise ValueError(reason)

