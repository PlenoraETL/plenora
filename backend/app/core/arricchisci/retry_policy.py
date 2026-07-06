"""Retry policy helpers shared by Arricchisci REST call paths."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional


RETRY_AFTER_FALLBACK_SECONDS = 60
"""Default used when a 429 response does not include Retry-After."""


def clamp_retry_after(raw_header: Optional[str], *, max_seconds: int) -> int:
    """Parse and clamp an HTTP Retry-After delta-seconds or HTTP-date value."""

    if max_seconds < 0:
        max_seconds = 0
    if raw_header is None:
        candidate = RETRY_AFTER_FALLBACK_SECONDS
    else:
        try:
            candidate = int(str(raw_header).strip())
        except (TypeError, ValueError):
            candidate = _retry_after_http_date_seconds(raw_header)
            if candidate is None:
                candidate = RETRY_AFTER_FALLBACK_SECONDS
    if candidate < 0:
        candidate = 0
    return min(candidate, max_seconds)


def _retry_after_http_date_seconds(raw_header: Optional[str]) -> int | None:
    if raw_header is None:
        return None
    try:
        retry_at = parsedate_to_datetime(str(raw_header).strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(math.ceil(delta)))
