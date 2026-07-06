"""Retry policy helpers shared by Arricchisci REST call paths."""

from __future__ import annotations

from typing import Optional


RETRY_AFTER_FALLBACK_SECONDS = 60
"""Default used when a 429 response does not include Retry-After."""


def clamp_retry_after(raw_header: Optional[str], *, max_seconds: int) -> int:
    """Parse and clamp an HTTP Retry-After delta-seconds value."""

    if max_seconds < 0:
        max_seconds = 0
    if raw_header is None:
        candidate = RETRY_AFTER_FALLBACK_SECONDS
    else:
        try:
            candidate = int(str(raw_header).strip())
        except (TypeError, ValueError):
            candidate = RETRY_AFTER_FALLBACK_SECONDS
    if candidate < 0:
        candidate = 0
    return min(candidate, max_seconds)
