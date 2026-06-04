"""Resilience primitives condivise (classificazione errori transitori, retry)."""

from app.shared.resilience.transient import (
    TRANSIENT_HTTP_STATUSES,
    is_transient,
    is_transient_http_status,
)

__all__ = [
    "TRANSIENT_HTTP_STATUSES",
    "is_transient",
    "is_transient_http_status",
]
