"""Compatibility facade for the shared outbound URL security policy."""

from __future__ import annotations

from typing import Optional

from app.shared.security import outbound_url as _shared


# Compatibility attributes for diagnostics/tests using the historical path.
socket = _shared.socket
logger = _shared.logger
ResolvedOutboundTarget = _shared.ResolvedOutboundTarget


def check_outbound_address(
    url: str, allow_loopback_opt_in: bool = True
) -> Optional[str]:
    return _shared.check_outbound_address(
        url,
        allow_loopback_opt_in=allow_loopback_opt_in,
    )


def validate_external_url(url: str) -> None:
    _shared.validate_external_url(url)


def resolve_external_url(url: str) -> ResolvedOutboundTarget:
    return _shared.resolve_external_url(url)


__all__ = [
    "ResolvedOutboundTarget",
    "check_outbound_address",
    "resolve_external_url",
    "validate_external_url",
]
