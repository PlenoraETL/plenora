"""Outbound URL validation and DNS-pinning primitives."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

from app.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedOutboundTarget:
    original_url: str
    pinned_url: str
    hostname: str
    host_header: str


def _network_opt_ins(allow_loopback_opt_in: bool) -> tuple[bool, bool]:
    env_name = os.environ.get(
        "ENVIRONMENT", os.environ.get("ENV", "development")
    ).lower()
    prod_like = env_name in ("production", "prod", "staging", "stage")
    allow_loopback = (
        allow_loopback_opt_in
        and not prod_like
        and os.environ.get("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "").strip() == "1"
    )
    allow_private = (
        not prod_like
        and os.environ.get("PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND", "").strip()
        == "1"
    )
    return allow_loopback, allow_private


def _check_and_resolve(
    url: str,
    *,
    allow_loopback_opt_in: bool,
) -> tuple[Optional[str], list[str]]:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return "URL non valido: solo schemi http/https sono permessi", []
    host = parsed.hostname
    if not host:
        return "URL non valido: hostname mancante", []
    if parsed.username is not None or parsed.password is not None:
        return "URL non valido: credenziali inline non consentite", []

    # RFC-reserved test domains intentionally do not require live DNS.
    if host == "example.com" or host.endswith(".example.com"):
        return None, []

    try:
        infos = socket.getaddrinfo(host, parsed.port)
    except socket.gaierror:
        return f"Host non risolvibile: {host}", []

    allow_loopback, allow_private = _network_opt_ins(allow_loopback_opt_in)
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        return f"Host non risolvibile: {host}", []

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"URL bloccato: indirizzo risolto non valido ({address})", []
        if ip.is_loopback:
            if allow_loopback:
                continue
            return (
                f"URL bloccato: l'host risolve a un IP privato/interno ({address})",
                [],
            )
        if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return (
                f"URL bloccato: l'host risolve a un IP privato/interno ({address})",
                [],
            )
        if ip.is_private:
            if allow_private:
                continue
            return (
                f"URL bloccato: l'host risolve a un IP privato/interno ({address})",
                [],
            )
    return None, addresses


def check_outbound_address(
    url: str, allow_loopback_opt_in: bool = True
) -> Optional[str]:
    """Return a rejection reason, or None when the target is allowed."""
    reason, _addresses = _check_and_resolve(
        url,
        allow_loopback_opt_in=allow_loopback_opt_in,
    )
    return reason


def validate_external_url(url: str) -> None:
    reason = check_outbound_address(url, allow_loopback_opt_in=True)
    if reason is not None:
        raise ValueError(reason)


def resolve_external_url(url: str) -> ResolvedOutboundTarget:
    """Resolve and validate once, returning the exact IP to connect to.

    The caller must use ``pinned_url`` for the socket connection while
    preserving ``hostname`` for HTTP Host, TLS SNI and certificate checks.
    This closes the validation/connect DNS-rebinding window.
    """
    reason, addresses = _check_and_resolve(url, allow_loopback_opt_in=True)
    if reason is not None:
        raise ValueError(reason)

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not addresses:
        return ResolvedOutboundTarget(url, url, hostname, parsed.netloc)

    address = addresses[0]
    ip_literal = f"[{address}]" if ":" in address else address
    pinned_netloc = ip_literal if parsed.port is None else f"{ip_literal}:{parsed.port}"
    default_port = 443 if parsed.scheme == "https" else 80
    host_header = (
        hostname
        if parsed.port in (None, default_port)
        else f"{hostname}:{parsed.port}"
    )
    return ResolvedOutboundTarget(
        original_url=url,
        pinned_url=urlunparse(parsed._replace(netloc=pinned_netloc)),
        hostname=hostname,
        host_header=host_header,
    )


__all__ = [
    "ResolvedOutboundTarget",
    "check_outbound_address",
    "resolve_external_url",
    "validate_external_url",
]
